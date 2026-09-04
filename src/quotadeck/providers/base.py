from __future__ import annotations

from abc import ABC, abstractmethod

from quotadeck.core.models import AccountRef, UsageSnapshot


class Provider(ABC):
    id: str

    @abstractmethod
    def discover(self) -> list[AccountRef]:
        raise NotImplementedError

    @abstractmethod
    def fetch(self, account: AccountRef) -> UsageSnapshot:
        raise NotImplementedError


def all_providers() -> list[Provider]:
    from quotadeck.providers.claude.provider import ClaudeProvider
    from quotadeck.providers.codex.provider import CodexProvider
    from quotadeck.providers.cursor.provider import CursorProvider
    from quotadeck.providers.grok.provider import GrokProvider

    return [CodexProvider(), CursorProvider(), ClaudeProvider(), GrokProvider()]
