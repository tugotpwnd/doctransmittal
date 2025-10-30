from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from .paths import logs_dir

_logger: logging.Logger | None = None

def get_logger(name: str = "docutrans") -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    log_path: Path = logs_dir() / "app.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name); logger.setLevel(logging.DEBUG)
    fh = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8"); fh.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(); ch.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh.setFormatter(fmt); ch.setFormatter(fmt)
    logger.addHandler(fh); logger.addHandler(ch)
    logger.debug("Logger initialized at %s", log_path)
    _logger = logger
    return logger