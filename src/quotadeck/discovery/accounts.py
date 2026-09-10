from __future__ import annotations

from quotadeck.config import AppConfig
from quotadeck.core.models import AccountRef
from quotadeck.discovery.sources import annotate_source
from quotadeck.providers.base import all_providers


def discover_accounts() -> list[AccountRef]:
    accounts: list[AccountRef] = []
    for provider in all_providers():
        accounts.extend(provider.discover())
    for account in accounts:
        annotate_source(account)
    return accounts

def select_accounts(discovered: list[AccountRef], config: AppConfig) -> list[AccountRef]:
    """Keep only enabled config accounts. Empty config means first-run: use all."""
    if not config.accounts:
        return list(discovered)
    wanted = {(item.provider, item.account_id): item for item in config.accounts if item.enabled}
    order = [(item.provider, item.account_id) for item in config.accounts if item.enabled]
    selected: list[AccountRef] = []
    for account in discovered:
        cfg = wanted.get((account.provider, account.account_id))
        if cfg is None:
            continue
        account.display_name = cfg.alias or account.display_name
        if cfg.source_path:
            account.source_path = cfg.source_path
        if cfg.source_kind:
            account.source_kind = cfg.source_kind
        if cfg.source_label:
            account.source_label = cfg.source_label
        selected.append(account)
    selected.sort(
        key=lambda item: order.index((item.provider, item.account_id)) if (item.provider, item.account_id) in order else 99
    )
    return selected
