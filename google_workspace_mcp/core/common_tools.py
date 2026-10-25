"""Tools present in every server: list_accounts, auth_status, whoami.

These are credential/account utilities that do not depend on a specific Google
service, so they are identical across all five servers.
"""

from __future__ import annotations

from typing import Optional

import google_auth_core as core
from google_auth_core.diagnostics import feature_login_fix

from . import scope
from .runtime import register


def _health(account: str) -> dict:
    """Token health plus any features (e.g. contacts) the login never granted.

    check_token_health calls an older token valid because everything else still
    works, so without this the agent can't tell contact search needs a new login.
    """
    health = core.check_token_health(account, "unified", core.ALL_SCOPES)
    missing = core.token_missing_features(account)
    if missing:
        health = {
            **health,
            "missing_features": sorted(missing),
            "fix": feature_login_fix(account, missing),
        }
    return health


def register_common_tools(mcp, service_name: Optional[str] = None) -> None:
    @register(mcp, core=True)
    def list_accounts() -> dict:
        """List configured Google accounts, the default account, and aliases."""
        accounts = scope.filter_accounts(core.list_accounts())
        default = core.get_default_account(service_name)
        aliases = core.get_account_aliases()
        if scope.get() is not None:
            # Under a client scope, show only what the scope lets a call reach.
            default = default if default and scope.filter_accounts([default]) else None
            aliases = {k: v for k, v in aliases.items() if scope.filter_accounts([v])}
        return {
            "ok": True,
            "data": {"accounts": accounts, "default": default, "aliases": aliases},
        }

    @register(mcp, core=True)
    def auth_status(account: Optional[str] = None) -> dict:
        """Report token health for one account, or all accounts if omitted."""
        if account:
            accounts = [core.resolve_account(account)]
            scope.check_account(accounts[0])
        else:
            accounts = scope.filter_accounts(core.list_accounts())
        statuses = {a: _health(a) for a in accounts}
        return {"ok": True, "data": statuses}

    @register(mcp, core=True)
    def whoami(account: Optional[str] = None) -> dict:
        """Resolve an account or alias and report its credential status.

        Does not call any Google API; use service-specific tools for live data.
        """
        try:
            _creds, resolved = core.get_credentials(account, service_name=service_name)
            scope.check_account(resolved)
        except core.AuthError as e:
            resolved = (
                core.resolve_account(account)
                if account
                else core.get_default_account(service_name)
            )
            if resolved:
                scope.check_account(resolved)
            return {"ok": False, "account": resolved, "data": {"error": str(e)}}
        health = _health(resolved)
        return {
            "ok": True,
            "account": resolved,
            "data": {"account": resolved, "health": health},
        }
