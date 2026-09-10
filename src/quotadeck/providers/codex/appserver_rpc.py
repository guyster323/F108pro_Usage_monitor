from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from quotadeck.core.models import UsageSnapshot, UsageWindow
from quotadeck.discovery.executables import find_executable
from quotadeck.providers.codex.auth import CodexAuth


class CodexRPCError(RuntimeError):
    pass


class _StdoutReader:
    """Reads child stdout on a daemon thread so RPC waits honour deadlines.

    A blocking readline() on the caller's thread can wait forever if the child
    stays alive without emitting a line; a queue with timeouts cannot.
    """

    _EOF = object()

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        self._proc = proc
        self._queue: queue.Queue[object] = queue.Queue()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        stdout = self._proc.stdout
        if stdout is not None:
            try:
                for line in stdout:
                    self._queue.put(line)
            except Exception:
                pass
        self._queue.put(self._EOF)

    def read_message(self, deadline: float) -> dict | None:
        """Next JSON message before the monotonic deadline; None on EOF."""
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CodexRPCError("RPC timeout")
            try:
                item = self._queue.get(timeout=remaining)
            except queue.Empty:
                raise CodexRPCError("RPC timeout") from None
            if item is self._EOF:
                return None
            line = str(item).strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue

    def close(self) -> None:
        self._thread.join(timeout=1.0)


def _rpc(reader: _StdoutReader, proc: subprocess.Popen[str], payload: dict, timeout: float) -> dict:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    deadline = time.monotonic() + timeout
    while True:
        message = reader.read_message(deadline)
        if message is None:
            raise CodexRPCError("RPC stream closed")
        if payload.get("id") is not None and message.get("id") == payload.get("id"):
            if "error" in message:
                raise CodexRPCError(str(message["error"]))
            return message.get("result") or {}


def fetch_app_server(auth: CodexAuth) -> UsageSnapshot:
    exe = find_executable("codex")
    if exe is None:
        raise CodexRPCError("codex CLI not found")
    env = os.environ.copy()
    env["CODEX_HOME"] = str(auth.home)
    creation = 0
    if sys.platform == "win32":
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        [str(exe), "-s", "read-only", "-a", "never", "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        env=env,
        creationflags=creation,
    )
    reader = _StdoutReader(proc)
    try:
        _rpc(
            reader,
            proc,
            {
                "id": 1,
                "method": "initialize",
                "params": {"clientInfo": {"name": "quotadeck", "version": "0.1.0"}},
            },
            8.0,
        )
        assert proc.stdin is not None
        proc.stdin.write(json.dumps({"method": "initialized", "params": {}}) + "\n")
        proc.stdin.flush()
        limits = _rpc(reader, proc, {"id": 2, "method": "account/rateLimits/read", "params": {}}, 3.0)
        account = {}
        try:
            account = _rpc(reader, proc, {"id": 3, "method": "account/read", "params": {}}, 3.0)
        except CodexRPCError:
            account = {}
    finally:
        if proc.stdin:
            try:
                proc.stdin.close()
            except Exception:
                pass
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        reader.close()

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
