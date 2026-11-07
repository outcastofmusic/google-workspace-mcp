"""Tests for core runtime helpers."""
from __future__ import annotations

import errno
import json

import anyio
import google_auth_core as core
import httplib2
import pytest
from googleapiclient.errors import HttpError

from google_workspace_mcp.core import runtime
from google_workspace_mcp.core.paths import free_path, safe_dir, safe_path
from google_workspace_mcp.core.runtime import cap_items, cap_text, ok, register, run_tool


def test_ok_without_meta():
    assert ok("a@x.com", [{"id": 1}]) == {
        "ok": True,
        "account": "a@x.com",
        "data": [{"id": 1}],
    }


def test_ok_with_next_page_token():
    assert ok("a@x.com", [], next_page_token="tok") == {
        "ok": True,
        "account": "a@x.com",
        "data": [],
        "next_page_token": "tok",
    }


def test_ok_drops_none_meta():
    assert ok("a@x.com", "x", next_page_token=None) == {
        "ok": True,
        "account": "a@x.com",
        "data": "x",
    }


def test_run_tool_maps_value_error():
    def boom():
        raise ValueError("bad range")

    with pytest.raises(core.InvalidArgumentError) as exc:
        run_tool(boom)
    assert str(exc.value) == "bad range"


def test_run_tool_reraises_google_core_error():
    err = core.AuthError("login first")

    def boom():
        raise err

    with pytest.raises(core.AuthError) as exc:
        run_tool(boom)
    assert exc.value is err


def test_safe_path_refuses_token_store_even_via_symlink(store_env, tmp_path):
    secret = store_env / "tokens" / "google_a.json"
    secret.write_text("{}")
    link = tmp_path / "innocent.pdf"
    link.symlink_to(secret)
    for p in (str(secret), str(link)):
        with pytest.raises(core.InvalidArgumentError, match="token store"):
            safe_path(p)
    with pytest.raises(core.InvalidArgumentError, match="token store"):
        safe_path("~/.google/credentials.json")
    with pytest.raises(core.InvalidArgumentError, match="token store"):
        safe_dir(str(store_env))
    # macOS volumes are case-insensitive: ~/.Google is the same directory.
    with pytest.raises(core.InvalidArgumentError, match="token store"):
        safe_path(str(store_env).upper() + "/x.json", write=True)


def test_safe_path_file_root(store_env, tmp_path, monkeypatch):
    root = tmp_path / "work"
    root.mkdir()
    inside = root / "a.txt"
    inside.write_text("x")
    outside = tmp_path / "b.txt"
    outside.write_text("x")
    monkeypatch.setenv("GOOGLE_MCP_FILE_ROOT", str(root))
    assert safe_path(str(inside)) == str(inside)
    with pytest.raises(core.InvalidArgumentError, match="GOOGLE_MCP_FILE_ROOT"):
        safe_path(str(outside))
    with pytest.raises(core.InvalidArgumentError, match="GOOGLE_MCP_FILE_ROOT"):
        safe_path(str(root / ".." / "b.txt"), write=True)


def test_safe_path_write_refuses_existing_unless_overwrite(store_env, tmp_path):
    target = tmp_path / "out.bin"
    assert safe_path(str(target), write=True) == str(target)
    target.write_text("keep me")
    with pytest.raises(core.InvalidArgumentError, match="overwrite=True"):
        safe_path(str(target), write=True)
    assert safe_path(str(target), write=True, overwrite=True) == str(target)


def test_safe_path_returns_named_path_not_link_target(store_env, tmp_path):
    target = tmp_path / "ab12.bin"
    target.write_text("x")
    link = tmp_path / "report.pdf"
    link.symlink_to(target)
    assert safe_path(str(link)) == str(link)


def test_free_path_picks_unused_name(tmp_path):
    p = tmp_path / "a.txt"
    assert free_path(str(p)) == str(p)
    p.write_text("x")
    assert free_path(str(p)) == str(tmp_path / "a (1).txt")


