from __future__ import annotations

import os
import shutil
from pathlib import Path


def find_executable(name: str) -> Path | None:
    """Locate a CLI, preferring .cmd/.exe over PowerShell shims on Windows."""
    found = shutil.which(name)
    if found:
        path = Path(found)
        if path.suffix.lower() == ".ps1":
            sibling = path.with_suffix(".cmd")
            if sibling.exists():
                return sibling
            exe = path.with_suffix(".exe")
            if exe.exists():
                return exe
        return path
    extras: list[Path] = []
    if os.name == "nt":
        roaming = Path(os.environ.get("APPDATA", ""))
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        extras.extend(
            [
                roaming / "npm" / f"{name}.cmd",
                roaming / "npm" / f"{name}.exe",
                local / "Programs" / name / f"{name}.exe",
                local / "cursor-agent" / f"{name}.cmd",
            ]
        )
    for candidate in extras:
        if candidate.exists():
            return candidate
    return None
