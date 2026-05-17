"""Logger condiviso (loguru)."""
from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from .config import project_path

_initialized = False


def setup_logger(level: str = "INFO") -> None:
    global _initialized
    if _initialized:
        return
    logger.remove()
    logger.add(sys.stderr, level=level, format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
        "<level>{level: <8}</level> "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:{line} - "
        "<level>{message}</level>"
    ))
    log_dir = project_path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "andreatrading.log",
        level="DEBUG",
        rotation="10 MB",
        retention=10,
        compression="gz",
    )
    _initialized = True


def get_logger():
    setup_logger()
    return logger