class _FakeMCP:
    """Captures what ``register`` hands to FastMCP, i.e. what a client calls."""

    def __init__(self):
        self.tools = {}

    def tool(self, annotations=None):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _http_error(status, retry_after=None):
    headers = {"status": str(status)}
    if retry_after is not None:
        headers["retry-after"] = str(retry_after)
    return HttpError(httplib2.Response(headers), b'{"error": {"message": "busy"}}')


def _flaky(errors, result="done"):
    """A call that raises each error in turn, then returns ``result``."""
    calls = []

    def call():
        calls.append(1)
        if len(calls) <= len(errors):
            raise errors[len(calls) - 1]
        return result

    return call, calls


@pytest.fixture
def sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(runtime.time, "sleep", slept.append)
    return slept


def _tool(api_call, **flags):
    mcp = _FakeMCP()

    @register(mcp, **flags)
    def tool():
        return run_tool(api_call)

    # register hands MCP an async wrapper that runs the tool in a worker thread.
    registered = mcp.tools["tool"]
    return lambda: anyio.run(registered)


def test_read_retries_429_honoring_retry_after(sleeps):
    call, calls = _flaky([_http_error(429, retry_after=3)])
    assert _tool(call)() == "done"
    assert len(calls) == 2
    assert len(sleeps) == 1 and 3 <= sleeps[0] <= 4


def test_read_retries_503_with_growing_backoff(sleeps):
    call, calls = _flaky([_http_error(503), _http_error(503)])
    assert _tool(call)() == "done"
    assert len(calls) == 3
    assert 0.5 <= sleeps[0] <= 1 and 1 <= sleeps[1] <= 2


def test_wrapped_http_error_message_is_retried(sleeps):
    # The Gmail/Calendar wrappers re-raise HttpError as a plain Exception.
    call, calls = _flaky([Exception("list failed: <HttpError 502 ...>")])
    assert _tool(call)() == "done"
    assert len(calls) == 2


def test_non_idempotent_write_is_not_retried(sleeps):
    call, calls = _flaky([_http_error(429, retry_after=1)])
    with pytest.raises(core.RateLimitError):
        _tool(call, mutating=True)()
    assert len(calls) == 1 and sleeps == []


def test_idempotent_write_is_retried(sleeps):
    call, calls = _flaky([_http_error(503)])
    assert _tool(call, mutating=True, idempotent=True)() == "done"
    assert len(calls) == 2


def test_client_errors_are_not_retried(sleeps):
    call, calls = _flaky([_http_error(404)])
    with pytest.raises(core.NotFoundError):
        _tool(call)()
    assert len(calls) == 1 and sleeps == []


def test_retry_after_beyond_budget_fails_fast(sleeps):
    call, calls = _flaky([_http_error(429, retry_after=60)])
    with pytest.raises(core.RateLimitError):
        _tool(call)()
    assert len(calls) == 1 and sleeps == []


def test_total_backoff_is_capped(sleeps):
    call, calls = _flaky([_http_error(503)] * 10)
    with pytest.raises(core.UpstreamError):
        _tool(call)()
    assert len(calls) <= runtime._TRANSIENT_MAX_RETRIES + 1
    assert sum(sleeps) <= runtime._TRANSIENT_MAX_TOTAL_WAIT


def test_run_tool_outside_a_tool_does_not_retry(sleeps):
    call, calls = _flaky([_http_error(503)])
    with pytest.raises(core.UpstreamError):
        run_tool(call)
    assert len(calls) == 1


def test_status_quoted_in_message_does_not_trigger_retry(sleeps):
    call, calls = _flaky([core.InvalidArgumentError("bad value 'x (HTTP 503)' (HTTP 400)")])
    with pytest.raises(core.InvalidArgumentError):
        _tool(call)()
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("retry_after", [float("nan"), -5.0])
def test_malformed_retry_after_falls_back_to_backoff(sleeps, retry_after):
    call, calls = _flaky([core.RateLimitError("slow down (HTTP 429)", retry_after=retry_after)])
    assert _tool(call)() == "done"
    assert 0.5 <= sleeps[0] <= 1


