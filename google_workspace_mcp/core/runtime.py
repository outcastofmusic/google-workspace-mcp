"""Shared runtime: account resolution + warm-client cache, the read-only gate,
error mapping, and the response envelope. Every tool in every server goes
through these helpers, which is what keeps the five servers aligned.
"""

from __future__ import annotations

import contextlib
import functools
import json
import math
import os
import random
import re
import sys
import threading
import time
from contextvars import ContextVar
from typing import Any, Callable, Optional

import anyio.to_thread
import google_auth_core as core
from mcp.types import ToolAnnotations

from . import policy, scope


def _readonly() -> bool:
    return os.getenv("GOOGLE_MCP_READONLY", "").strip().lower() in ("1", "true", "yes", "on")


# Evaluated once at import; servers are launched with the env already set.
READONLY = _readonly()

# "core" registers only tools tagged core=True; anything else means all tools.
# Read once at import, like READONLY.
TOOLSET = os.getenv("GOOGLE_MCP_TOOLSET", "all").strip().lower()
if TOOLSET not in ("all", "core"):
    # stderr, not stdout: stdout carries the stdio MCP transport.
    print(f"GOOGLE_MCP_TOOLSET={TOOLSET!r} is not 'all' or 'core'; registering all tools.",
          file=sys.stderr)

_api_cache: dict = {}
_lock = threading.Lock()

# One lock per cached client. googleapiclient services sit on httplib2, which
# is not thread-safe, so two tool calls must never use the same client at once.
# Locking per (service, account) keeps calls on one account serialized while
# different accounts still run in parallel.
_client_locks: dict = {}
# Locks the current tool call holds, released when the call returns. Only set
# inside a registered tool's worker thread; direct (test) calls take no locks.
_held = threading.local()

# Seconds to wait before each retry when a connection to Google could not be
# opened at all (network blip, Wi-Fi/VPN change, wake from sleep).
_CONNECT_RETRY_DELAYS = (2, 5)

# Rate limits (429) and transient server errors are retried with backoff, but
# only for calls that are safe to repeat: the first request may have gone
# through, so a retried send or append could happen twice.
_TRANSIENT_STATUSES = {500, 502, 503, 504}
_TRANSIENT_MAX_RETRIES = 4
_TRANSIENT_BASE_DELAY = 1.0
_TRANSIENT_MAX_TOTAL_WAIT = 20.0

# Set by the ``register`` wrapper for the duration of a tool call: True for
# read tools and writes marked idempotent. Calls outside a registered tool
# default to False and are never retried on 429/5xx.
_retry_safe: ContextVar[bool] = ContextVar("retry_safe", default=False)
# Set by the ``register`` wrapper: True while a mutating tool runs, so
# ``get_api`` applies the account policy (see ``policy.check_account``).
_mutating: ContextVar[bool] = ContextVar("mutating", default=False)
# Default cap on what a read tool returns inline, roughly 25k tokens. Bigger
# results waste the agent's context or get cut off by the client with no hint
# on how to fetch the rest.
DEFAULT_MAX_CHARS = 100_000


def ok(account: str, data: Any, **meta: Any) -> dict:
    """Standard success envelope shared by all tools.

    Extra keyword args are added to the envelope only when not None: today
    next_page_token, and the truncation keys produced by cap_text/cap_items.
    """
    out = {"ok": True, "account": account, "data": data}
    out.update({k: v for k, v in meta.items() if v is not None})
    return out


def _char_budget(max_chars: Optional[int]) -> int:
    if max_chars is None:
        return DEFAULT_MAX_CHARS
    if max_chars < 1:
        raise core.InvalidArgumentError("max_chars must be at least 1")
    return max_chars


def _check_offset(offset: int) -> None:
    if offset < 0:
        raise core.InvalidArgumentError("offset must be 0 or greater")


