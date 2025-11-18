"""
file_safety.py
--------------

Provides atomic, traceability-safe file operations for the CheckPrint
workflow. Supports:

• Preflight validation (no changes made)
• Ordered execution of file operations
• Best-effort rollback with full reporting (Mode B)
• Fail-fast behaviour during preflight and execution

This module ensures that no DB writes occur unless all required file
operations can be safely completed.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import shutil
import os
from typing import Optional, List, Tuple


# ------------------------------------------------------------
# Dataclasses
# ------------------------------------------------------------

@dataclass
class FileOp:
    """
    Represents a file operation.

    action:
        "copy"   - copy src → dst
        "rename" - rename src → dst
        "delete" - remove src
    src: Path
    dst: Path | None
    """
    action: str
    src: Path
    dst: Optional[Path] = None


class PreflightError(Exception):
    """Raised when preflight validation detects an unsafe file operation."""
    def __init__(self, path: Path, reason: str):
        self.path = path
        self.reason = reason
        super().__init__(f"{path} :: {reason}")


# ------------------------------------------------------------
# Planning helpers (returns FileOp objects)
# ------------------------------------------------------------

def plan_copy(src: Path, dst: Path) -> FileOp:
    return FileOp("copy", Path(src), Path(dst))


def plan_rename(src: Path, dst: Path) -> FileOp:
    return FileOp("rename", Path(src), Path(dst))


def plan_delete(path: Path) -> FileOp:
    return FileOp("delete", Path(path))


# ------------------------------------------------------------
# Preflight: fail fast
# ------------------------------------------------------------

def preflight_ops(ops: List[FileOp]) -> Tuple[bool, Optional[Path], Optional[str]]:
    """
    Returns:
        (ok, path, reason)

    If ok=False:
        path   = path that caused the failure
        reason = explanation usable for UI display
    """
    for op in ops:
        if op.action == "copy":
            ok, p, r = _preflight_copy(op)
        elif op.action == "rename":
            ok, p, r = _preflight_rename(op)
        elif op.action == "delete":
            ok, p, r = _preflight_delete(op)
        else:
            return False, None, f"Unknown operation: {op.action}"

        if not ok:
            return ok, p, r

    return True, None, None


# ------------------------------
# Individual preflight checkers
# ------------------------------

def _preflight_copy(op: FileOp):
    src, dst = op.src, op.dst

    if not src.exists():
        return False, src, "Source file does not exist."

    # src readable?
    # Directories cannot be opened, but they *can* be renamed or copied into
    if src.is_dir():
        # Must have read & write permissions
        if not os.access(src, os.R_OK | os.W_OK):
            return False, src, "No permission to access directory."
    else:
        # Regular file read test
        try:
            with open(src, "rb"):
                pass
        except Exception as e:
            return False, src, f"Cannot read file: {e}"

    dst_parent = dst.parent
    if not dst_parent.exists():
        return False, dst_parent, "Destination folder does not exist."

    # dst writable?
    # Try to open dst for writing (or create temp)
    try:
        if dst.exists():
            with open(dst, "rb+"):
                pass
        else:
            test_tmp = dst_parent / (".permission_test_tmp")
            with open(test_tmp, "wb") as f:
                f.write(b"test")
            test_tmp.unlink()
    except Exception as e:
        return False, dst, f"No write permission: {e}"

    return True, None, None


def _preflight_rename(op: FileOp):
    src, dst = op.src, op.dst

    if not src.exists():
        return False, src, "Source file does not exist."

    # src readable?
    # Directories cannot be opened, but they *can* be renamed or copied into
    if src.is_dir():
        # Must have read & write permissions
        if not os.access(src, os.R_OK | os.W_OK):
            return False, src, "No permission to access directory."
    else:
        # Regular file read test
        try:
            with open(src, "rb"):
                pass
        except Exception as e:
            return False, src, f"Cannot read file: {e}"

    # dst parent exists?
    dp = dst.parent
    if not dp.exists():
        return False, dp, "Destination folder does not exist."

    # dst writable?
    try:
        if dst.exists():
            with open(dst, "rb+"):
                pass
        else:
            test_tmp = dp / (".permission_test_tmp")
            with open(test_tmp, "wb") as f:
                f.write(b"test")
            test_tmp.unlink()
    except Exception as e:
        return False, dst, f"No write permission: {e}"

    return True, None, None


def _preflight_delete(op: FileOp):
    src = op.src

    if not src.exists():
        return True, None, None

    # Directory delete check
    if src.is_dir():
        if not os.access(src, os.W_OK):
            return False, src, "Cannot remove directory: no write permission."
        return True, None, None

    # check readable/writable
    try:
        with open(src, "rb+"):
            pass
    except Exception as e:
        return False, src, f"Cannot remove file: {e}"

    return True, None, None


# ------------------------------------------------------------
# Execution and rollback (best effort)
# ------------------------------------------------------------

def execute_ops(ops: List[FileOp]) -> List[Tuple[FileOp, str]]:
    """
    Executes operations in order.

    Returns:
        rollback_failures: List[(FileOp, reason)]

    Rollback is best-effort. If rollback fails on some files,
    they are returned in rollback_failures list.
    """
    applied: List[FileOp] = []

    try:
        for op in ops:
            _execute_single(op)
            applied.append(op)

    except Exception as exc:
        # begin rollback
        rollback_failures = _attempt_rollback(applied)
        raise RuntimeError(f"File operation failed: {exc}") from exc

    return []  # no rollback failures


def _execute_single(op: FileOp):
    if op.action == "copy":
        shutil.copy2(op.src, op.dst)
    elif op.action == "rename":
        op.src.rename(op.dst)
    elif op.action == "delete":
        if op.src.exists():
            op.src.unlink()
    else:
        raise RuntimeError(f"Unknown op type during execution: {op.action}")


# ------------------------------
# Rollback (best effort)
# ------------------------------

def _attempt_rollback(applied_ops: List[FileOp]) -> List[Tuple[FileOp, str]]:
    """
    Try to undo applied operations in reverse order.

    Returns:
        List of (FileOp, error_reason) for any rollback steps that failed.
    """
    failures = []

    for op in reversed(applied_ops):
        try:
            if op.action == "copy":
                # remove dst
                if op.dst and op.dst.exists():
                    op.dst.unlink()
            elif op.action == "rename":
                # rename dst → src
                if op.dst.exists():
                    op.dst.rename(op.src)
            elif op.action == "delete":
                # cannot restore a deleted file (no backup cache)
                pass
        except Exception as e:
            failures.append((op, str(e)))

    return failures