def test_connect_failure_still_retried_for_writes(sleeps):
    call, calls = _flaky([OSError(errno.ECONNREFUSED, "refused")])
    assert _tool(call, mutating=True)() == "done"
    assert sleeps == [runtime._CONNECT_RETRY_DELAYS[0]]


def test_retry_flag_is_reset_after_tool_raises(sleeps):
    call, _ = _flaky([_http_error(404)])
    with pytest.raises(core.NotFoundError):
        _tool(call)()
    assert runtime._retry_safe.get() is False
def test_cap_text_fits_returns_no_meta():
    assert cap_text("hello", 10) == ("hello", {})


def test_cap_text_truncates_and_pages():
    chunk, meta = cap_text("abcdefghij", 4, hint="more")
    assert chunk == "abcd"
    assert meta == {"truncated": True, "total_chars": 10, "offset": 0, "next_offset": 4, "hint": "more"}
    chunk, meta = cap_text("abcdefghij", 4, offset=8)
    assert chunk == "ij"
    assert meta == {"truncated": False, "total_chars": 10, "offset": 8}


def test_cap_text_default_limit():
    # 40,000 keeps next_offset inside a client's 50,000-character cut (agent-ready-defaults).
    chunk, meta = cap_text("x" * 60_000)
    assert len(chunk) == 40_000 and meta["next_offset"] == 40_000


@pytest.mark.parametrize("kwargs", [{"max_chars": 0}, {"offset": -1}])
def test_cap_text_rejects_bad_args(kwargs):
    with pytest.raises(core.InvalidArgumentError):
        cap_text("abc", **kwargs)


def test_cap_items_keeps_whole_items_and_pages():
    items = ["aaaa", "bbbb", "cccc"]  # each serializes to 6 chars
    kept, meta = cap_items(items, 13, hint="next")
    assert kept == ["aaaa", "bbbb"]
    assert meta == {"truncated": True, "total_items": 3, "offset": 0, "next_offset": 2, "hint": "next"}
    kept, meta = cap_items(items, 13, offset=2)
    assert kept == ["cccc"] and meta["truncated"] is False


def test_cap_items_always_returns_one_item():
    kept, meta = cap_items(["x" * 50, "y"], 10)
    assert kept == ["x" * 50] and meta["next_offset"] == 1


# --- compact JSON results (compact-tool-json) --------------------------------

def _server_with(fn):
    from google_workspace_mcp.core.server import build_server

    mcp = build_server("t")
    register(mcp)(fn)
    return mcp


def _text(mcp, name, args=None):
    async def go():
        return await mcp.call_tool(name, args or {})

    return anyio.run(go).content[0].text


def test_dict_result_is_compact_json():
    def nested() -> dict:
        """A nested result."""
        return {"ok": True, "data": [{"id": "a", "tags": ["x", "y"]}, {"id": "b"}]}

    text = _text(_server_with(nested), "nested")
    assert text == '{"ok":true,"data":[{"id":"a","tags":["x","y"]},{"id":"b"}]}'


def test_non_ascii_unescaped():
    def greet() -> dict:
        """Greek text."""
        return {"data": "Καλημέρα", "note": "line1\nline2"}

    text = _text(_server_with(greet), "greet")
    assert "Καλημέρα" in text and "\\u" not in text
    assert "\n" not in text and json.loads(text)["note"] == "line1\nline2"


def test_string_result_unchanged():
    def plain() -> str:
        """A string result."""
        return "hello"

    assert _text(_server_with(plain), "plain") == "hello"


def test_direct_call_returns_dict():
    def direct() -> dict:
        """Direct."""
        return {"ok": True}

    assert register(_FakeMCP())(direct)() == {"ok": True}


def test_schema_unchanged_by_wrapper():
    def search(query: str, max_results: int = 10) -> dict:
        """Search things."""
        return {"ok": True}

    async def go():
        return {t.name: t for t in await _server_with(search).list_tools()}["search"]

    tool = anyio.run(go)
    assert set(tool.input_schema["properties"]) == {"query", "max_results"}
    assert tool.input_schema["required"] == ["query"]
    assert tool.output_schema is None and tool.description == "Search things."

