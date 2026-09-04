from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

from quotadeck.devices.aula_f108.constants import (
    AULA_PROCESS_NAMES,
    PID,
    USAGE_PAGE_CONFIG,
    USAGE_PAGE_LCD,
    VID,
)
from quotadeck.devices.aula_f108.payload import Frame, build_payload
from quotadeck.devices.aula_f108.protocol import Progress, sync_clock, upload_payload
from quotadeck.devices.aula_f108.transport import Transport


@dataclass
class HidInterface:
    usage_page: int
    usage: int
    product: str
    path: str


def aula_software_running() -> list[str]:
    found: list[str] = []
    if sys.platform != "win32":
        return found
    try:
        import subprocess

        raw = subprocess.check_output(
            ["tasklist", "/FO", "CSV", "/NH"],
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return found
    lower = raw.lower()
    for name in AULA_PROCESS_NAMES:
        if name.lower() in lower:
            found.append(name)
    return found


def enumerate_interfaces() -> list[HidInterface]:
    results: list[HidInterface] = []
    try:
        import hid
    except ImportError:
        return results
    for info in hid.enumerate(VID, PID):
        results.append(
            HidInterface(
                usage_page=int(info.get("usage_page") or 0),
                usage=int(info.get("usage") or 0),
                product=str(info.get("product_string") or "AULA F108 Pro"),
                path=(info.get("path") or b"").decode("utf-8", "replace")
                if isinstance(info.get("path"), bytes)
                else str(info.get("path") or ""),
            )
        )
    return results


def wired_mode_ok(interfaces: list[HidInterface] | None = None) -> bool:
    ifaces = interfaces if interfaces is not None else enumerate_interfaces()
    pages = {item.usage_page for item in ifaces}
    return USAGE_PAGE_CONFIG in pages and USAGE_PAGE_LCD in pages


def open_transport(prefer: str = "auto") -> Transport:
    errors: list[str] = []
    order = ["hidapi", "win32"] if prefer == "auto" else [prefer]
    for name in order:
        try:
            if name == "hidapi":
                from quotadeck.devices.aula_f108.transport_hidapi import HidapiTransport

                return HidapiTransport()
            if name == "win32":
                from quotadeck.devices.aula_f108.transport_win32 import Win32Transport

                return Win32Transport()
            if name == "mock":
                from quotadeck.devices.aula_f108.transport_mock import MockTransport

                return MockTransport()
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError("could not open F108: " + " | ".join(errors))


class F108Device:
    def __init__(self, transport: Transport | None = None, prefer: str = "auto") -> None:
        self.transport = transport or open_transport(prefer)

    def upload_frames(self, frames: list[Frame], progress: Progress | None = None) -> int:
        payload = build_payload(frames)
        upload_payload(self.transport, payload, progress)
        return len(payload)

    def upload_payload(self, payload: bytes, progress: Progress | None = None) -> None:
        upload_payload(self.transport, payload, progress)

    def sync_clock(self):
        return sync_clock(self.transport)

    def close(self) -> None:
        self.transport.close()

    def __enter__(self) -> F108Device:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
