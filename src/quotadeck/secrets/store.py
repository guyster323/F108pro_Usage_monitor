"""Windows user-store for the optional Cursor Admin API key.

The key is stored only in Windows Credential Manager (or an injected test
store). Callers must never write it to config, cache JSON, or log records.
"""

from __future__ import annotations

import os
from typing import Protocol

CURSOR_ADMIN_SECRET_TARGET = "QuotaDeck/CursorAdminAPI"


class SecretStore(Protocol):
    def set(self, target: str, secret: str) -> None: ...

    def get(self, target: str) -> str | None: ...

    def delete(self, target: str) -> None: ...


class MemorySecretStore:
    """Process-local store for tests. Never used as a production persistence path."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def set(self, target: str, secret: str) -> None:
        name = target.strip()
        value = secret.strip()
        if not name or not value:
            raise ValueError("Secret target and value are required.")
        self._values[name] = value

    def get(self, target: str) -> str | None:
        return self._values.get(target.strip())

    def delete(self, target: str) -> None:
        self._values.pop(target.strip(), None)


class WindowsCredentialStore:
    """Current-user Windows Credential Manager (CRED_TYPE_GENERIC)."""

    _CRED_TYPE_GENERIC = 1
    _CRED_PERSIST_LOCAL_MACHINE = 2

    def set(self, target: str, secret: str) -> None:
        self._require_windows()
        name = target.strip()
        value = secret.strip()
        if not name or not value:
            raise ValueError("Secret target and value are required.")
        blob = value.encode("utf-8")
        advapi32, credential_type = self._api()
        cred = credential_type()
        cred.Flags = 0
        cred.Type = self._CRED_TYPE_GENERIC
        cred.TargetName = name
        cred.Comment = "QuotaDeck Cursor Admin API"
        cred.CredentialBlobSize = len(blob)
        cred.CredentialBlob = self._blob_pointer(blob)
        cred.Persist = self._CRED_PERSIST_LOCAL_MACHINE
        cred.AttributeCount = 0
        cred.Attributes = None
        cred.TargetAlias = None
        cred.UserName = "QuotaDeck"
        if not advapi32.CredWriteW(self._byref(cred), 0):
            raise OSError(self._last_error())

    def get(self, target: str) -> str | None:
        self._require_windows()
        name = target.strip()
        if not name:
            return None
        import ctypes

        advapi32, credential_type = self._api()
        pointer = ctypes.POINTER(credential_type)()
        if not advapi32.CredReadW(name, self._CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            return None
        try:
            cred = pointer.contents
            size = int(cred.CredentialBlobSize)
            if size <= 0 or not cred.CredentialBlob:
                return None
            data = self._read_blob(cred.CredentialBlob, size)
            return data.decode("utf-8").strip() or None
        finally:
            advapi32.CredFree(pointer)

    def delete(self, target: str) -> None:
        self._require_windows()
        name = target.strip()
        if not name:
            return
        advapi32, _credential_type = self._api()
        advapi32.CredDeleteW(name, self._CRED_TYPE_GENERIC, 0)

    @staticmethod
    def _require_windows() -> None:
        if os.name != "nt":
            raise OSError("Windows Credential Manager is only available on Windows.")

    @staticmethod
    def _api():
        import ctypes
        from ctypes import wintypes

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", wintypes.FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.c_void_p),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
        advapi32.CredWriteW.restype = wintypes.BOOL
        advapi32.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(CREDENTIAL)),
        ]
        advapi32.CredReadW.restype = wintypes.BOOL
        advapi32.CredFree.argtypes = [ctypes.c_void_p]
        advapi32.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        advapi32.CredDeleteW.restype = wintypes.BOOL
        return advapi32, CREDENTIAL

    @staticmethod
    def _blob_pointer(blob: bytes):
        import ctypes

        buffer = ctypes.create_string_buffer(blob, len(blob))
        WindowsCredentialStore._keep_alive = buffer
        return ctypes.cast(buffer, ctypes.c_void_p)

    @staticmethod
    def _read_blob(pointer: object, size: int) -> bytes:
        import ctypes

        return ctypes.string_at(pointer, size)

    @staticmethod
    def _byref(value: object):
        import ctypes

        return ctypes.byref(value)

    @staticmethod
    def _last_error() -> int:
        import ctypes

        return ctypes.get_last_error()


_DEFAULT_STORE: SecretStore | None = None


def default_secret_store() -> SecretStore:
    """Return the process default store. Tests should inject their own store."""

    global _DEFAULT_STORE
    if _DEFAULT_STORE is not None:
        return _DEFAULT_STORE
    if os.name == "nt":
        return WindowsCredentialStore()
    return MemorySecretStore()


def set_default_secret_store(store: SecretStore | None) -> None:
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


def store_cursor_admin_key(secret: str, *, store: SecretStore | None = None) -> None:
    (store or default_secret_store()).set(CURSOR_ADMIN_SECRET_TARGET, secret)


def load_cursor_admin_key(*, store: SecretStore | None = None) -> str | None:
    return (store or default_secret_store()).get(CURSOR_ADMIN_SECRET_TARGET)


def delete_cursor_admin_key(*, store: SecretStore | None = None) -> None:
    (store or default_secret_store()).delete(CURSOR_ADMIN_SECRET_TARGET)
