from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from quotadeck.core.models import UsageSnapshot, UsageWindow
from quotadeck.discovery.executables import find_executable
from quotadeck.providers.codex.auth import CodexAuth


class CodexRPCError(RuntimeError):
    pass


def _read_line(proc: subprocess.Popen[str], timeout: float) -> dict | None:
    if proc.stdout is None:
        return None
    line = proc.stdout.readline()
    if not line:
        return None
    line = line.strip()
    if not line:
        return _read_line(proc, timeout)
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return _read_line(proc, timeout)


def _rpc(proc: subprocess.Popen[str], payload: dict, timeout: float) -> dict:
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload) + "\n")
    proc.stdin.flush()
    deadline = datetime.now(timezone.utc).timestamp() + timeout
    while datetime.now(timezone.utc).timestamp() < deadline:
        message = _read_line(proc, timeout)
        if message is None:
            break
        if payload.get("id") is not None and message.get("id") == payload.get("id"):
            if "error" in message:
                raise CodexRPCError(str(message["error"]))
            return message.get("result") or {}
    raise CodexRPCError("RPC timeout")


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
    try:
        _rpc(
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
        limits = _rpc(proc, {"id": 2, "method": "account/rateLimits/read", "params": {}}, 3.0)
        account = {}
        try:
            account = _rpc(proc, {"id": 3, "method": "account/read", "params": {}}, 3.0)
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
