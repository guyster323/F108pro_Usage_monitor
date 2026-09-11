from __future__ import annotations

import atexit
import faulthandler
import logging
import os
import platform
import re
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

from quotadeck import __version__
from quotadeck.config import app_dir
from quotadeck.core.mask import mask_text

DEFAULT_MAX_BYTES = 2 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 3
DEFAULT_KEEP_DAYS = 14
DEFAULT_MAX_LOG_FILES = 80
_active_session: DiagnosticSession | None = None
_PID_PATTERN = re.compile(r"-pid(?P<pid>\d+)\.log$")


class RedactingFormatter(logging.Formatter):
    """Mask credentials and local identity after formatting the traceback."""

    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        return mask_text(super().format(record))


class PrivateRotatingFileHandler(RotatingFileHandler):
    """Create every rollover file with user-only permissions on POSIX."""

    def _open(self):
        stream = super()._open()
        if os.name == "posix":
            try:
                os.chmod(self.baseFilename, 0o600)
            except OSError:
                pass
        return stream


def _fallback_log_directory() -> Path:
    suffix = f"-{os.getuid()}" if hasattr(os, "getuid") else ""
    return Path(tempfile.gettempdir()) / f"QuotaDeck{suffix}" / "logs"


def _resolve_log_directory(log_dir: Path | None) -> Path:
    candidates = [Path(log_dir)] if log_dir is not None else []
    if log_dir is None:
        try:
            candidates.append(app_dir() / "logs")
        except Exception:
            pass
        candidates.append(_fallback_log_directory())
    last_error: OSError | None = None
    for candidate in candidates:
        probe: Path | None = None
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            resolved = candidate.resolve()
            if os.name == "posix":
                os.chmod(resolved, 0o700)
            probe = resolved / f".quotadeck-log-probe-{os.getpid()}-{uuid.uuid4().hex}"
            descriptor = os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            probe.unlink()
            return resolved
        except OSError as exc:
            last_error = exc
            if probe is not None:
                try:
                    probe.unlink(missing_ok=True)
                except OSError:
                    pass
    if last_error is not None:
        raise last_error
    raise OSError("no diagnostic log directory available")


def _remove_old_logs(
    directory: Path,
    keep_days: int,
    max_files: int = DEFAULT_MAX_LOG_FILES,
) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, keep_days))
    retained: list[tuple[float, Path]] = []
    for path in directory.glob("quotadeck-*.log*"):
        try:
            mtime = path.stat().st_mtime
            changed = datetime.fromtimestamp(mtime, tz=timezone.utc)
            if changed < cutoff:
                path.unlink()
            else:
                retained.append((mtime, path))
        except OSError:
            continue
    retained.sort(key=lambda item: item[0], reverse=True)
    for _mtime, path in retained[max(1, max_files) :]:
        try:
            path.unlink()
        except OSError:
            continue


