"""Run one read call across several accounts and merge the results.

Fan-out tools answer questions such as "search all my inboxes" in one call.
Each account goes through ``get_api`` and ``run_tool`` like any single-account
tool, so caching, per-client locking, retries and error mapping are unchanged.
Accounts run one after another inside the tool's worker thread, in sorted
order, so two fan-out calls always take client locks in the same order and
cannot deadlock. A failing account is reported under ``errors`` and does not
fail the whole call.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

import google_auth_core as core

from . import scope
from .runtime import get_api, run_tool


def target_accounts(accounts: Optional[list[str]]) -> list[str]:
    """Resolve ``accounts`` (emails or aliases), or every configured account.

    Under a client scope with ``accounts``, the default list holds only the
    scoped accounts; an explicitly named account outside it stays in the list
    so ``get_api`` refuses it and the caller reports why.
    """
    names = accounts if accounts else core.list_accounts()
    resolved = set()
    for name in names:
        try:
            resolved.add(core.resolve_account(name))
        except Exception:  # noqa: BLE001 - get_api reports the unknown account
            resolved.add(name)
    if not accounts:
        return sorted(scope.filter_accounts(resolved))
    return sorted(resolved)


def fan_out(
    service_name: str,
    factory: Callable[[Optional[str]], Any],
    accounts: Optional[list[str]],
    call: Callable[[Any], Any],
) -> tuple[dict, dict]:
    """Run ``call(api)`` once per account.

    Returns ``(results, errors)``: results maps each resolved account to what
    ``call`` returned, errors maps each failed account to its message.
    """
    results: dict = {}
    errors: dict = {}
    for name in target_accounts(accounts):
        try:
            api, resolved = get_api(service_name, factory, name)
            results[resolved] = run_tool(lambda: call(api))
        except core.GoogleCoreError as e:
            errors[name] = str(e)
    return results, errors


def merged(items: list, results: dict, errors: dict, **meta: Any) -> dict:
    """Envelope for a fan-out tool: ``ok`` is False only when every account failed."""
    out = {
        "ok": bool(results) or not errors,
        "accounts": sorted([*results, *errors]),
        "data": items,
    }
    if errors:
        out["errors"] = errors
    out.update({k: v for k, v in meta.items() if v is not None})
    return out


def tag(account: str, items: list) -> list:
    """Copy each item dict with an ``account`` key, so merged lists stay traceable."""
    return [{**item, "account": account} for item in items]
