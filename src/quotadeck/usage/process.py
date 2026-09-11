"""Bounded subprocess helper for optional external usage collectors.

Production calls never use a shell. Stdout is drained incrementally and the
child is killed as soon as the byte cap or timeout is crossed. Stderr is
discarded so a CLI diagnostic cannot leak credentials into UI logs.
"""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quotadeck.discovery.executables import find_executable


DEFAULT_COLLECTOR_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_JSON_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_RECORDS = 20_000

_Runner = Callable[..., subprocess.CompletedProcess[bytes] | subprocess.CompletedProcess[str]]


@dataclass(frozen=True, slots=True)
class BoundedCommandResult:
    returncode: int | None = None
    stdout: bytes = b""
    issue_code: str | None = None


def resolve_optional_executable(
    name: str,
    *,
    explicit: str | Path | None = None,
    env_var: str | None = None,
) -> Path | None:
    """Return a user-supplied or PATH-discovered collector executable."""

    if explicit is not None:
        path = Path(explicit).expanduser()
        return path if path.is_file() else None
    if env_var:
        raw = os.environ.get(env_var, "").strip()
        if raw:
            path = Path(raw).expanduser()
            return path if path.is_file() else None
    return find_executable(name)


def minimal_child_environment(
    extras: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Pass only process-launch essentials plus explicitly scoped roots."""

    child: dict[str, str] = {}
    for name in (
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SystemRoot",
        "WINDIR",
        "COMSPEC",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
    ):
        value = os.environ.get(name)
        if isinstance(value, str) and value:
            child[name] = value
    if extras:
        for key, value in extras.items():
            if key and value:
                child[str(key)] = str(value)
    return child


def run_bounded_command(
    argv: list[str],
    *,
    timeout_seconds: float = DEFAULT_COLLECTOR_TIMEOUT_SECONDS,
    max_output_bytes: int = DEFAULT_MAX_JSON_BYTES,
    env: Mapping[str, str] | None = None,
    runner: _Runner | None = None,
) -> BoundedCommandResult:
    """Run ``argv`` without a shell and bound time plus stdout size."""

    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if max_output_bytes < 1:
        raise ValueError("max_output_bytes must be at least 1")
    if not argv or not argv[0]:
        return BoundedCommandResult(issue_code="missing_executable")
    child_env = minimal_child_environment() if env is None else dict(env)
    if runner is not None:
        return _run_injected_command(
            runner,
            argv,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
            env=child_env,
        )
    return _run_popen_command(
        argv,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
        env=child_env,
    )


def _run_popen_command(
    argv: list[str],
    *,
    timeout_seconds: float,
    max_output_bytes: int,
    env: Mapping[str, str],
) -> BoundedCommandResult:
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(env),
            shell=False,
            bufsize=0,
        )
    except OSError:
        return BoundedCommandResult(issue_code="command_failed")
    assert process.stdout is not None
    output = bytearray()
    oversized = threading.Event()
    read_failed = threading.Event()

    def drain_stdout() -> None:
        try:
            while chunk := process.stdout.read(64 * 1024):
                remaining = max_output_bytes + 1 - len(output)
                if remaining > 0:
                    output.extend(chunk[:remaining])
                if len(output) > max_output_bytes or len(chunk) > remaining:
                    oversized.set()
                    try:
                        process.kill()
                    except OSError:
                        pass
                    break
        except OSError:
            read_failed.set()
        finally:
            try:
                process.stdout.close()
            except OSError:
                pass

    reader = threading.Thread(target=drain_stdout, daemon=True)
    reader.start()
    try:
        returncode = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        process.wait()
        reader.join(timeout=1.0)
        return BoundedCommandResult(issue_code="command_timeout")
    reader.join(timeout=1.0)
    if reader.is_alive() or read_failed.is_set():
        try:
            process.kill()
        except OSError:
            pass
        return BoundedCommandResult(issue_code="command_failed")
    if oversized.is_set():
        return BoundedCommandResult(issue_code="output_too_large")
    return BoundedCommandResult(returncode=returncode, stdout=bytes(output))


def _run_injected_command(
    runner: _Runner,
    argv: list[str],
    *,
    timeout_seconds: float,
    max_output_bytes: int,
    env: Mapping[str, str],
) -> BoundedCommandResult:
    """Compatibility seam for deterministic tests; production uses Popen."""

    try:
        completed = runner(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=False,
            check=False,
            timeout=timeout_seconds,
            env=dict(env),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return BoundedCommandResult(issue_code="command_timeout")
    except OSError:
        return BoundedCommandResult(issue_code="command_failed")
    raw_stdout = completed.stdout or b""
    stdout = (
        raw_stdout.encode("utf-8", errors="replace")
        if isinstance(raw_stdout, str)
        else bytes(raw_stdout)
    )
    if len(stdout) > max_output_bytes:
        return BoundedCommandResult(issue_code="output_too_large")
    return BoundedCommandResult(returncode=completed.returncode, stdout=stdout)


def decode_json_bytes(raw: bytes, *, max_output_bytes: int) -> Any:
    """Decode a bounded JSON payload; never retain prompt/response bodies."""

    if len(raw) > max_output_bytes:
        raise ValueError("output_too_large")
    text = raw.decode("utf-8")
    if len(text) > max_output_bytes:
        raise ValueError("output_too_large")
    import json

    return json.loads(text)


__all__ = [
    "DEFAULT_COLLECTOR_TIMEOUT_SECONDS",
    "DEFAULT_MAX_JSON_BYTES",
    "DEFAULT_MAX_RECORDS",
    "BoundedCommandResult",
    "decode_json_bytes",
    "minimal_child_environment",
    "resolve_optional_executable",
    "run_bounded_command",
]