def _tail(path: Path, limit: int = 128 * 1024) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            return stream.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _previous_unclean_ui_log(directory: Path) -> Path | None:
    candidates: list[tuple[float, Path]] = []
    for path in directory.glob("quotadeck-ui-*.log"):
        try:
            if path.is_file() and not path.name.endswith("-crash.log"):
                candidates.append((path.stat().st_mtime, path))
        except OSError:
            continue
    for _mtime, latest in sorted(candidates, key=lambda item: item[0], reverse=True):
        match = _PID_PATTERN.search(latest.name)
        if match is not None and _pid_is_alive(int(match.group("pid"))):
            continue
        clean: bool | None = None
        marker = " quotadeck event=process_end "
        # RotatingFileHandler keeps the newest records in the base file and
        # older records in .1, .2, ... . Qt can still emit teardown messages
        # after process_end, so the marker may have just rolled into .1.
        rotations: list[tuple[int, Path]] = []
        for path in directory.glob(f"{latest.name}.*"):
            suffix = path.name.removeprefix(f"{latest.name}.")
            if suffix.isdigit():
                rotations.append((int(suffix), path))
        segments = [latest, *(path for _index, path in sorted(rotations))]
        for segment in segments:
            for line in reversed(_tail(segment).splitlines()):
                if marker not in line:
                    continue
                fields = line.split(marker, 1)[1]
                if fields.startswith("clean=true ") or fields == "clean=true":
                    clean = True
                elif fields.startswith("clean=false ") or fields == "clean=false":
                    clean = False
                break
            if clean is not None:
                break
        return None if clean is True else latest
    return None


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    # configure_diagnostics never creates two active sessions in one process;
    # a prior log with our PID belongs to a session that was explicitly closed.
    if pid == os.getpid():
        return False
    if os.name == "posix":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    if sys.platform == "win32":
        try:
            import ctypes

            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle.restype = ctypes.c_int
            handle = kernel.OpenProcess(0x1000, False, pid)
            if handle:
                kernel.CloseHandle(handle)
                return True
            # Access denied also means that a process with this PID exists.
            return ctypes.get_last_error() == 5
        except Exception:
            return False
    return False


def _format_fields(fields: dict[str, object]) -> str:
    rendered: list[str] = []
    for key, value in sorted(fields.items()):
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif value is None:
            text = "null"
        elif isinstance(value, (int, float)):
            text = str(value)
        else:
            text = repr(str(value))
        rendered.append(f"{key}={text}")
    return " ".join(rendered)


