"""Client scope: limit one tool call to some accounts and some Drive folders.

A client attaches the scope to a call in the request's ``_meta`` under
:data:`SCOPE_KEY`; the model writes tool arguments but never ``_meta``, so an
argument cannot reach outside it::

    {"accounts": ["work"],
     "drive_folders": [{"account": "me@work.com", "id": "<folder id>"}]}

``WorkspaceServer.call_tool`` parses it and sets :data:`current` for the call.
``anyio.to_thread`` copies context variables into the tool's worker thread, so
``get_api`` (account check) and the ``register`` wrapper (folder check) see it.

The folder check walks a file's parents up to a scope folder or a root. Parents
are cached per account for :data:`PARENT_TTL` seconds, at most
:data:`PARENT_MAX` entries, and dropped for any file this server moves or trashes.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional

import google_auth_core as core

SCOPE_KEY = "io.github.outcastofmusic.workspace/scope"

# How far up the parent chain the folder check walks before giving up.
MAX_DEPTH = 25
PARENT_TTL = 600.0
PARENT_MAX = 5000


@dataclass(frozen=True)
class Folder:
    account: str  # resolved email
    id: str


@dataclass(frozen=True)
class Scope:
    """A parsed scope. ``None`` for a part means that part does not limit."""

    accounts: Optional[tuple[str, ...]] = None  # resolved emails
    folders: Optional[tuple[Folder, ...]] = None

    def allows_account(self, resolved: str) -> bool:
        if self.accounts is None:
            return True
        return resolved.casefold() in {a.casefold() for a in self.accounts}

    def folder_ids(self, resolved: str) -> frozenset[str]:
        """Scope folders that belong to ``resolved``."""
        return frozenset(
            f.id for f in self.folders or () if f.account.casefold() == resolved.casefold()
        )

    def to_dict(self) -> dict:
        """The scope as recorded in the audit log."""
        return {
            "accounts": list(self.accounts) if self.accounts is not None else None,
            "drive_folders": (
                [{"account": f.account, "id": f.id} for f in self.folders]
                if self.folders is not None
                else None
            ),
        }


current: ContextVar[Optional[Scope]] = ContextVar("client_scope", default=None)


def get() -> Optional[Scope]:
    return current.get()


def required() -> bool:
    """True when ``GOOGLE_MCP_REQUIRE_SCOPE`` makes every call need a scope."""
    value = os.getenv("GOOGLE_MCP_REQUIRE_SCOPE", "").strip().lower()
    return value in ("1", "true", "yes", "on")


def _invalid(message: str) -> None:
    raise core.InvalidArgumentError(f"Invalid scope in _meta[{SCOPE_KEY!r}]: {message}")


def outside(message: str) -> None:
    raise core.InvalidArgumentError(f"Outside scope: {message}")


def _resolve(name: str) -> str:
    try:
        return core.resolve_account(name)
    except Exception:  # noqa: BLE001 - an unknown alias stays literal and matches nothing
        return name


def _string(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _invalid(f"{where} must be a non-empty string")
    return value.strip()


def parse(value: Any) -> Scope:
    """Validate the ``_meta`` scope object and resolve its account aliases."""
    if not isinstance(value, Mapping):
        _invalid("must be an object with 'accounts' and/or 'drive_folders'")
    unknown = sorted(set(value) - {"accounts", "drive_folders"})
    if unknown:
        _invalid(f"unknown key(s) {', '.join(unknown)}; allowed: accounts, drive_folders")

    accounts = None
    if value.get("accounts") is not None:
        raw = value["accounts"]
        if not isinstance(raw, list) or not raw:
            _invalid("accounts must be a non-empty list of emails or aliases")
        accounts = tuple(
            dict.fromkeys(_resolve(_string(a, f"accounts[{i}]")) for i, a in enumerate(raw))
        )

    folders = None
    if value.get("drive_folders") is not None:
        raw = value["drive_folders"]
        if not isinstance(raw, list) or not raw:
            _invalid("drive_folders must be a non-empty list of {account, id} objects")
        out = []
        for i, entry in enumerate(raw):
            if not isinstance(entry, Mapping) or set(entry) != {"account", "id"}:
                _invalid(f"drive_folders[{i}] must be an object with exactly 'account' and 'id'")
            out.append(Folder(
                account=_resolve(_string(entry["account"], f"drive_folders[{i}].account")),
                id=_string(entry["id"], f"drive_folders[{i}].id"),
            ))
        folders = tuple(dict.fromkeys(out))

    return Scope(accounts=accounts, folders=folders)


def from_meta(meta: Optional[Mapping]) -> Optional[Scope]:
    """The scope a request's ``_meta`` carries, or None when it has none."""
    if not meta or SCOPE_KEY not in meta:
        return None
    return parse(meta[SCOPE_KEY])


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def check_account(resolved: str) -> None:
    """Refuse ``resolved`` when the current scope does not list it."""
    sc = current.get()
    if sc is not None and not sc.allows_account(resolved):
        outside(f"account {resolved}")


def filter_accounts(names: Iterable[str]) -> list[str]:
    """Keep the resolved account names the current scope allows."""
    sc = current.get()
    return [n for n in names if sc is None or sc.allows_account(n)]


# ---------------------------------------------------------------------------
# Parent cache and ancestry
# ---------------------------------------------------------------------------

_parents: "OrderedDict[tuple[str, str], tuple[float, tuple[str, ...]]]" = OrderedDict()
_parents_lock = threading.Lock()


def remember(account: str, file_id: str, parents: Iterable[str]) -> None:
    with _parents_lock:
        key = (account.casefold(), file_id)
        _parents[key] = (time.monotonic() + PARENT_TTL, tuple(parents))
        _parents.move_to_end(key)
        while len(_parents) > PARENT_MAX:
            _parents.popitem(last=False)


def _cached(account: str, file_id: str) -> Optional[tuple[str, ...]]:
    with _parents_lock:
        key = (account.casefold(), file_id)
        hit = _parents.get(key)
        if hit is None:
            return None
        if hit[0] < time.monotonic():
            del _parents[key]
            return None
        _parents.move_to_end(key)
        return hit[1]


def forget(file_ids: Iterable[str]) -> None:
    """Drop the cached parents of ``file_ids`` in every account."""
    ids = set(file_ids)
    if not ids:
        return
    with _parents_lock:
        for key in [k for k in _parents if k[1] in ids]:
            del _parents[key]


def clear_cache() -> None:
    with _parents_lock:
        _parents.clear()


def in_folders(
    account: str,
    file_id: str,
    folders: frozenset[str],
    lookup: Callable[[str], list[str]],
) -> bool:
    """True when ``file_id`` is one of ``folders`` or lies below one of them.

    ``lookup(id)`` returns the parents Google reports for ``id``; results are
    cached. A file with no parents (a root, or a file only shared with the
    user) ends the walk outside. Loops in parents end it too.
    """
    if not folders:
        return False
    frontier = [file_id]
    seen: set[str] = set()
    for _level in range(MAX_DEPTH + 1):
        nxt: list[str] = []
        for fid in frontier:
            if fid in folders:
                return True
            if fid in seen:
                continue
            seen.add(fid)
            parents = _cached(account, fid)
            if parents is None:
                parents = tuple(lookup(fid) or ())
                remember(account, fid, parents)
            nxt.extend(p for p in parents if p not in seen)
        if not nxt:
            return False
        frontier = nxt
    return False
