from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from quotadeck.core.models import UsageSnapshot, UsageWindow
from quotadeck.discovery.executables import find_executable
from quotadeck.providers.codex.auth import CodexAuth

log = logging.getLogger("quotadeck.provider.codex_rpc")
_EOF = object()
_LINE_TOO_LONG = object()
_READ_FAILED = object()
_RPC_MAX_LINE_CHARS = 256 * 1024
_RPC_QUEUE_MAX_ITEMS = 16
_QUEUE_PUT_POLL_SECONDS = 0.05


class CodexRPCError(RuntimeError):
    pass


def _app_server_environment(home: Path) -> dict[str, str]:
    """Keep launch essentials while withholding unrelated process secrets."""

    env = {"CODEX_HOME": str(home)}
    for name in (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "APPDATA",
        "LOCALAPPDATA",
        "USERPROFILE",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    ):
        value = os.environ.get(name)
        if isinstance(value, str) and value:
            env[name] = value
    return env


class _JsonLineReader:
    """Read bounded JSONL records without letting child output grow memory."""

    def __init__(
        self,
        stream: TextIO,
        *,
        max_line_chars: int = _RPC_MAX_LINE_CHARS,
        max_queue_items: int = _RPC_QUEUE_MAX_ITEMS,
    ) -> None:
        if max_line_chars < 1:
            raise ValueError("max_line_chars must be positive")
        if max_queue_items < 1:
            raise ValueError("max_queue_items must be positive")
        self._max_line_chars = max_line_chars
        self._items: queue.Queue[str | object] = queue.Queue(maxsize=max_queue_items)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._read,
            args=(stream,),
            name="codex-rpc-reader",
            daemon=True,
        )
        self._thread.start()

    def _put(self, item: str | object) -> bool:
        while not self._stop.is_set():
            try:
                self._items.put(item, timeout=_QUEUE_PUT_POLL_SECONDS)
                return True
            except queue.Full:
                continue
        return False

    def _read(self, stream: TextIO) -> None:
        try:
            try:
                fd = stream.fileno()
            except (AttributeError, OSError, ValueError):
                self._read_text_stream(stream)
            else:
                self._read_pipe(fd)
        except (OSError, UnicodeError, ValueError):
            if not self._stop.is_set():
                self._put(_READ_FAILED)

    def _read_text_stream(self, stream: TextIO) -> None:
        """Bound reads for in-memory/test streams that have no file descriptor."""

        while not self._stop.is_set():
            # Include room for CRLF while keeping the JSON payload itself
            # within the configured character limit.  Unlike iteration or
            # readline() without a size, this never materializes an
            # attacker-controlled, unterminated line in one allocation.
            line = stream.readline(self._max_line_chars + 2)
            if line == "":
                self._put(_EOF)
                return
            line_ending_chars = 0
            if line.endswith("\n"):
                line_ending_chars = 1
                if line.endswith("\r\n"):
                    line_ending_chars = 2
            if len(line) - line_ending_chars > self._max_line_chars:
                self._put(_LINE_TOO_LONG)
                return
            if not self._put(line):
                return

    def _read_pipe(self, fd: int) -> None:
        """Poll a real child pipe so stop() also interrupts an idle read."""

        pending = bytearray()
        while not self._stop.is_set():
            newline_at = pending.find(b"\n")
            if newline_at >= 0:
                payload_bytes = newline_at
                if newline_at and pending[newline_at - 1] == 0x0D:
                    payload_bytes -= 1
                if payload_bytes > self._max_line_chars:
                    self._put(_LINE_TOO_LONG)
                    return
                raw_line = bytes(pending[: newline_at + 1])
                del pending[: newline_at + 1]
                if not self._put(raw_line.decode("utf-8", errors="replace")):
                    return
                continue

            # One extra byte detects an over-limit payload; a second permits
            # the CRLF terminator at the exact boundary.
            if len(pending) > self._max_line_chars:
                if not (
                    len(pending) == self._max_line_chars + 1
                    and pending[-1] == 0x0D
                ):
                    self._put(_LINE_TOO_LONG)
                    return
            read_size = max(
                1,
                min(8192, self._max_line_chars + 2 - len(pending)),
            )
            chunk = self._read_pipe_chunk(fd, read_size)
            if chunk is None:
                continue
            if chunk == b"":
                if pending:
                    if len(pending) > self._max_line_chars:
                        self._put(_LINE_TOO_LONG)
                        return
                    if not self._put(pending.decode("utf-8", errors="replace")):
                        return
                self._put(_EOF)
                return
            pending.extend(chunk)

    def _read_pipe_chunk(self, fd: int, size: int) -> bytes | None:
        if sys.platform != "win32":
            import select

            try:
                readable, _, _ = select.select(
                    [fd],
                    [],
                    [],
                    _QUEUE_PUT_POLL_SECONDS,
                )
            except InterruptedError:
                return None
            if not readable:
                return None
            return os.read(fd, size)

        # select() cannot wait on anonymous pipes on Windows.  PeekNamedPipe
        # makes the same read cooperative without changing the descriptor's
        # blocking mode (important for supported Python 3.11 installations).
        import ctypes
        import msvcrt
        from ctypes import wintypes

        available = wintypes.DWORD()
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(fd))
        kernel32 = ctypes.windll.kernel32
        if not kernel32.PeekNamedPipe(
            handle,
            None,
            0,
            None,
            ctypes.byref(available),
            None,
        ):
            error = int(kernel32.GetLastError())
            if error in (109, 232, 233):  # broken, closing, or disconnected pipe
                return b""
            raise OSError(error, "PeekNamedPipe failed")
        if not available.value:
            self._stop.wait(_QUEUE_PUT_POLL_SECONDS)
            return None
        return os.read(fd, min(size, int(available.value)))

    def get(self, timeout: float) -> dict | None:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexRPCError("RPC timeout")
            try:
                item = self._items.get(timeout=remaining)
            except queue.Empty as exc:
                raise CodexRPCError("RPC timeout") from exc
            if item is _EOF:
                return None
            if item is _LINE_TOO_LONG:
                raise CodexRPCError("RPC line exceeds safety limit")
            if item is _READ_FAILED:
                raise CodexRPCError("RPC stream read failed")
            line = str(item).strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(message, dict):
                return message

    def stop(self) -> None:
        """Release a producer blocked by a full queue during RPC cleanup."""

        self._stop.set()

    def join(self, timeout: float = 1.0) -> bool:
        self._thread.join(timeout=max(0.0, timeout))
        return not self._thread.is_alive()


