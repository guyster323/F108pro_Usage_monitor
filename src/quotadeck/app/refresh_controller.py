from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from quotadeck.config import AppConfig

log = logging.getLogger("quotadeck")

# Job kinds. "auto" applies the normal upload policy, "refresh" only reads
# usage (never writes flash), "upload" is the explicit user action (force).
_PRIORITY = {"auto": 0, "refresh": 1, "upload": 2}


class _RefreshWorker(QThread):
    polled = Signal(object, object)  # snapshots, frames
    uploaded = Signal(object)  # UploadResult
    failed = Signal(str)

    def __init__(self, runtime, kind: str) -> None:
        super().__init__()
        self.runtime = runtime
        self.kind = kind

    def run(self) -> None:
        try:
            snapshots = self.runtime.poll()
            frames = self.runtime.build_frames(snapshots)
        except Exception as exc:
            log.exception("usage poll failed")
            self.failed.emit(str(exc))
            return
        # Poll results reach the UI even if the upload below is skipped/fails.
        self.polled.emit(snapshots, frames)
        if self.kind in ("auto", "upload"):
            result = self.runtime.maybe_upload(snapshots, force=self.kind == "upload")
            log.info("upload result (%s): %s", self.kind, result.message())
            self.uploaded.emit(result)


class RefreshController(QObject):
    """Owns the runtime, the poll schedule, and the single background worker.

    The controller lives for the whole application lifetime; hiding or
    restoring the window never affects the schedule. At most one worker runs
    at a time, and at most one follow-up job is queued while it runs, so
    delayed ticks are never replayed in a burst.
    """

    polled = Signal(object, object)  # snapshots, frames
    upload_done = Signal(object, bool)  # UploadResult, manual
    poll_failed = Signal(str, bool)  # message, manual
    schedule_updated = Signal(object, object)  # last_poll, next_poll
    busy_changed = Signal(bool)

    def __init__(self, runtime, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timer)
        self._worker: _RefreshWorker | None = None
        self._current_kind: str | None = None
        self._pending: str | None = None
        self._pending_config: AppConfig | None = None
        self._started = False
        self._stopping = False
        self.last_poll: datetime | None = None
        self.next_poll: datetime | None = None

    # -- public API ----------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        # First run (no saved account selection yet) must not flash every
        # discovered account automatically; it only reads usage.
        kind = "auto" if self.runtime.config.accounts else "refresh"
        self._launch(kind)

    def request_refresh(self) -> None:
        """Read usage now without forcing an LCD write."""
        self._request("refresh")

    def request_upload(self) -> None:
        """Explicit user upload; bypasses cooldown but not the daily limit."""
        self._request("upload")

    def update_config(self, config: AppConfig) -> None:
        """Apply settings; deferred until the running job finishes."""
        if self._worker is not None:
            self._pending_config = config
            return
        self.runtime.update_config(config)
        self._reschedule()

    def is_busy(self) -> bool:
        return self._worker is not None

    def shutdown(self, wait_ms: int = 30_000) -> None:
        self._stopping = True
        self._started = False
        self._timer.stop()
        self._pending = None
        self._pending_config = None
        worker = self._worker
        if worker is not None and not worker.wait(wait_ms):
            log.warning("refresh worker did not finish within %sms at shutdown", wait_ms)

    # -- internals -------------------------------------------------------
    def _request(self, kind: str) -> None:
        if self._stopping:
            return
        if self._worker is not None:
            if self._pending is None or _PRIORITY[kind] > _PRIORITY[self._pending]:
                self._pending = kind
            return
        self._launch(kind)

    def _on_timer(self) -> None:
        self._request("auto")

    def _launch(self, kind: str) -> None:
        self._timer.stop()
        worker = _RefreshWorker(self.runtime, kind)
        self._worker = worker
        self._current_kind = kind
        worker.polled.connect(self._on_polled)
        worker.uploaded.connect(self._on_uploaded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(self._on_finished)
        self.busy_changed.emit(True)
        worker.start()

    def _on_polled(self, snapshots, frames) -> None:
        self.last_poll = datetime.now(timezone.utc)
        self.polled.emit(snapshots, frames)

    def _on_uploaded(self, result) -> None:
        self.upload_done.emit(result, self._current_kind == "upload")

    def _on_failed(self, message: str) -> None:
        self.poll_failed.emit(message, self._current_kind in ("refresh", "upload"))

    def _on_finished(self) -> None:
        worker = self._worker
        self._worker = None
        self._current_kind = None
        if worker is not None:
            worker.deleteLater()
        if self._pending_config is not None:
            self.runtime.update_config(self._pending_config)
            self._pending_config = None
        self.busy_changed.emit(False)
        pending = self._pending
        self._pending = None
        if pending is not None and not self._stopping:
            self._launch(pending)
            return
        self._reschedule()

    def _reschedule(self) -> None:
        if not self._started or self._worker is not None:
            return
        interval = max(15, int(self.runtime.config.poll_seconds))
        self._timer.start(interval * 1000)
        self.next_poll = datetime.now(timezone.utc) + timedelta(seconds=interval)
        self.schedule_updated.emit(self.last_poll, self.next_poll)