def cap_text(
    text: str, max_chars: Optional[int] = None, offset: int = 0, hint: str = ""
) -> tuple[str, dict]:
    """Return ``text[offset:offset + max_chars]`` plus envelope meta for ``ok()``.

    The meta is empty when the whole text fits. Otherwise it carries
    ``truncated`` (more text remains), ``total_chars``, ``offset``, and while
    more remains ``next_offset`` and ``hint`` (how to get the rest).
    """
    limit = _char_budget(max_chars)
    _check_offset(offset)
    chunk = text[offset:offset + limit]
    end = offset + len(chunk)
    more = end < len(text)
    if offset == 0 and not more:
        return chunk, {}
    meta = {"truncated": more, "total_chars": len(text), "offset": offset}
    if more:
        meta.update(next_offset=end, hint=hint)
    return chunk, meta


def cap_items(
    items: list, max_chars: Optional[int] = None, offset: int = 0, hint: str = ""
) -> tuple[list, dict]:
    """Keep whole items from ``offset`` while their JSON size fits ``max_chars``.

    At least one item is always kept so callers paging with ``next_offset``
    make progress. Meta mirrors :func:`cap_text`, counting items instead of
    characters (``total_items``).
    """
    limit = _char_budget(max_chars)
    _check_offset(offset)
    kept, used = [], 0
    for item in items[offset:]:
        size = len(json.dumps(item, default=str))
        if kept and used + size > limit:
            break
        kept.append(item)
        used += size
    end = offset + len(kept)
    more = end < len(items)
    if offset == 0 and not more:
        return kept, {}
    meta = {"truncated": more, "total_items": len(items), "offset": offset}
    if more:
        meta.update(next_offset=end, hint=hint)
    return kept, meta


def _client(
    service_name: str,
    factory: Callable[[Optional[str]], Any],
    account: Optional[str],
):
    """Resolve ``account`` and return ``(instance, resolved, client_lock)``.

    Refuses an account outside the call's client scope before any API client
    is built or used.
    """
    # get_credentials and client factories may refresh an expired token and
    # rewrite its file in place; with tools on worker threads, two calls must
    # not do that at the same time, so both run under the cache lock.
    with _lock:
        _creds, resolved = core.get_credentials(account, service_name=service_name)
        scope.check_account(resolved)
        key = (service_name, resolved)
        inst = _api_cache.get(key)
        if inst is None:
            inst = factory(resolved)
            _api_cache[key] = inst
        client_lock = _client_locks.setdefault(key, threading.Lock())
    return inst, resolved, client_lock


def get_api(
    service_name: str,
    factory: Callable[[Optional[str]], Any],
    account: Optional[str],
):
    """Resolve ``account``, validate credentials, and return a cached API client.

    Raises :class:`google_auth_core.AuthError` with an actionable message when
    credentials are missing or unusable. Instances are cached per
    ``(service_name, resolved_account)`` so a long-lived server reuses warm
    authorized clients across calls, and different accounts use different
    instances and therefore never interfere. An account outside the call's
    client scope is refused (``Outside scope: account ...``).

    Returns ``(api_instance, resolved_account)``.
    """
    inst, resolved, client_lock = _client(service_name, factory, account)
    if _mutating.get():
        policy.check_account(account, resolved)
    held = getattr(_held, "locks", None)
    if held is not None and client_lock not in held:
        client_lock.acquire()
        held.append(client_lock)
    return inst, resolved


@contextlib.contextmanager
def borrow_api(
    service_name: str,
    factory: Callable[[Optional[str]], Any],
    account: Optional[str],
):
    """Like :func:`get_api`, but hold the client's lock only inside the block.

    The scope's folder check uses it before a tool runs, so the Drive lock is
    not kept while the tool takes its own clients; when the tool already holds
    the lock (filtering a Drive tool's results), it is reused as is.
    """
    inst, resolved, client_lock = _client(service_name, factory, account)
    held = getattr(_held, "locks", None)
    if held is None or client_lock in held:
        yield inst, resolved
        return
    with client_lock:
        yield inst, resolved


def reset_api_cache() -> None:
    """Drop all cached API clients (used by tests and after re-auth)."""
    with _lock:
        _api_cache.clear()
        _client_locks.clear()


def cached_keys() -> list:
    """Return the cached (service, account) keys, for tests/inspection."""
    with _lock:
        return sorted(_api_cache.keys())


