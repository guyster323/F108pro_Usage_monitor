from __future__ import annotations

import os
import re
import unicodedata

_TOKENISH = re.compile(
    r"(Bearer\s+)?[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"
    r"|(?:sk|xai)-[A-Za-z0-9\-_]{8,}"
    r"|WorkosCursorSessionToken=[^\s;]+",
    re.IGNORECASE,
)
_AUTH_HEADER = re.compile(
    r"\b(?P<scheme>Bearer|Basic)\s+(?P<secret>[^\s,;]+)",
    re.IGNORECASE,
)
_SECRET_FIELD = re.compile(
    r"(?P<prefix>[\"']?(?:(?:access|refresh|id|session)?[_-]?token|session|"
    r"password|passwd|secret|api[_-]?key|apikey|x[_-]?api[_-]?key|"
    r"client[_-]?secret|authorization[_-]?code)[\"']?\s*[:=]\s*"
    r"[\"']?)(?P<secret>[^\"'\s,;&}]+)",
    re.IGNORECASE,
)
_COOKIE_HEADER = re.compile(
    r"(?P<prefix>\b(?:cookie|set-cookie)\s*[:=]\s*)(?P<secret>[^\r\n]+)",
    re.IGNORECASE,
)
_EMAIL = re.compile(r"(?<![\w.+-])(?P<local>[A-Z0-9._%+-]+)@(?P<domain>[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.IGNORECASE)


def mask_secret(value: str | None, keep: int = 4) -> str:
    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return f"…{value[-keep:]}"

def mask_text(text: str) -> str:
    def _mask_group(match: re.Match[str]) -> str:
        prefix = match.groupdict().get("prefix")
        if prefix is None:
            prefix = f"{match.groupdict().get('scheme', '')} "
        return f"{prefix}{mask_secret(match.group('secret'))}"

    def _repl(match: re.Match[str]) -> str:
        raw = match.group(0)
        return mask_secret(raw)

    masked = _COOKIE_HEADER.sub(
        lambda match: f"{match.group('prefix')}<redacted>",
        text,
    )
    masked = _AUTH_HEADER.sub(_mask_group, masked)
    masked = _SECRET_FIELD.sub(_mask_group, masked)
    masked = _TOKENISH.sub(_repl, masked)
    masked = _EMAIL.sub(lambda match: f"…@{match.group('domain')}", masked)

    # Tracebacks stay useful while avoiding the local Windows/Linux username.
    homes: list[tuple[str, str]] = []
    for name in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME"):
        value = os.environ.get(name)
        if value:
            homes.append((value, f"%{name}%"))
    for value, replacement in sorted(set(homes), key=lambda item: len(item[0]), reverse=True):
        for candidate in {value, value.replace("\\", "/")}:
            masked = re.sub(
                re.escape(candidate),
                lambda _match, replacement=replacement: replacement,
                masked,
                flags=re.IGNORECASE,
            )
    return masked


def safe_display_text(
    value: object,
    *,
    max_length: int = 160,
    fallback: str = "unknown",
) -> str:
    """Remove terminal controls, bidi overrides, and rich-text delimiters."""

    if not isinstance(value, str) or max_length < 1:
        return fallback
    cleaned: list[str] = []
    # Bound work before whitespace folding; some Unicode characters expand in
    # UI engines even though QuotaDeck never needs an unbounded model label.
    for character in value[: max_length * 8]:
        if character.isspace():
            cleaned.append(" ")
        elif unicodedata.category(character).startswith("C"):
            continue
        elif character == "<":
            cleaned.append("[")
        elif character == ">":
            cleaned.append("]")
        else:
            cleaned.append(character)
    text = " ".join("".join(cleaned).split()).strip()
    return text[:max_length] or fallback


def email_local(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    return email.split("@", 1)[0]