def _rpc(
    proc: subprocess.Popen[str],
    reader: _JsonLineReader,
    payload: dict,
    timeout: float,
) -> dict:
    assert proc.stdin is not None
    request_id = payload.get("id")
    method = str(payload.get("method") or "unknown")
    started = time.monotonic()
    log.debug("event=codex_rpc_request method=%s request_id=%r", method, request_id)
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = reader.get(deadline - time.monotonic())
        if message is None:
            raise CodexRPCError("RPC stream closed")
        if request_id is not None and message.get("id") == request_id:
            if "error" in message:
                raise CodexRPCError(str(message["error"]))
            log.debug(
                "event=codex_rpc_response method=%s duration_ms=%d",
                method,
                int((time.monotonic() - started) * 1000),
            )
            return message.get("result") or {}
    raise CodexRPCError("RPC timeout")


def fetch_app_server(auth: CodexAuth) -> UsageSnapshot:
    exe = find_executable("codex")
    if exe is None:
        raise CodexRPCError("codex CLI not found")
    env = _app_server_environment(auth.home)
    creation = 0
    if sys.platform == "win32":
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [str(exe), "-s", "read-only", "-a", "never", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=env,
        creationflags=creation,
    )
    if proc.stdout is None:
        proc.terminate()
        raise CodexRPCError("codex app-server stdout unavailable")
    reader = _JsonLineReader(proc.stdout)
    started = time.monotonic()
    log.info("event=codex_rpc_process_start")
    try:
        _rpc(
            proc,
            reader,
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "quotadeck", "version": "0.2.3"}},
            },
            8.0,
        )
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"method": "initialized", "params": {}}) + "\n")
        proc.stdin.flush()
        limits = _rpc(proc, reader, {"id": 2, "method": "account/rateLimits/read", "params": {}}, 3.0)
        account = {}
        try:
            account = _rpc(proc, reader, {"id": 3, "method": "account/read", "params": {}}, 3.0)
        except CodexRPCError:
            log.warning("event=codex_rpc_account_read_failed", exc_info=True)
            account = {}
    finally:
        if proc.stdin:
            try:
                proc.stdin.close()
            except Exception:
                pass
        reader.stop()
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            log.warning("event=codex_rpc_terminate_failed", exc_info=True)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except Exception:
                log.warning("event=codex_rpc_kill_failed", exc_info=True)
            else:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    log.error("event=codex_rpc_process_did_not_exit_after_kill")
                except Exception:
                    log.warning("event=codex_rpc_wait_after_kill_failed", exc_info=True)
        except Exception:
            log.warning("event=codex_rpc_wait_failed", exc_info=True)
        reader_stopped = reader.join(1.0)
        if not reader_stopped:
            log.warning("event=codex_rpc_reader_still_running")
        if reader_stopped and proc.stdout is not None:
            try:
                proc.stdout.close()
            except Exception:
                log.warning("event=codex_rpc_stdout_close_failed", exc_info=True)
        log.info(
            "event=codex_rpc_process_end duration_ms=%d returncode=%r",
            int((time.monotonic() - started) * 1000),
            proc.returncode,
        )
    rate = limits.get("rateLimits") or limits.get("rate_limits") or limits
    windows: list[UsageWindow] = []
    for key, window_id, label in (
        ("primary", "session", "5H"),
        ("secondary", "weekly", "WEEK"),
    ):
        raw = rate.get(key) or {}
        used = raw.get("usedPercent", raw.get("used_percent"))
        if used is None:
            continue
        used_f = float(used)
        reset = raw.get("resetsAt") or raw.get("resets_at")
        resets_at = datetime.fromtimestamp(int(reset), tz=timezone.utc) if reset else None
        windows.append(
            UsageWindow(
                id=window_id,
                label=label,
                used_percent=used_f,
                remaining_percent=max(0.0, 100.0 - used_f),
                resets_at=resets_at,
            )
        )
    acct = account.get("account") or {}
    email = acct.get("email") or auth.email or "codex"
    plan = acct.get("planType") or acct.get("plan_type") or auth.plan
    return UsageSnapshot(
        provider="codex",
        account_id=auth.account_id or "unknown",
        display_name=email.split("@", 1)[0].upper(),
        plan=str(plan) if plan else None,
        windows=windows,
        status="ok" if windows else "error",
        fetched_at=datetime.now(timezone.utc),
        error=None if windows else "app-server returned no windows",
        source_path=str(auth.home),
    )
