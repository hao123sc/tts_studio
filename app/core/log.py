"""日志（FR-S-4）：按天滚动，隐私保护——日志不记录全文，只记前 20 字。"""

from __future__ import annotations

import logging
from logging.handlers import TimedRotatingFileHandler

from .config import user_data_dir

_LOGGER_NAME = "tts_studio"
_configured = False


def short(text: str, n: int = 20) -> str:
    """截取文本前 n 字用于日志（隐私要求：不记全文）。"""
    t = (text or "").replace("\n", " ").strip()
    return t[:n] + ("…" if len(t) > n else "")


def get_logger() -> logging.Logger:
    """获取全局 logger（首次调用时初始化按天滚动的文件处理器）。"""
    global _configured
    logger = logging.getLogger(_LOGGER_NAME)
    if not _configured:
        logger.setLevel(logging.INFO)
        log_dir = user_data_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            log_dir / "app.log", when="midnight", backupCount=30, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(console)
        _configured = True
    return logger


def set_level(level: str) -> None:
    """设置日志级别。"""
    get_logger().setLevel(getattr(logging, level.upper(), logging.INFO))