def _http_status(exc: BaseException) -> Optional[int]:
    """HTTP status of a raw HttpError, a wrapped "HttpError NNN" message, or a
    mapped "(HTTP NNN)" error; None when there is none."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        # Last match: Google's message may quote user text before the real status.
        found = re.findall(r"(?:HttpError\s+|\(HTTP )(\d{3})", str(exc))
        status = found[-1] if found else None
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _transient_delay(err: core.GoogleCoreError, raw: BaseException, attempt: int, waited: float) -> Optional[float]:
    """Seconds to wait before retrying a rate-limited or 5xx call, or None to give up."""
    if not _retry_safe.get() or attempt >= _TRANSIENT_MAX_RETRIES:
        return None
    retry_after = getattr(err, "retry_after", None)
    if isinstance(err, core.RateLimitError) and retry_after is not None and 0 <= retry_after < math.inf:
        delay = retry_after + random.uniform(0, 1)
    elif isinstance(err, core.RateLimitError) or (
        isinstance(err, core.UpstreamError) and _http_status(raw) in _TRANSIENT_STATUSES
    ):
        backoff = _TRANSIENT_BASE_DELAY * 2**attempt
        delay = random.uniform(backoff / 2, backoff)
    else:
        return None
    if waited + delay > _TRANSIENT_MAX_TOTAL_WAIT:
        return None
    return delay


def run_tool(fn: Callable[[], Any]) -> Any:
    """Execute an API call, mapping any failure to the shared error taxonomy.

    Failures to open a connection at all are retried after a short wait. The
    request never reached Google in that case, so retrying is safe even for
    writes such as sending mail.

    Rate limits (429) and transient 5xx errors are retried with jittered
    backoff, honoring Retry-After, up to about 20s in total, but only inside
    tools registered as reads or idempotent writes (see ``register``).
    """
    connect_delays = iter(_CONNECT_RETRY_DELAYS)
    attempt = 0
    waited = 0.0
    while True:
        try:
            return fn()
        except core.GoogleCoreError as e:
            raw = err = e
        except ValueError as e:
            raise core.InvalidArgumentError(str(e)) from e
        except Exception as e:  # noqa: BLE001 - normalize everything to GoogleCoreError
            if core.is_connect_failure(e):
                delay = next(connect_delays, None)
                if delay is not None:
                    time.sleep(delay)
                    continue
            raw, err = e, core.map_exception(e)
        delay = _transient_delay(err, raw, attempt, waited)
        if delay is None:
            if err is raw:
                raise err
            raise err from raw
        time.sleep(delay)
        attempt += 1
        waited += delay


def _retry_scope(fn: Callable, safe: bool) -> Callable:
    """Wrap a tool so ``run_tool`` knows whether its calls may be repeated."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _retry_safe.set(safe)
        try:
            return fn(*args, **kwargs)
        finally:
            _retry_safe.reset(token)

    return wrapper


