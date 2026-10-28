"""异步任务：排队、在独立进程中执行、进度查询、到期清理。

任务状态保存在内存中，服务重启后未完成的任务会丢失（客户端重新提交即可）。
OCR 模型较大，放在常驻的工作进程里，每个进程只加载一次。
"""

import json
import logging
import multiprocessing
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace

from docparser import config

logger = logging.getLogger(__name__)

QUEUED, RUNNING, SUCCEEDED, FAILED = "queued", "running", "succeeded", "failed"


class QueueFull(Exception):
    pass


@dataclass
class Job:
    id: str
    owner: str
    filename: str
    options: dict
    status: str = QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    result: dict = field(default_factory=dict)
    error: str | None = None

    @property
    def dir(self) -> str:
        return os.path.join(config.DATA_DIR, "jobs", self.id)

    def progress(self) -> dict:
        try:
            with open(os.path.join(self.dir, "progress.json"), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}


def _warmup() -> None:
    from docparser import ocr

    ocr.engine()


def _run(job_dir: str, options: dict) -> dict:
    # 在工作进程中执行，延迟导入：主进程不需要加载 OCR 依赖
    from docparser.pipeline import run

    return run(job_dir, options)


class JobManager:
    def __init__(self):
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # spawn：避免 fork 带上主进程的线程和锁；进程常驻，模型只加载一次
        self._pool = ProcessPoolExecutor(
            max_workers=config.MAX_WORKERS, mp_context=multiprocessing.get_context("spawn")
        )
        # 预先加载模型（每个进程约 20 秒），避免第一个任务等待
        for _ in range(config.MAX_WORKERS):
            self._pool.submit(_warmup)
        shutil.rmtree(os.path.join(config.DATA_DIR, "jobs"), ignore_errors=True)
        os.makedirs(os.path.join(config.DATA_DIR, "jobs"), exist_ok=True)
        threading.Thread(target=self._cleanup_loop, daemon=True).start()

    def submit(self, owner: str, filename: str, data: bytes, options: dict, input_name: str = "input.pdf") -> Job:
        with self._lock:
            pending = sum(1 for j in self._jobs.values() if j.status in (QUEUED, RUNNING))
            if pending >= config.MAX_QUEUE + config.MAX_WORKERS:
                raise QueueFull()
            job = Job(id=uuid.uuid4().hex, owner=owner, filename=filename, options=options)
            self._jobs[job.id] = job
        os.makedirs(job.dir)
        with open(os.path.join(job.dir, input_name), "wb") as f:
            f.write(data)
        future = self._pool.submit(_run, job.dir, {**options, "filename": filename})
        future.add_done_callback(lambda fut, job=job: self._done(job, fut))
        # 进程池不提供「开始执行」的回调，由 pipeline 写入 progress.json 后再标记为 running
        with self._lock:
            return replace(job)

    def _done(self, job: Job, future) -> None:
        try:
            result, status, error = future.result(), SUCCEEDED, None
        except Exception as e:  # noqa: BLE001 — 任何失败都要反映到任务状态里
            logger.exception("job %s failed", job.id)
            result, status, error = {}, FAILED, f"{type(e).__name__}: {e}"
        with self._lock:
            job.finished_at, job.result, job.status, job.error = time.time(), result, status, error

    def get(self, job_id: str, owner: str) -> Job | None:
        """返回任务的快照：_done 在另一个线程里写入，直接读任务可能读到写了一半的状态。"""
        job = self._jobs.get(job_id)
        if not job or job.owner != owner:
            return None
        progress = job.progress() if job.status == QUEUED else {}
        with self._lock:
            # 读 progress.json 期间任务可能已经结束，不能把结束状态改回 running
            if progress and job.status == QUEUED:
                job.status, job.started_at = RUNNING, progress.get("started_at")
            return replace(job)

    def delete(self, job_id: str, owner: str) -> bool:
        job = self.get(job_id, owner)
        if not job or job.status in (QUEUED, RUNNING):
            return False
        with self._lock:
            self._jobs.pop(job_id, None)
        shutil.rmtree(job.dir, ignore_errors=True)
        return True

    def stats(self) -> dict:
        jobs = list(self._jobs.values())
        return {s: sum(1 for j in jobs if j.status == s) for s in (QUEUED, RUNNING, SUCCEEDED, FAILED)}

    def _cleanup_loop(self) -> None:
        while True:
            time.sleep(60)
            deadline = time.time() - config.JOB_TTL_MINUTES * 60
            for job in list(self._jobs.values()):
                if job.finished_at and job.finished_at < deadline:
                    with self._lock:
                        self._jobs.pop(job.id, None)
                    shutil.rmtree(job.dir, ignore_errors=True)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
