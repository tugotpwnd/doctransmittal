from __future__ import annotations
import sys, traceback, datetime
from PyQt5.QtWidgets import QApplication
from .paths import logs_dir
from .logger import get_logger
from .traceback_dialog import TracebackDialog

_log = get_logger()

def install_excepthook() -> None:
    log_dir = logs_dir()
    def handler(exc_type, exc, tb):
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        (log_dir / f"crash_{ts}.log").write_text("".join(traceback.format_exception(exc_type, exc, tb)), encoding="utf-8")
        app = QApplication.instance()
        _log.exception("Unhandled exception", exc_info=(exc_type, exc, tb))
        if app is None:
            traceback.print_exception(exc_type, exc, tb)
        else:
            TracebackDialog(exc_type, exc, tb).exec_()
    sys.excepthook = handler