def _policy_scope(fn: Callable, mutating: bool) -> Callable:
    """Wrap a tool so mutating calls pass the safety policy and get audited.

    Recipient checks run before the tool; account checks run in ``get_api``
    once the account is resolved (``_mutating`` tells it to).
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        token = _mutating.set(mutating)
        result: Any = None
        error: Optional[BaseException] = None
        try:
            if mutating:
                policy.check_recipients(fn.__name__, kwargs)
            result = fn(*args, **kwargs)
            return result
        except BaseException as e:
            error = e
            raise
        finally:
            _mutating.reset(token)
            if mutating:
                account = result.get("account") if isinstance(result, dict) else None
                sc = scope.get()
                policy.audit(
                    fn.__name__, kwargs, account=account or kwargs.get("account"), error=error,
                    scope=sc.to_dict() if sc is not None else None,
                )

    return wrapper


# Parameters through which Drive, Docs, Sheets and Slides tools name files and folders.
# Only tools registered with drive_scoped=True are checked, so a Gmail
# message_id or a Calendar event_id is never read as a Drive file.
SCOPED_FILE_PARAMS = (
    "file_id", "file_ids", "folder_id", "parent_id", "new_parent_id",
    "document_id", "spreadsheet_id", "presentation_id",
)
# Tools that create a file, and the parameter that must name its parent under
# a folder scope (without one the file lands outside the scope).
_CREATE_PARENT = {
    "upload_file": "parent_id",
    "create_folder": "parent_id",
    "copy_file": "parent_id",
    "create_document": "folder_id",
    "create_document_from_markdown": "folder_id",
    "create_spreadsheet": "folder_id",
    "create_presentation": "folder_id",
    "create_presentation_from_outline": "folder_id",
}
# Batch tools that create files from ``items``, and each item's parent key.
_ITEM_CREATE_PARENT = {"batch_create_folders": "parent_id"}
# Tools whose file lists are cut to the scope after the Google call.
_FILTERED_TOOLS = frozenset({"search_files", "list_files", "search_files_all_accounts"})
# Tools that reach beyond any folder by design. list_changes lists the
# changes of the whole Drive, with file names, whatever folder they are in.
_REFUSED_TOOLS = frozenset({"list_drives", "list_changes"})


def _named_files(kwargs: dict) -> list[tuple[str, str]]:
    """``(parameter, id)`` for every file or folder a call names, including
    the same keys inside ``items`` of the batch tools."""
    out: list[tuple[str, str]] = []

    def add(where: str, value: Any) -> None:
        values = value if isinstance(value, (list, tuple)) else [value]
        for v in values:
            if v is not None and v != "":
                out.append((where, str(v)))

    for name in SCOPED_FILE_PARAMS:
        add(name, kwargs.get(name))
    items = kwargs.get("items")
    if isinstance(items, list):
        for i, item in enumerate(items):
            if isinstance(item, dict):
                for name in SCOPED_FILE_PARAMS:
                    add(f"items[{i}].{name}", item.get(name))
    return out


def _drive_client(service: Optional[str], account: Optional[str]):
    """Borrow the Drive client of the account the tool will resolve to."""
    from ..drive import drive_api  # lazy: core must not import a service at load

    name = account if account else core.get_default_account(service)
    return borrow_api("drive", drive_api.DriveAPI, name)


def _parents_lookup(api: Any) -> Callable[[str], list]:
    def lookup(file_id: str) -> list:
        token = _retry_safe.set(True)  # a metadata read, safe to repeat
        try:
            return run_tool(lambda: api.get_parents(file_id))
        finally:
            _retry_safe.reset(token)

    return lookup


def _check_drive_args(tool: str, service: Optional[str], sc: scope.Scope, kwargs: dict) -> None:
    """Refuse a Drive, Docs, Sheets or Slides call that names anything outside the folders."""
    if tool in _REFUSED_TOOLS:
        scope.outside(f"{tool} is not available under a Drive folder scope")
    if kwargs.get("drive_id"):
        scope.outside(
            "drive_id is not allowed under a Drive folder scope; "
            "pass parent_id or folder_id of a scope folder instead"
        )
    param = _CREATE_PARENT.get(tool)
    if param and not kwargs.get(param):
        scope.outside(f"{tool} needs {param} set to a folder inside the scope")
    item_param = _ITEM_CREATE_PARENT.get(tool)
    if item_param:
        for i, item in enumerate(kwargs.get("items") or []):
            if not (isinstance(item, dict) and item.get(item_param)):
                scope.outside(f"{tool} needs items[{i}].{item_param} set to a folder inside the scope")
    named = _named_files(kwargs)
    if not named:
        return
    with _drive_client(service, kwargs.get("account")) as (api, resolved):
        folders = sc.folder_ids(resolved)
        lookup = _parents_lookup(api)
        for where, file_id in named:
            if not scope.in_folders(resolved, file_id, folders, lookup):
                scope.outside(
                    f"file {file_id} ({where}) is not inside the scope's Drive folders for {resolved}"
                )


def _keep_files(sc: scope.Scope, service: Optional[str], account: str, files: list) -> list:
    """The files of ``account`` that lie inside the scope's folders."""
    folders = sc.folder_ids(account)
    if not folders or not files:
        return []
    kept = []
    with _drive_client(service, account) as (api, resolved):
        lookup = _parents_lookup(api)
        for f in files:
            file_id = f.get("id") if isinstance(f, dict) else None
            if not file_id:
                continue
            if "parents" in f:
                scope.remember(resolved, file_id, f.get("parents") or ())
            try:
                inside = scope.in_folders(resolved, file_id, folders, lookup)
            except core.GoogleCoreError:
                inside = False
            if inside:
                kept.append(f)
    return kept


