"""任务状态：查询时读 progress.json 的间隙任务刚好结束，结束状态不能被改回 running；
查询返回快照，字段前后一致，之后的变更不影响已返回的快照。

用法: python tests/test_jobs.py（只依赖标准库）
"""

import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from docparser import config  # noqa: E402
from docparser.jobs import FAILED, QUEUED, RUNNING, SUCCEEDED, Job, JobManager  # noqa: E402

failed = 0


def check(ok: bool, name: str) -> None:
    global failed
    print(("✅ " if ok else "❌ ") + name)
    failed += not ok


class Future:
    def __init__(self, result=None, error=None):
        self._result, self._error = result, error

    def result(self):
        if self._error:
            raise self._error
        return self._result


def manager() -> JobManager:
    # 不启动进程池和清理线程，只测状态变更；只初始化了 _jobs 和 _lock，JobManager 新增属性时要在这里补上
    m = JobManager.__new__(JobManager)
    m._jobs, m._lock = {}, threading.Lock()
    return m


def finish_while_reading_progress(future: Future) -> Job:
    """get() 读 progress.json 时，_done() 在另一个线程里把任务标记为结束。"""
    m = manager()
    job = Job(id="j1", owner="k", filename="a.pdf", options={})
    m._jobs[job.id] = job

    def progress():
        done = threading.Thread(target=m._done, args=(job, future))
        done.start()
        done.join()
        return {"stage": "ocr", "started_at": 1.0}

    job.progress = progress
    m.get(job.id, "k")
    return m.get(job.id, "k")


config.DATA_DIR = tempfile.mkdtemp()

job = finish_while_reading_progress(Future(result={"markdown": "x"}))
check(job.status == SUCCEEDED, "读进度期间任务完成 → 仍是 succeeded")
check(job.result == {"markdown": "x"} and job.finished_at, "结果和结束时间都保留")

job = finish_while_reading_progress(Future(error=RuntimeError("boom")))
check(job.status == FAILED and job.error == "RuntimeError: boom", "读进度期间任务失败 → 仍是 failed")

m = manager()
job = Job(id="j2", owner="k", filename="a.pdf", options={})
m._jobs[job.id] = job
job.progress = lambda: {}
check(m.get(job.id, "k").status == QUEUED, "还没有进度 → queued")
job.progress = lambda: {"stage": "ocr", "started_at": 2.0}
check(m.get(job.id, "k").status == RUNNING and job.started_at == 2.0, "有进度 → running，记录开始时间")
snapshot = m.get(job.id, "k")
check(snapshot.status == RUNNING, "完成前取得的快照")
m._done(job, Future(result={"files": ["markdown"]}))
check(snapshot.status == RUNNING and snapshot.result == {} and snapshot.finished_at is None, "任务完成后，之前的快照不变")
snapshot = m.get(job.id, "k")
check(snapshot.status == SUCCEEDED and snapshot.result == {"files": ["markdown"]} and snapshot.finished_at,
      "运行中完成 → succeeded，快照里状态、结果、结束时间一致")
check(snapshot is not job and job.status == SUCCEEDED, "返回的是副本，任务本身也已更新")
check(m.get(job.id, "other") is None, "其他 Key 查不到任务")

sys.exit(1 if failed else 0)