class DiagnosticSession:
    def __init__(
        self,
        component: str,
        directory: Path,
        *,
        max_bytes: int,
        backup_count: int,
        console: bool,
    ) -> None:
        self.component = component
        self.directory = directory
        self.session_id = uuid.uuid4().hex[:12]
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        stem = f"quotadeck-{component}-{stamp}-pid{os.getpid()}"
        self.log_path = directory / f"{stem}.log"
        self.crash_path = directory / f"{stem}-crash.log"
        self.logger = logging.getLogger("quotadeck")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        self._clean = False
        self._ended = False
        self._closed = False
        self._fault_stream: TextIO | None = None
        self._fault_was_enabled = faulthandler.is_enabled()
        self._old_sys_hook = None
        self._old_thread_hook = None
        self._old_unraisable_hook = None
        self._sys_hook = None
        self._thread_hook = None
        self._unraisable_hook = None
        self._qt_handler = None
        self._old_qt_handler = None

        formatter = RedactingFormatter(
            "%(asctime)s.%(msecs)03dZ %(levelname)s pid=%(process)d "
            "thread=%(threadName)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        self._handler = PrivateRotatingFileHandler(
            self.log_path,
            maxBytes=max(256, int(max_bytes)),
            backupCount=max(1, int(backup_count)),
            encoding="utf-8",
        )
        self._handler._quotadeck_owned = True  # type: ignore[attr-defined]
        self._handler.setLevel(logging.DEBUG)
        self._handler.setFormatter(formatter)
        self.logger.addHandler(self._handler)
        self._console_handler: logging.Handler | None = None
        if console:
            console_handler = logging.StreamHandler()
            console_handler._quotadeck_owned = True  # type: ignore[attr-defined]
            console_handler.setLevel(logging.INFO)
            console_handler.setFormatter(formatter)
            self.logger.addHandler(console_handler)
            self._console_handler = console_handler

    def event(self, name: str, level: int = logging.INFO, **fields: object) -> None:
        suffix = _format_fields(fields)
        message = f"event={name}"
        if suffix:
            message += f" {suffix}"
        self.logger.log(level, message)

    def exception(self, name: str, **fields: object) -> None:
        suffix = _format_fields(fields)
        message = f"event={name}"
        if suffix:
            message += f" {suffix}"
        self.logger.exception(message)
        self.flush()

    def flush(self) -> None:
        for handler in (self._handler, self._console_handler):
            if handler is not None:
                try:
                    handler.flush()
                except Exception:
                    # Diagnostics must never replace the original failure
                    # when a profile, disk, or console stream becomes
                    # unwritable during shutdown.
                    pass
        if self._fault_stream is not None:
            try:
                self._fault_stream.flush()
            except Exception:
                pass

    def install_exception_hooks(self) -> None:
        if self._old_sys_hook is not None:
            return
        self._old_sys_hook = sys.excepthook
        self._old_thread_hook = threading.excepthook
        self._old_unraisable_hook = sys.unraisablehook

        def sys_hook(exc_type, exc_value, exc_traceback) -> None:
            self.logger.critical(
                "event=python_unhandled_exception origin=main",
                exc_info=(exc_type, exc_value, exc_traceback),
            )
            self.flush()
            # Do not pass the original exception to a previous/default hook.
            # It would print the unredacted message (and possibly source-line
            # literals) to redirected stderr after our safe formatter ran.

        def thread_hook(args) -> None:
            self.logger.critical(
                "event=python_unhandled_exception origin=thread thread=%r",
                getattr(args.thread, "name", None),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            self.flush()

        def unraisable_hook(args) -> None:
            object_type = type(args.object).__qualname__ if args.object is not None else None
            self.logger.error(
                "event=python_unraisable object_type=%r error=%r",
                object_type,
                args.err_msg,
                exc_info=(type(args.exc_value), args.exc_value, args.exc_traceback),
            )
            self.flush()

        self._sys_hook = sys_hook
        self._thread_hook = thread_hook
        self._unraisable_hook = unraisable_hook
        sys.excepthook = sys_hook
        threading.excepthook = thread_hook
        sys.unraisablehook = unraisable_hook

    def enable_fault_handler(self) -> None:
        stream: TextIO | None = None
        try:
            stream = self.crash_path.open("a", encoding="utf-8", buffering=1)
            if os.name == "posix":
                os.chmod(self.crash_path, 0o600)
            stream.write(
                f"\n=== QuotaDeck session {self.session_id} pid={os.getpid()} "
                f"utc={datetime.now(timezone.utc).isoformat()} ===\n"
            )
            faulthandler.enable(file=stream, all_threads=True)
            self._fault_stream = stream
            self.event("faulthandler_enabled", path=self.crash_path.name)
        except Exception as exc:
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
            self._fault_stream = None
            self.event("faulthandler_unavailable", level=logging.WARNING, error=exc)

    def install_qt_message_handler(self) -> None:
        if self._qt_handler is not None:
            return
        try:
            from PySide6.QtCore import QtMsgType, qInstallMessageHandler

            levels = {
                QtMsgType.QtDebugMsg: logging.DEBUG,
                QtMsgType.QtInfoMsg: logging.INFO,
                QtMsgType.QtWarningMsg: logging.WARNING,
                QtMsgType.QtCriticalMsg: logging.ERROR,
                QtMsgType.QtFatalMsg: logging.CRITICAL,
            }
            guard = threading.local()

            def handler(kind, context, message) -> None:
                if getattr(guard, "active", False):
                    return
                guard.active = True
                try:
                    location = ""
                    if context is not None:
                        source = Path(context.file).name if context.file else ""
                        if source or context.line:
                            location = f" source={source!r} line={context.line}"
                    self.logger.log(
                        levels.get(kind, logging.INFO),
                        "event=qt_message type=%s%s message=%s",
                        getattr(kind, "name", str(kind)),
                        location,
                        message,
                    )
                    if kind == QtMsgType.QtFatalMsg:
                        self.flush()
                finally:
                    guard.active = False

            self._qt_handler = handler
            self._old_qt_handler = qInstallMessageHandler(handler)
            self.event("qt_message_handler_installed")
        except Exception as exc:
            self.event("qt_message_handler_unavailable", level=logging.WARNING, error=exc)

    def mark_shutdown(self, reason: str, *, exit_code: int = 0) -> None:
        if self._closed or self._ended:
            return
        self.event("process_end", clean=True, exit_code=exit_code, reason=reason)
        self._clean = True
        self._ended = True
        self.flush()

    def mark_unclean_shutdown(self, reason: str, *, exit_code: int = 1) -> None:
        if self._closed or self._ended:
            return
        self.event(
            "process_end",
            level=logging.ERROR,
            clean=False,
            exit_code=exit_code,
            reason=reason,
        )
        self._ended = True
        self.flush()

    def close(self) -> None:
        global _active_session
        if self._closed:
            return
        self.flush()
        if self._qt_handler is not None:
            try:
                from PySide6.QtCore import qInstallMessageHandler

                qInstallMessageHandler(self._old_qt_handler)
            except Exception:
                pass
        if self._old_sys_hook is not None and sys.excepthook is self._sys_hook:
            sys.excepthook = self._old_sys_hook
        if self._old_thread_hook is not None and threading.excepthook is self._thread_hook:
            threading.excepthook = self._old_thread_hook
        if self._old_unraisable_hook is not None and sys.unraisablehook is self._unraisable_hook:
            sys.unraisablehook = self._old_unraisable_hook
        if self._fault_stream is not None:
            try:
                faulthandler.disable()
                self._fault_stream.close()
                if self._fault_was_enabled:
                    faulthandler.enable(all_threads=True)
            except Exception:
                pass
            self._fault_stream = None
        for handler in (self._handler, self._console_handler):
            if handler is not None:
                self.logger.removeHandler(handler)
                handler.close()
        self._closed = True
        if _active_session is self:
            _active_session = None

    def _on_process_exit(self) -> None:
        if self._closed or self._ended:
            return
        self.event("process_end", level=logging.ERROR, clean=False, reason="atexit_without_clean_qt_shutdown")
        self._ended = True
        self.flush()


def configure_diagnostics(
    component: str,
    *,
    log_dir: Path | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
    keep_days: int = DEFAULT_KEEP_DAYS,
    console: bool = False,
    install_hooks: bool = True,
    enable_faults: bool = True,
    check_previous: bool = True,
) -> DiagnosticSession:
    global _active_session
    if _active_session is not None and not _active_session._closed:
        _active_session.event("diagnostics_reused", requested_component=component)
        return _active_session

    directory = _resolve_log_directory(log_dir)
    _remove_old_logs(directory, keep_days)
    previous = _previous_unclean_ui_log(directory) if component == "ui" and check_previous else None
    try:
        session = DiagnosticSession(
            component,
            directory,
            max_bytes=max_bytes,
            backup_count=backup_count,
            console=console,
        )
    except OSError:
        if log_dir is not None:
            raise
        fallback = _resolve_log_directory(_fallback_log_directory())
        if fallback == directory:
            raise
        directory = fallback
        _remove_old_logs(directory, keep_days)
        previous = _previous_unclean_ui_log(directory) if component == "ui" and check_previous else None
        session = DiagnosticSession(
            component,
            directory,
            max_bytes=max_bytes,
            backup_count=backup_count,
            console=console,
        )
    _active_session = session
    session.event(
        "session_start",
        component=component,
        frozen=bool(getattr(sys, "frozen", False)),
        platform=platform.platform(),
        python=platform.python_version(),
        session=session.session_id,
        version=__version__,
    )
    if previous is not None:
        session.event(
            "previous_ui_session_unclean",
            level=logging.WARNING,
            previous_log=previous.name,
        )
    if install_hooks:
        session.install_exception_hooks()
    if enable_faults:
        session.enable_fault_handler()
    atexit.register(session._on_process_exit)
    return session


def current_session() -> DiagnosticSession | None:
    return _active_session


def diagnostic_log_directory() -> Path:
    if _active_session is not None and not _active_session._closed:
        return _active_session.directory
    return _resolve_log_directory(None)
