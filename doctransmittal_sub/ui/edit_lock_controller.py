from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable

from PyQt5.QtCore import QObject, QTimer
from PyQt5.QtWidgets import (
    QAction,
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QToolButton,
    QVBoxLayout,
)

from ..services import edit_lock_service as locks


_DANGER_WORDS = (
    "delete", "apply", "proceed", "build", "submit", "save", "overwrite", "rename",
    "new", "edit", "cancel", "complete", "archive", "accept", "reject", "approve",
    "resubmit", "remove", "add", "import", "migrate", "increment", "decrement",
    "set", "purge", "restore", "project settings", "templates", "remap",
)

_SAFE_WORDS = (
    "print", "refresh", "open", "view", "back", "close", "browse", "select",
    "clear", "show", "load", "unload", "copy", "history", "request editing",
    "release editing", "take over", "acquire editing",
)


class EditLockController(QObject):
    """Owns edit-lock UI state, heartbeat, handover and read-only enforcement."""

    def __init__(self, main_window):
        super().__init__(main_window)
        self.mw = main_window
        self.db_path: Path | None = None
        self.owner_token = ""
        self.requester_token = ""
        self.handover_requested = False
        self._last_request_token_seen = ""
        self._last_request_response_seen = ""
        self._last_prompted_db = ""
        self._read_only = True

        self.heartbeat_timer = QTimer(self)
        self.heartbeat_timer.setInterval(15_000)
        self.heartbeat_timer.timeout.connect(self._heartbeat_tick)

        self.monitor_timer = QTimer(self)
        self.monitor_timer.setInterval(15_000)
        self.monitor_timer.timeout.connect(self._monitor_tick)

        self._connect_sidebar()
        self._apply_read_only_ui(True, "No project database open.")
        self._update_sidebar({"mode": "none", "detail": "No project database open."})

    # ------------------------------------------------------------------
    # Public API called by MainWindow
    # ------------------------------------------------------------------

    def activate_for_db(self, db_path: Path | str) -> None:
        db_path = Path(db_path).expanduser().resolve()
        if self.db_path and self.db_path != db_path:
            self.release_current(silent=True, reason="switch_database")

        # If MainWindow pre-activated this DB before RegisterTab finished loading,
        # projectInfoReady may call activate_for_db() again. Keep the existing lock.
        if self.db_path == db_path and self.owner_token:
            try:
                status = locks.get_lock_status(db_path)
                if status.get("owner_token") == self.owner_token:
                    locks.set_lock_context(db_path, self.owner_token, read_only=False)
                    self._apply_read_only_ui(False, "Editing enabled.")
                    self._update_sidebar({"mode": "edit", "detail": "You own editing access.", "status": status})
                    return
            except Exception:
                pass

        self.db_path = db_path
        self.owner_token = ""
        self.requester_token = ""
        self.handover_requested = False
        locks.set_lock_context(db_path, "", read_only=True)

        try:
            status = locks.get_lock_status(db_path)
        except Exception as exc:
            self._switch_to_read_only(f"Could not inspect edit lock: {exc}", status={})
            return

        if status.get("locked"):
            # If the lock belongs to the same configured user on the same PC,
            # and the previous local process no longer appears to be running,
            # reclaim it automatically. This handles unclean exits / missed SharePoint
            # cleanup without waiting for the 15 minute stale timeout.
            if locks.same_identity_can_reclaim(status, owner_name=self._user_name()):
                self._acquire_editing(force=True, active_force=True, clear_requests=True, reason="same_identity_reclaim")
                return

            same_identity = False
            try:
                same_identity = locks.identity_matches_lock(status, owner_name=self._user_name())
            except Exception:
                same_identity = False
            if not self._last_prompted_db == str(db_path):
                self._last_prompted_db = str(db_path)
                extra = ""
                if same_identity:
                    extra = "\n\nThis lock appears to be from the same user on this PC, but the previous process may still be running. Use Force Takeover only if you are sure no other active instance is editing."
                QMessageBox.information(
                    self.mw,
                    "Register opened read-only",
                    "This register is currently locked for editing by:\n\n"
                    f"{status.get('owner_name') or 'Unknown'} on {status.get('machine_name') or 'unknown machine'}\n\n"
                    "You may view the register, print reports and inspect history, but edits are disabled.\n"
                    "Use the Edit Lock panel on the left to request access."
                    + extra,
                )
            self._switch_to_read_only("Locked by another user.", status=status)
            self.monitor_timer.start()
            return

        if status.get("is_stale"):
            resp = QMessageBox.question(
                self.mw,
                "Stale edit lock detected",
                "The previous edit lock appears stale.\n\n"
                f"Previous owner: {status.get('owner_name') or 'Unknown'} on {status.get('machine_name') or 'unknown machine'}\n"
                f"Last heartbeat: {status.get('heartbeat_utc') or '-'}\n\n"
                "Take over editing now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if resp != QMessageBox.Yes:
                self._switch_to_read_only("Stale lock not taken over.", status=status)
                self.monitor_timer.start()
                return

        self._acquire_editing(force=bool(status.get("is_stale")))

    def release_current(self, silent: bool = False, reason: str = "release") -> None:
        if self.db_path and self.owner_token:
            try:
                locks.release_lock(self.db_path, self.owner_token, reason=reason)
            except Exception as exc:
                if not silent:
                    QMessageBox.warning(self.mw, "Edit Lock", f"Could not release edit lock:\n{exc}")
        if self.db_path:
            locks.set_lock_context(self.db_path, "", read_only=True)
        self.owner_token = ""
        self.heartbeat_timer.stop()
        if not silent and self.db_path:
            self._switch_to_read_only("Editing released.", status=locks.get_lock_status(self.db_path))

    def close(self) -> None:
        self.release_current(silent=True, reason="application_closed")
        if self.db_path:
            locks.clear_lock_context(self.db_path)

    # ------------------------------------------------------------------
    # Sidebar actions
    # ------------------------------------------------------------------

    def _connect_sidebar(self) -> None:
        sidebar = getattr(self.mw, "sidebar", None)
        if not sidebar:
            return
        pairs = (
            ("editLockRequestAccessRequested", self.request_access),
            ("editLockReleaseAccessRequested", self.release_access_clicked),
            ("editLockTakeoverRequested", self.takeover_clicked),
            ("editLockForceTakeoverRequested", self.force_takeover_clicked),
            ("editLockHistoryRequested", self.show_history),
        )
        for signal_name, slot in pairs:
            sig = getattr(sidebar, signal_name, None)
            if sig is not None:
                try:
                    sig.connect(slot)
                except Exception:
                    pass

    def request_access(self) -> None:
        if not self.db_path:
            QMessageBox.information(self.mw, "Edit Lock", "Open a project database first.")
            return
        status = locks.get_lock_status(self.db_path)
        if not status.get("locked"):
            self._acquire_editing(force=bool(status.get("is_stale")))
            return
        if self.owner_token and status.get("owner_token") == self.owner_token:
            QMessageBox.information(self.mw, "Edit Lock", "You already own editing access.")
            return
        name = self._user_name()
        try:
            result = locks.request_handover(self.db_path, requester_name=name, requester_token=self.requester_token)
        except Exception as exc:
            QMessageBox.warning(self.mw, "Edit Lock", f"Could not request editing access:\n{exc}")
            return
        self.requester_token = result.get("requester_token") or self.requester_token
        self.handover_requested = True
        self._last_request_response_seen = ""
        self._switch_to_read_only("Editing access requested.", status=result.get("status") or status)
        QMessageBox.information(
            self.mw,
            "Access requested",
            "Editing access has been requested.\n\n"
            "A separate request file has been created in the DB-Lock folder.\n\nIf the current editor releases the lock, this session will attempt to acquire it automatically."
        )

    def release_access_clicked(self) -> None:
        if not self.owner_token:
            QMessageBox.information(self.mw, "Edit Lock", "This session does not own editing access.")
            return
        resp = QMessageBox.question(
            self.mw,
            "Release editing access?",
            "Release editing access and switch this session to read-only mode?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp == QMessageBox.Yes:
            self.release_current(silent=False, reason="manual_release")

    def takeover_clicked(self) -> None:
        if not self.db_path:
            return
        status = locks.get_lock_status(self.db_path)
        if status.get("locked") and not status.get("is_stale"):
            QMessageBox.information(
                self.mw,
                "Edit Lock",
                "The current lock is not stale. Request access, or use Force Takeover only for operational edge cases."
            )
            return
        self._acquire_editing(force=True, reason="stale_takeover")

    def force_takeover_clicked(self) -> None:
        if not self.db_path:
            QMessageBox.information(self.mw, "Edit Lock", "Open a project database first.")
            return
        status = locks.get_lock_status(self.db_path)
        owner = status.get("owner_name") or "Unknown"
        machine = status.get("machine_name") or "unknown machine"
        locked = bool(status.get("locked") or status.get("is_stale"))
        if not locked:
            self._acquire_editing(force=True, reason="force_takeover_available")
            return
        resp = QMessageBox.warning(
            self.mw,
            "Force takeover edit lock?",
            "Force takeover should only be used when the existing lock is known to be abandoned or operationally blocking.\n\n"
            f"Current recorded owner: {owner} on {machine}\n\n"
            "This will clear the old lock files/request files and assign editing access to this session.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        self._acquire_editing(force=True, active_force=True, clear_requests=True, reason="force_takeover")

    def show_history(self) -> None:
        if not self.db_path:
            QMessageBox.information(self.mw, "Edit Lock History", "Open a project database first.")
            return
        try:
            rows = locks.get_lock_events(self.db_path, limit=120)
        except Exception as exc:
            QMessageBox.warning(self.mw, "Edit Lock History", f"Could not load lock history:\n{exc}")
            return
        lines = []
        for r in rows:
            lines.append(
                f"{r.get('happened_utc') or '-'} | {r.get('event') or '-'} | "
                f"{r.get('actor_name') or '-'} @ {r.get('machine_name') or '-'} | {r.get('details') or ''}"
            )
        text = "\n".join(lines) if lines else "No edit-lock events recorded yet."
        dlg = QDialog(self.mw)
        dlg.setWindowTitle("Edit Lock History")
        lay = QVBoxLayout(dlg)
        edit = QPlainTextEdit(dlg)
        edit.setReadOnly(True)
        edit.setPlainText(text)
        lay.addWidget(edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, dlg)
        buttons.rejected.connect(dlg.reject)
        buttons.accepted.connect(dlg.accept)
        lay.addWidget(buttons)
        dlg.resize(900, 500)
        dlg.exec_()

    # ------------------------------------------------------------------
    # Lock operations
    # ------------------------------------------------------------------

    def _acquire_editing(
        self,
        force: bool = False,
        active_force: bool = False,
        clear_requests: bool = False,
        reason: str = "acquired",
    ) -> None:
        if not self.db_path:
            return
        try:
            result = locks.try_acquire_lock(
                self.db_path,
                owner_name=self._user_name(),
                force=force,
                reason=reason,
                allow_active_takeover=active_force,
                clear_requests=clear_requests,
            )
        except Exception as exc:
            self._switch_to_read_only(f"Could not acquire edit lock: {exc}", status={})
            return
        if not result.get("acquired"):
            self._switch_to_read_only("Editing is locked by another user.", status=result.get("status") or {})
            return
        self.owner_token = result.get("owner_token") or ""
        self.requester_token = ""
        self.handover_requested = False
        self._last_request_response_seen = ""
        locks.set_lock_context(self.db_path, self.owner_token, read_only=False)
        self.heartbeat_timer.start()
        self.monitor_timer.start()
        self._read_only = False
        self._apply_read_only_ui(False, "Editing enabled.")
        status = result.get("status") or locks.get_lock_status(self.db_path)
        self._update_sidebar({"mode": "edit", "detail": "You own editing access.", "status": status})
        try:
            self.mw.statusBar().showMessage("EDIT MODE - this session owns the register edit lock.", 8000)
        except Exception:
            pass

    def _switch_to_read_only(self, detail: str, status: Dict[str, Any] | None = None) -> None:
        if self.db_path:
            locks.set_lock_context(self.db_path, "", read_only=True)
        self.owner_token = ""
        self.heartbeat_timer.stop()
        self._read_only = True
        self._apply_read_only_ui(True, detail)
        self._update_sidebar({"mode": "read_only", "detail": detail, "status": status or {}})
        try:
            self.mw.statusBar().showMessage(f"READ ONLY - {detail}", 10000)
        except Exception:
            pass

    def _heartbeat_tick(self) -> None:
        if not (self.db_path and self.owner_token):
            return
        try:
            status = locks.refresh_heartbeat(self.db_path, self.owner_token)
        except Exception as exc:
            QMessageBox.warning(
                self.mw,
                "Edit lock lost",
                f"This session no longer safely owns the edit lock.\n\n{exc}\n\n"
                "The application has been switched to read-only mode."
            )
            self._switch_to_read_only("Edit lock lost.", status={})
            return
        self._handle_pending_request(status)
        self._update_sidebar({"mode": "edit", "detail": "You own editing access.", "status": status})

    def _monitor_tick(self) -> None:
        if not self.db_path:
            return
        try:
            status = locks.get_lock_status(self.db_path)
        except Exception:
            return

        if self.owner_token:
            if status.get("conflict") or (status.get("locked") and status.get("owner_token") != self.owner_token):
                QMessageBox.warning(
                    self.mw,
                    "Edit lock conflict",
                    "Another active edit owner was detected. This session has been switched to read-only mode."
                )
                self._switch_to_read_only("Another owner was detected.", status=status)
                return
            self._handle_pending_request(status)
            self._update_sidebar({"mode": "edit", "detail": "You own editing access.", "status": status})
            return

        # Read-only monitoring. Requesters only write their own .request JSON file.
        # The owner responds by updating that request file, while the heartbeat lock file remains owner-only.
        if self.handover_requested:
            self._handle_request_response(status)
            if not status.get("locked"):
                self._acquire_editing(force=bool(status.get("is_stale")))
                return
        self._update_sidebar({"mode": "read_only", "detail": "Read-only mode.", "status": status})

    def _handle_request_response(self, status: Dict[str, Any]) -> None:
        if not (self.db_path and self.requester_token):
            return
        try:
            req = locks.get_handover_request_status(self.db_path, self.requester_token)
        except Exception:
            return
        state = (req.get("state") or "").lower()
        if not state or state == "requested":
            return
        response_key = f"{self.requester_token}:{state}:{req.get('responded_utc') or ''}"
        if response_key == self._last_request_response_seen:
            return
        self._last_request_response_seen = response_key

        if state in {"declined", "rejected"}:
            responder = req.get("responded_by_name") or "Current editor"
            QMessageBox.information(
                self.mw,
                "Editing access declined",
                f"{responder} declined the editing access request.\n\n"
                "This session will remain read-only."
            )
            self.handover_requested = False
            self.requester_token = ""
            self._switch_to_read_only("Editing access request declined.", status=status)
            return

        if state in {"released", "accepted", "cleared"}:
            # The owner has explicitly acknowledged/released via this request file.
            # In SharePoint, the request response can sync before the old lock JSON/DB
            # clear operation is visible on the requester's machine. Treat the response
            # as the handover authority and acquire with active takeover enabled, but
            # only if we are not about to override an unrelated new owner.
            target_owner = (req.get("target_owner_token") or "").strip()
            current_owner = (status.get("owner_token") or "").strip()
            if current_owner and target_owner and current_owner != target_owner:
                QMessageBox.warning(
                    self.mw,
                    "Editing access not acquired",
                    "The handover was acknowledged, but another edit owner now appears to hold the lock.\n\n"
                    "This session will remain read-only. Use Force Takeover only if you are certain this is a stale/incorrect lock."
                )
                self.handover_requested = False
                self.requester_token = ""
                self._switch_to_read_only("Another owner appeared after handover.", status=status)
                return

            self._update_sidebar({
                "mode": "read_only",
                "detail": "Handover acknowledged; acquiring editing access...",
                "status": status,
            })
            self._acquire_editing(
                force=True,
                active_force=True,
                clear_requests=False,
                reason="handover_acquire",
            )
            if self.owner_token:
                try:
                    QMessageBox.information(
                        self.mw,
                        "Editing access acquired",
                        "The previous editor released the lock. This session now owns editing access."
                    )
                except Exception:
                    pass
            return

    def _handle_pending_request(self, status: Dict[str, Any]) -> None:
        if not self.owner_token:
            return
        req_token = status.get("requested_by_token") or ""
        if not req_token or req_token == self.owner_token or req_token == self._last_request_token_seen:
            return
        self._last_request_token_seen = req_token
        requester = status.get("requested_by_name") or "Another user"
        req_machine = status.get("requested_by_machine") or "unknown machine"
        resp = QMessageBox.question(
            self.mw,
            "Editing access requested",
            f"{requester} on {req_machine} is requesting editing access.\n\n"
            "Release the edit lock now?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp == QMessageBox.Yes:
            self.release_current(silent=False, reason="handover_release")
        else:
            try:
                locks.clear_handover_request(self.db_path, self.owner_token, event="handover_declined")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # UI helpers
    # ------------------------------------------------------------------

    def _user_name(self) -> str:
        try:
            return (self.mw.settings.get("user.name", "") or "").strip() or locks.default_owner_name()
        except Exception:
            return locks.default_owner_name()

    def _update_sidebar(self, payload: Dict[str, Any]) -> None:
        sidebar = getattr(self.mw, "sidebar", None)
        if not sidebar or not hasattr(sidebar, "set_edit_lock_status"):
            return
        mode = payload.get("mode") or "none"
        status = payload.get("status") or {}
        detail = payload.get("detail") or ""
        if mode == "edit":
            data = {
                "mode": "edit",
                "owner": status.get("owner_name") or self._user_name(),
                "machine": status.get("machine_name") or "",
                "detail": detail,
                "can_request": False,
                "can_release": True,
                "can_takeover": False,
            }
            if status.get("requested_by_name"):
                data["detail"] = f"Request from {status.get('requested_by_name')}"
        elif mode == "read_only":
            locked = bool(status.get("locked"))
            stale = bool(status.get("is_stale"))
            data = {
                "mode": "read_only",
                "owner": status.get("owner_name") or ("Stale lock" if stale else "Available"),
                "machine": status.get("machine_name") or "",
                "detail": detail,
                "can_request": True,
                "can_release": False,
                "can_takeover": stale and not locked,
                "can_force_takeover": bool(self.db_path and (locked or stale)),
                "request_sent": self.handover_requested,
                "locked": locked,
            }
        else:
            data = {"mode": "none", "owner": "-", "machine": "", "detail": detail, "can_request": False, "can_release": False, "can_takeover": False}
        try:
            sidebar.set_edit_lock_status(data)
        except Exception:
            pass

    def _apply_read_only_ui(self, read_only: bool, detail: str = "") -> None:
        can_edit = not read_only
        mw = self.mw

        # Abort transient write workflows when the lock is lost.
        if read_only:
            try:
                mw._workflow_active = False
                if hasattr(mw, "_remap_active"):
                    mw._remap_active = False
                if hasattr(mw, "idx_transmit"):
                    mw.tabs.setTabEnabled(mw.idx_transmit, False)
                if hasattr(mw, "idx_files"):
                    mw.tabs.setTabEnabled(mw.idx_files, False)
            except Exception:
                pass

        # Register table edit triggers.
        try:
            table = mw.register_tab.table
            if not hasattr(table, "_edit_lock_original_triggers"):
                table._edit_lock_original_triggers = table.editTriggers()
            if read_only:
                table.setEditTriggers(QTableView.NoEditTriggers)
            else:
                table.setEditTriggers(table._edit_lock_original_triggers)
        except Exception:
            pass

        # Directly known high-risk controls.
        for obj in (
            getattr(getattr(mw, "register_tab", None), "btn_delete", None),
            getattr(getattr(mw, "register_tab", None), "btn_proceed", None),
            getattr(getattr(mw, "files_tab", None), "btn_proceed", None),
        ):
            try:
                if obj is not None:
                    obj.setEnabled(can_edit)
            except Exception:
                pass

        # Sidebar has its own method so it can preserve filtering/printing.
        try:
            if hasattr(mw.sidebar, "set_read_only_mode"):
                mw.sidebar.set_read_only_mode(read_only)
        except Exception:
            pass

        for root in self._write_roots():
            self._set_dangerous_controls_enabled(root, can_edit)

        # Re-apply lock buttons after generic scans.
        try:
            self._update_sidebar({
                "mode": "edit" if self.owner_token else ("read_only" if self.db_path else "none"),
                "detail": detail,
                "status": locks.get_lock_status(self.db_path) if self.db_path else {},
            })
        except Exception:
            pass

    def _write_roots(self) -> Iterable[Any]:
        for name in ("register_tab", "transmittal_tab", "files_tab", "history_tab", "checkprint_tab"):
            root = getattr(self.mw, name, None)
            if root is not None:
                yield root

    def _set_dangerous_controls_enabled(self, root: Any, enabled: bool) -> None:
        for cls in (QPushButton, QToolButton):
            try:
                widgets = root.findChildren(cls)
            except Exception:
                widgets = []
            for widget in widgets:
                text = (widget.text() or "").replace("&", "").strip().lower()
                if not text:
                    continue
                if self._is_lock_widget(widget):
                    continue
                if self._is_dangerous_text(text):
                    try:
                        widget.setEnabled(enabled)
                    except Exception:
                        pass
        try:
            actions = root.findChildren(QAction)
        except Exception:
            actions = []
        for action in actions:
            text = (action.text() or "").replace("&", "").strip().lower()
            if self._is_dangerous_text(text):
                try:
                    action.setEnabled(enabled)
                except Exception:
                    pass

    def _is_lock_widget(self, widget: Any) -> bool:
        try:
            return bool((widget.objectName() or "").startswith("EditLock"))
        except Exception:
            return False

    def _is_dangerous_text(self, text: str) -> bool:
        if any(safe in text for safe in _SAFE_WORDS):
            return False
        return any(word in text for word in _DANGER_WORDS)