def _filter_files(tool: str, service: Optional[str], sc: scope.Scope, result: Any) -> Any:
    if tool not in _FILTERED_TOOLS or not isinstance(result, dict):
        return result
    if tool == "search_files_all_accounts":
        files = result.get("data") or []
        kept_ids = set()
        for account in sorted({f.get("account") for f in files if f.get("account")}):
            mine = [f for f in files if f.get("account") == account]
            kept_ids.update(id(f) for f in _keep_files(sc, service, account, mine))
        return {**result, "data": [f for f in files if id(f) in kept_ids]}
    data = result.get("data")
    account = result.get("account")
    if not isinstance(data, dict) or not account:
        return result
    files = _keep_files(sc, service, account, data.get("files") or [])
    return {**result, "data": {**data, "files": files}}


def _drive_scope(fn: Callable, service: Optional[str], mutating: bool) -> Callable:
    """Wrap a Drive, Docs, Sheets or Slides tool so a folder scope limits the files it
    names, creates and returns. Mutating calls also drop the cached parents of
    every file they name, since a move or trash changes them."""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            sc = scope.get()
            if sc is None or sc.folders is None:
                return fn(*args, **kwargs)
            _check_drive_args(fn.__name__, service, sc, kwargs)
            return _filter_files(fn.__name__, service, sc, fn(*args, **kwargs))
        finally:
            if mutating:
                scope.forget(fid for _where, fid in _named_files(kwargs))

    return wrapper


def _run_scoped(fn: Callable, args: tuple, kwargs: dict) -> Any:
    """Run a tool in the current (worker) thread, releasing any client locks
    that ``get_api`` took on its behalf once it finishes."""
    _held.locks = []
    try:
        return fn(*args, **kwargs)
    finally:
        for lock in reversed(_held.locks):
            lock.release()
        _held.locks = None


def _off_loop(fn: Callable) -> Callable:
    """Async wrapper FastMCP awaits instead of calling ``fn`` on the event loop.

    Tools are blocking (Google HTTP calls, connect-retry sleeps), so each call
    runs in a worker thread and concurrent calls actually overlap.
    ``functools.wraps`` keeps the name, docstring, and signature FastMCP builds
    the schema from.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(_run_scoped, fn, args, kwargs)

    return wrapper


def register(
    mcp,
    *,
    mutating: bool = False,
    destructive: bool = False,
    idempotent: bool | None = None,
    open_world: bool = False,
    core: bool = False,
    drive_scoped: bool = False,
):
    """Decorator that registers a tool, honoring the read-only gate and
    attaching MCP ToolAnnotations derived from the same flags.

    Mutating tools are not registered at all when ``GOOGLE_MCP_READONLY`` is set,
    so they never appear in ``list_tools``. Destructive tools get a clear marker
    appended to their description. With ``GOOGLE_MCP_TOOLSET=core`` only tools
    tagged ``core=True`` are registered.

    ``drive_scoped`` (set by the Drive, Docs, Sheets and Slides servers) makes a client
    scope's ``drive_folders`` limit the files the tool names, creates and lists.
    """

    def deco(fn: Callable) -> Callable:
        if mutating and READONLY:
            return fn  # skip registration entirely
        if TOOLSET == "core" and not core:
            return fn
        if destructive and fn.__doc__:
            fn.__doc__ = fn.__doc__.rstrip() + (
                "\n\n[DESTRUCTIVE] Permanently changes or deletes data; cannot be undone."
            )
        annotations = ToolAnnotations(
            readOnlyHint=not mutating,
            # MCP defaults destructiveHint to True when absent; mutating
            # non-destructive tools must set it False explicitly.
            destructiveHint=destructive if mutating else None,
            idempotentHint=idempotent,
            # True for tools whose effect reaches people outside the account
            # (sending mail, sharing files).
            openWorldHint=open_world,
        )
        retry_safe = not mutating or idempotent is True
        inner = fn
        if drive_scoped:
            inner = _drive_scope(fn, getattr(mcp, "workspace_service", None), mutating)
        mcp.tool(annotations=annotations)(
            _off_loop(_policy_scope(_retry_scope(inner, retry_safe), mutating))
        )
        return fn  # direct callers keep the plain sync function

    return deco
