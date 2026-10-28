"""服务配置，全部来自环境变量。"""

import hashlib
import os


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name) or default)


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name) or default)


VERSION = "0.1.4"

DATA_DIR = os.environ.get("DATA_DIR", "/data")
# 逗号分隔的多个 Key，便于轮换；每个 Key 只能看到自己提交的任务
API_KEYS = [k.strip() for k in os.environ.get("API_KEYS", "").split(",") if k.strip()]
# 报错和判定报告的默认语言（en / zh）；请求带 Accept-Language 时按请求的语言
DEFAULT_LANGUAGE = "zh" if os.environ.get("DEFAULT_LANGUAGE", "en").lower().startswith("zh") else "en"

MAX_WORKERS = _int("MAX_WORKERS", 2)  # 同时处理的任务数
MAX_QUEUE = _int("MAX_QUEUE", 20)  # 排队任务上限，超过返回 429
MAX_FILE_MB = _int("MAX_FILE_MB", 100)
MAX_PAGES = _int("MAX_PAGES", 300)
JOB_TTL_MINUTES = _int("JOB_TTL_MINUTES", 60)  # 结果保留时间，到期自动删除
OCR_DPI = _int("OCR_DPI", 200)  # 渲染分辨率。样例实测 200 DPI 识别准确，每页 3–7 秒；小字号扫描件可调高
OCR_THREADS = _int("OCR_THREADS", 2)  # 每个任务进程使用的 CPU 线程数（GPU 上不使用）
# 倾斜校正：OCR 前把轻微倾斜的扫描图转正（表格倾斜 0.6° 就会单元格错位）。超过 OCR_DESKEW_MAX 度的不处理
OCR_DESKEW = os.environ.get("OCR_DESKEW", "1") not in ("0", "false", "no")
OCR_DESKEW_MAX = _float("OCR_DESKEW_MAX", 5)

# 推理设备：cpu，或 gpu / gpu:0（需要 GPU 版镜像，见 README「GPU 部署」）
OCR_DEVICE = os.environ.get("OCR_DEVICE", "cpu")
# 模型组合：默认是在 8 核 CPU 上实测选定的（见 ocr.py）。有 GPU 时可以换成精度更高的模型，
# 如版面 PP-DocLayout_plus-L、检测 PP-OCRv5_server_det（构建镜像时按这里的配置预下载）
OCR_MODELS = {
    "layout": os.environ.get("OCR_LAYOUT_MODEL", "PP-DocLayout-M"),
    "det": os.environ.get("OCR_DET_MODEL", "PP-OCRv5_mobile_det"),
    "rec": os.environ.get("OCR_REC_MODEL", "PP-OCRv5_server_rec"),
    "table": os.environ.get("OCR_TABLE_MODEL", "SLANet_plus"),
}

# Office 文档（doc、docx 等）先用 LibreOffice 转成 PDF
LIBREOFFICE = os.environ.get("LIBREOFFICE", "soffice")
CONVERT_TIMEOUT = _int("CONVERT_TIMEOUT", 300)

# 页面判定阈值，见 classify.DEFAULTS
CLASSIFY = {
    key: _float(f"CLASSIFY_{key.upper()}", default)
    for key, default in {
        "min_text_chars": 20,
        "scan_image_coverage": 0.5,
        "min_valid_ratio": 0.9,
        "region_min_coverage": 0.1,
        "outline_min_drawings": 300,
    }.items()
}


def key_owner(api_key: str) -> str:
    """任务归属：用 Key 的哈希标识，不在内存或磁盘中保存 Key 原文。"""
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]
