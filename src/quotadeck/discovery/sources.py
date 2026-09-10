from __future__ import annotations


PROVIDER_CLI_LABELS = {
    "codex": "Codex CLI",
    "cursor": "Cursor CLI",
    "claude": "Claude Code",
    "grok": "Grok CLI",
}

PROVIDER_APP_LABELS = {
    "cursor": "Cursor App",
    "claude": "Claude App",
    "codex": "Codex App",
    "grok": "Grok App",
}

def classify_source(path: str, provider: str = "") -> tuple[str, str]:
    """Return (kind, label) for a credential path. kind is cli or app."""
    raw = (path or "").replace("\\", "/").lower()
    if "state.vscdb" in raw or "/cursor/user/globalstorage" in raw:
        return "app", PROVIDER_APP_LABELS.get(provider, "App")
    if raw.endswith("/cursor/auth.json") or "/cursor/auth.json" in raw:
        return "cli", PROVIDER_CLI_LABELS.get(provider or "cursor", "CLI")
    if "claude" in raw and ("appdata" in raw or "application support" in raw) and ".claude" not in raw:
        return "app", PROVIDER_APP_LABELS.get(provider or "claude", "App")
    if provider in PROVIDER_CLI_LABELS:
        return "cli", PROVIDER_CLI_LABELS[provider]
    return "cli", "CLI"

def annotate_source(account) -> None:
    if account.source_label:
        return
    kind, label = classify_source(account.source_path, account.provider)
    account.source_kind = kind
    account.source_label = label
