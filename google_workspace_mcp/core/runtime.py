"""Shared runtime: account resolution + warm-client cache, the read-only gate,
error mapping, and the response envelope. Every tool in every server goes
through these helpers, which is what keeps the five servers aligned.
"""

from __future__ import annotations

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

from . import policy


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
    instances and therefore never interfere.

    Returns ``(api_instance, resolved_account)``.
    """
    # get_credentials and client factories may refresh an expired token and
    # rewrite its file in place; with tools on worker threads, two calls must
    # not do that at the same time, so both run under the cache lock.
    with _lock:
        _creds, resolved = core.get_credentials(account, service_name=service_name)
        key = (service_name, resolved)
        inst = _api_cache.get(key)
        if inst is None:
            inst = factory(resolved)
            _api_cache[key] = inst
        client_lock = _client_locks.setdefault(key, threading.Lock())
    if _mutating.get():
        policy.check_account(account, resolved)
    held = getattr(_held, "locks", None)
    if held is not None and client_lock not in held:
        client_lock.acquire()
        held.append(client_lock)
    return inst, resolved


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
                policy.audit(
                    fn.__name__, kwargs, account=account or kwargs.get("account"), error=error
                )

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
):
    """Decorator that registers a tool, honoring the read-only gate and
    attaching MCP ToolAnnotations derived from the same flags.

    Mutating tools are not registered at all when ``GOOGLE_MCP_READONLY`` is set,
    so they never appear in ``list_tools``. Destructive tools get a clear marker
    appended to their description. With ``GOOGLE_MCP_TOOLSET=core`` only tools
    tagged ``core=True`` are registered.
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
        mcp.tool(annotations=annotations)(
            _off_loop(_policy_scope(_retry_scope(fn, retry_safe), mutating))
        )
        return fn  # direct callers keep the plain sync function

    return deco
