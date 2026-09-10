from __future__ import annotations

import re

_TOKENISH = re.compile(
    r"(Bearer\s+)?[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
    r"|sk-[A-Za-z0-9\-_]{8,}"
    r"|WorkosCursorSessionToken=[^\s;]+",
    re.IGNORECASE,
)


def mask_secret(value: str | None, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return f"…{value[-keep:]}"

def mask_text(text: str) -> str:
    def _repl(match: re.Match[str]) -> str:
        raw = match.group(0)
        return mask_secret(raw)

    return _TOKENISH.sub(_repl, text)


def email_local(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    return email.split("@", 1)[0]
