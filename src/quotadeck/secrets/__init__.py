"""Secret storage for optional administrator credentials.

API keys are never written to ``config.json`` or application logs. On Windows
the default backend is Credential Manager. Tests inject an in-memory store.
"""

from quotadeck.secrets.store import (
    CURSOR_ADMIN_SECRET_TARGET,
    MemorySecretStore,
    SecretStore,
    WindowsCredentialStore,
    default_secret_store,
    delete_cursor_admin_key,
    load_cursor_admin_key,
    store_cursor_admin_key,
)

__all__ = [
    "CURSOR_ADMIN_SECRET_TARGET",
    "MemorySecretStore",
    "SecretStore",
    "WindowsCredentialStore",
    "default_secret_store",
    "delete_cursor_admin_key",
    "load_cursor_admin_key",
    "store_cursor_admin_key",
]
