from __future__ import annotations

import sys


def _run_key():
    if sys.platform != "win32":
        return None
    import winreg

    return winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "QuotaDeck"


def set_launch_at_startup(enabled: bool) -> None:
    key_info = _run_key()
    if key_info is None:
        return
    import winreg
    hive, path, name = key_info
    key = winreg.OpenKey(hive, path, 0, winreg.KEY_SET_VALUE)
    try:
        if enabled:
            if getattr(sys, "frozen", False):
                command = f'"{sys.executable}"'
            else:
                command = f'"{sys.executable}" -m quotadeck ui'
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
    finally:
        winreg.CloseKey(key)
