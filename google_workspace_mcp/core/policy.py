"""Safety policy for tools that change data or reach people outside the account.

An agent reads untrusted content (an email, a shared doc) and may act on
instructions hidden in it. These checks limit what such an agent can do with a
mutating tool. Every setting is an env var read at call time, so tests can
change it with ``monkeypatch.setenv``.

- ``GOOGLE_MCP_REQUIRE_ACCOUNT``: ``auto`` (default) makes mutating tools
  require an explicit ``account`` when more than one account is configured, so
  an agent never sends from the default account by accident. ``1`` always
  requires it; ``0`` never does.
- ``GOOGLE_MCP_READONLY_ACCOUNTS``: comma-separated emails or aliases whose
  mutating calls are refused, while other accounts stay writable.
- ``GOOGLE_MCP_ALLOWED_RECIPIENTS``: comma-separated addresses or domains
  (``bob@x.com``, ``x.com``, ``@x.com``). When set, mutating tools refuse any
  recipient, attendee, share target, or filter forward address outside it.
  Tools whose recipients are implicit (a reply, a saved draft) and "anyone"
  links are refused too, unless ``GOOGLE_MCP_ALLOW_IMPLICIT_RECIPIENTS=1``
  (replies and drafts only; "anyone" links stay refused).
- ``GOOGLE_MCP_AUDIT_LOG``: path of a JSON-lines file; every mutating call is
  appended with time, tool, account, arguments (long strings cut), the client
  scope it ran under (see ``core/scope.py``), and outcome.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import sys
import threading
from typing import Any, Iterable, Optional

import google_auth_core as core

# Arguments that name people a mutating tool will contact or grant access to.
_RECIPIENT_ARGS = ("to", "cc", "bcc", "additional_cc", "email", "attendees", "attendee_emails")
# Tools that contact people the call does not name (the thread's sender, a
# draft's saved To/Cc), so an allowlist cannot check them.
_IMPLICIT_RECIPIENT_TOOLS = frozenset({"reply_to_message", "send_draft"})
_EMAIL = re.compile(r"[^\s<>,;\"'()]+@[^\s<>,;\"'()]+")
_AUDIT_MAX_STR = 200
_audit_lock = threading.Lock()


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str) -> list[str]:
    return [p.strip() for p in os.getenv(name, "").split(",") if p.strip()]


def _deny(message: str) -> None:
    raise core.InvalidArgumentError(f"Blocked by policy: {message}")


# ---------------------------------------------------------------------------
# Account checks (called from get_api once the account is resolved)
# ---------------------------------------------------------------------------

def _account_required() -> bool:
    mode = os.getenv("GOOGLE_MCP_REQUIRE_ACCOUNT", "auto").strip().lower()
    if mode in ("1", "true", "yes", "on"):
        return True
    if mode in ("0", "false", "no", "off"):
        return False
    try:
        return len(core.list_accounts()) > 1
    except Exception:  # noqa: BLE001 - an unreadable store should not block reads
        return False


def _readonly_accounts() -> set[str]:
    out = set()
    for entry in _env_list("GOOGLE_MCP_READONLY_ACCOUNTS"):
        out.add(entry.casefold())
        try:
            out.add(core.resolve_account(entry).casefold())
        except Exception:  # noqa: BLE001 - unknown alias: keep the literal entry
            pass
    return out


def check_account(account: Optional[str], resolved: str) -> None:
    """Refuse a mutating call that names no account when one is required, or
    that targets an account listed as read-only."""
    if account is None and _account_required():
        _deny(
            "this tool changes data and several accounts are configured; "
            "pass account explicitly (see list_accounts)"
        )
    if resolved.casefold() in _readonly_accounts():
        _deny(f"account {resolved} is read-only (GOOGLE_MCP_READONLY_ACCOUNTS)")


# ---------------------------------------------------------------------------
# Recipient checks (called before a mutating tool runs)
# ---------------------------------------------------------------------------

def _addresses(value: Any) -> list[str]:
    """Email addresses in a recipient argument; refuse parts that hold none."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [a for v in value for a in _addresses(v)]
    out = []
    for part in re.split(r"[,;]", str(value)):
        part = part.strip()
        if not part:
            continue
        found = _EMAIL.findall(part)
        if not found:
            _deny(f"{part!r} is not an email address")
        out.extend(found)
    return out


def _allowed(address: str, allowlist: Iterable[str]) -> bool:
    address = address.casefold()
    domain = address.rsplit("@", 1)[-1]
    for entry in allowlist:
        entry = entry.casefold()
        if "@" in entry and not entry.startswith("@"):
            if address == entry:
                return True
            continue
        d = entry.lstrip("@")
        if domain == d or domain.endswith("." + d):
            return True
    return False


def recipients(tool: str, kwargs: dict) -> list[str]:
    """Every address a call to ``tool`` with ``kwargs`` names as a recipient."""
    found = []
    for key in _RECIPIENT_ARGS:
        found.extend(_addresses(kwargs.get(key)))
    action = kwargs.get("action")
    if isinstance(action, dict):
        found.extend(_addresses(action.get("forward")))
    return found


def check_recipients(tool: str, kwargs: dict) -> None:
    allowlist = _env_list("GOOGLE_MCP_ALLOWED_RECIPIENTS")
    if not allowlist:
        return
    if kwargs.get("anyone"):
        _deny("'anyone' links are not allowed while GOOGLE_MCP_ALLOWED_RECIPIENTS is set")
    if tool in _IMPLICIT_RECIPIENT_TOOLS and not _flag("GOOGLE_MCP_ALLOW_IMPLICIT_RECIPIENTS"):
        _deny(
            f"{tool} sends to recipients the call does not name, which "
            "GOOGLE_MCP_ALLOWED_RECIPIENTS cannot check; set "
            "GOOGLE_MCP_ALLOW_IMPLICIT_RECIPIENTS=1 to allow it"
        )
    blocked = sorted({a for a in recipients(tool, kwargs) if not _allowed(a, allowlist)})
    if blocked:
        _deny(f"recipients outside GOOGLE_MCP_ALLOWED_RECIPIENTS: {', '.join(blocked)}")


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _AUDIT_MAX_STR else value[:_AUDIT_MAX_STR] + "…"
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def audit(
    tool: str,
    kwargs: dict,
    *,
    account: Optional[str],
    error: Optional[BaseException],
    scope: Optional[dict] = None,
) -> None:
    """Append one JSON line per mutating call to ``GOOGLE_MCP_AUDIT_LOG``.

    ``scope`` is the client scope the call ran under (``null`` without one).
    """
    path = os.getenv("GOOGLE_MCP_AUDIT_LOG", "").strip()
    if not path:
        return
    entry = {
        "time": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "tool": tool,
        "account": account,
        "args": _redact({k: v for k, v in kwargs.items() if k != "account"}),
        "scope": scope,
        "ok": error is None,
    }
    if error is not None:
        entry["error"] = _redact(str(error))
    try:
        with _audit_lock:
            fd = os.open(os.path.expanduser(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")
    except OSError as e:
        # stderr, not stdout: stdout carries the stdio MCP transport.
        print(f"GOOGLE_MCP_AUDIT_LOG write failed: {e}", file=sys.stderr)
