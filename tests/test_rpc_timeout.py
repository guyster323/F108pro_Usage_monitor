from __future__ import annotations

import subprocess
import sys
import time

import pytest

from quotadeck.providers.codex.appserver_rpc import CodexRPCError, _rpc, _StdoutReader


def _spawn(code: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-u", "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _cleanup(proc: subprocess.Popen[str], reader: _StdoutReader) -> None:
    proc.kill()
    proc.wait(timeout=5)
    reader.close()


def test_rpc_timeout_enforced_on_silent_child() -> None:
    proc = _spawn("import time; time.sleep(10)")
    reader = _StdoutReader(proc)
    try:
        start = time.monotonic()
        with pytest.raises(CodexRPCError, match="timeout"):
            _rpc(reader, proc, {"id": 1, "method": "x", "params": {}}, 0.3)
        assert time.monotonic() - start < 2.0
    finally:
        _cleanup(proc, reader)


def test_rpc_timeout_enforced_on_garbage_output() -> None:
    proc = _spawn(
        "import sys, time\n"
        "for _ in range(5):\n"
        "    sys.stdout.write('not json\\n\\n')\n"
        "    sys.stdout.flush()\n"
        "time.sleep(10)\n"
    )
    reader = _StdoutReader(proc)
    try:
        start = time.monotonic()
        with pytest.raises(CodexRPCError, match="timeout"):
            _rpc(reader, proc, {"id": 1, "method": "x", "params": {}}, 0.3)
        assert time.monotonic() - start < 2.0
    finally:
        _cleanup(proc, reader)


def test_rpc_reports_closed_stream_on_eof() -> None:
    proc = _spawn("pass")
    reader = _StdoutReader(proc)
    try:
        with pytest.raises(CodexRPCError, match="closed"):
            _rpc(reader, proc, {"id": 1, "method": "x", "params": {}}, 2.0)
    finally:
        _cleanup(proc, reader)


def test_rpc_returns_matching_response() -> None:
    proc = _spawn(
        "import sys, json\n"
        "line = sys.stdin.readline()\n"
        "req = json.loads(line)\n"
        "sys.stdout.write('\\n')\n"
        "sys.stdout.write('garbage\\n')\n"
        "sys.stdout.write(json.dumps({'id': 999, 'result': {'wrong': True}}) + '\\n')\n"
        "sys.stdout.write(json.dumps({'id': req['id'], 'result': {'ok': True}}) + '\\n')\n"
        "sys.stdout.flush()\n"
    )
    reader = _StdoutReader(proc)
    try:
        result = _rpc(reader, proc, {"id": 7, "method": "x", "params": {}}, 5.0)
        assert result == {"ok": True}
    finally:
        _cleanup(proc, reader)
