"""Cross-cutting guarantees that span the whole system:

* multi-account isolation through the shared ``get_api`` cache (the headline
  requirement: parallel accounts must never interfere),
* persistence (credentials load from disk on "restart"; no interactive flow),
* the read-only gate (mutating tools disappear from every server),
* all five servers expose the aligned common tools.
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import google_auth_core as core
import pytest

from google_workspace_mcp.core import runtime
from google_workspace_mcp.core.server import build_server

# --- multi-account isolation -------------------------------------------------

def test_get_api_isolation(store_env, write_token):
    write_token(store_env, "a@x.com")
    write_token(store_env, "b@y.com")

    created = []

    def factory(account):
        created.append(account)
        return SimpleNamespace(account=account)

    api_a, ra = runtime.get_api("gmail", factory, "a@x.com")
    api_a2, _ = runtime.get_api("gmail", factory, "a@x.com")
    api_b, rb = runtime.get_api("gmail", factory, "b@y.com")

    assert ra == "a@x.com" and rb == "b@y.com"
    assert api_a is api_a2  # cached per account
    assert api_a is not api_b
    assert api_a.account == "a@x.com" and api_b.account == "b@y.com"
    assert created == ["a@x.com", "b@y.com"]  # one construction per account
    assert runtime.cached_keys() == [("gmail", "a@x.com"), ("gmail", "b@y.com")]


def test_get_api_alias_resolves(store_env, write_token):
    write_token(store_env, "real@x.com")
    core.set_account_alias("work", "real@x.com")
    _api, resolved = runtime.get_api("gmail", lambda a: SimpleNamespace(account=a), "work")
    assert resolved == "real@x.com"


@pytest.mark.anyio
async def test_parallel_accounts_do_not_interfere(store_env, write_token):
    write_token(store_env, "a@x.com", token="tok-A")
    write_token(store_env, "b@y.com", token="tok-B")

    def factory(account):
        # capture which account's credentials were loaded for this build
        creds, resolved = core.get_credentials(account)
        return SimpleNamespace(account=resolved, token=creds.token)

    async def call(acct):
        return await asyncio.to_thread(runtime.get_api, "gmail", factory, acct)

    (api_a, _), (api_b, _) = await asyncio.gather(call("a@x.com"), call("b@y.com"))
    assert api_a.token == "tok-A"
    assert api_b.token == "tok-B"  # no cross-contamination under concurrency



def _blocking_tool_server(on_call):
    """A server with one tool that resolves its account through get_api and
    then runs ``on_call(account)`` in place of a Google request."""
    from google_workspace_mcp.core import ok, register, run_tool

    mcp = build_server("test-mcp")

    @register(mcp)
    def slow_call(account: str | None = None) -> dict:
        """Stand-in for a blocking Google call."""
        api, resolved = runtime.get_api("gmail", lambda a: SimpleNamespace(account=a), account)
        return ok(resolved, run_tool(lambda: on_call(api.account)))

    return mcp


@pytest.mark.anyio
async def test_tool_calls_for_two_accounts_overlap(store_env, write_token):
    """Through the MCP layer, a blocking call for one account must not hold up
    another account's call. The barrier only opens when both are in flight at
    once; if tools ran on the event loop, the first would time out alone."""
    write_token(store_env, "a@x.com")
    write_token(store_env, "b@y.com")
    barrier = threading.Barrier(2, timeout=5)

    def on_call(account):
        barrier.wait()
        return account

    mcp = _blocking_tool_server(on_call)
    ra, rb = await asyncio.gather(
        mcp.call_tool("slow_call", {"account": "a@x.com"}),
        mcp.call_tool("slow_call", {"account": "b@y.com"}),
    )
    assert _parse_tool(ra)["data"] == "a@x.com"
    assert _parse_tool(rb)["data"] == "b@y.com"


@pytest.mark.anyio
async def test_tool_calls_on_one_account_never_share_the_client(store_env, write_token):
    """httplib2 is not thread-safe, so calls on the same cached client must run
    one at a time even though they now leave the event loop."""
    write_token(store_env, "a@x.com")
    active = 0
    peak = 0
    guard = threading.Lock()

    def on_call(account):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return account

    mcp = _blocking_tool_server(on_call)
    results = await asyncio.gather(
        *(mcp.call_tool("slow_call", {"account": "a@x.com"}) for _ in range(4))
    )
    assert all(_parse_tool(r)["ok"] for r in results)
    assert peak == 1


# --- persistence -------------------------------------------------------------

def test_credentials_persist_across_restart(store_env, write_token, monkeypatch):
    write_token(store_env, "a@x.com")
    runtime.get_api("gmail", lambda a: SimpleNamespace(account=a), "a@x.com")

    # Simulate a server restart: drop the in-process cache.
    runtime.reset_api_cache()

    # Any interactive auth attempt should be considered a failure.
    def boom(*args, **kwargs):
        raise AssertionError("interactive auth must not run in the server")

    monkeypatch.setattr(core, "authenticate", boom)

    _api, resolved = runtime.get_api("gmail", lambda a: SimpleNamespace(account=a), "a@x.com")
    assert resolved == "a@x.com"  # loaded from disk, no re-consent


def test_missing_credentials_raise_actionable_error(store_env):
    with pytest.raises(core.AuthError) as exc:
        runtime.get_api("gmail", lambda a: SimpleNamespace(account=a), "ghost@x.com")
    assert "google-auth login ghost@x.com" in str(exc.value)


# --- read-only gate ----------------------------------------------------------

def test_readonly_gate_hides_mutating_tools():
    """With GOOGLE_MCP_READONLY set, mutating tools must not be registered."""
    code = (
        "import asyncio\n"
        "from google_workspace_mcp.gmail import server\n"
        "names = [t.name for t in asyncio.run(server.mcp.list_tools())]\n"
        "print(('send_message' in names, 'delete_message' in names, 'list_messages' in names))\n"
    )
    env = {**os.environ, "GOOGLE_MCP_READONLY": "1"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "(False, False, True)"


def test_mutating_tools_present_by_default():
    code = (
        "import asyncio\n"
        "from google_workspace_mcp.gmail import server\n"
        "names = [t.name for t in asyncio.run(server.mcp.list_tools())]\n"
        "print(('send_message' in names, 'list_messages' in names))\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "GOOGLE_MCP_READONLY"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "(True, True)"


@pytest.mark.parametrize(
    "pkg,read_tool,write_tool",
    [
        ("docs", "read_document", "create_document"),
        ("drive", "list_files", "upload_file"),
    ],
)
def test_readonly_gate_hides_mutating_tools_docs_drive(pkg, read_tool, write_tool):
    code = (
        f"import asyncio\n"
        f"from google_workspace_mcp.{pkg} import server\n"
        f"names = [t.name for t in asyncio.run(server.mcp.list_tools())]\n"
        f"print(({write_tool!r} in names, {read_tool!r} in names))\n"
    )
    env = {**os.environ, "GOOGLE_MCP_READONLY": "1"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "(False, True)"


# --- toolset gate ------------------------------------------------------------

_CORE_TOOLSET = {
    "gmail": {
        "list_messages", "search_messages", "get_message", "get_thread", "list_labels",
        "download_attachment", "send_message", "reply_to_message", "draft_reply",
        "forward_message", "create_draft", "list_drafts", "send_draft", "modify_labels",
        "archive_message", "trash_message", "search_contacts",
    },
    "calendar": {
        "list_calendars", "list_events", "search_events", "get_event", "freebusy_query",
        "find_available_slots", "create_event", "quick_add_event", "update_event",
        "respond_to_event", "delete_event", "search_contacts",
    },
    "sheets": {
        "get_spreadsheet", "read_range", "batch_read", "create_spreadsheet", "update_range",
        "batch_update_values", "append_rows", "write_table", "add_sheet", "clear_range",
        "format_cells", "find_replace",
    },
    "docs": {
        "read_document", "read_document_as_markdown", "get_content_map",
        "create_document_from_markdown", "replace_document_with_markdown", "append_markdown",
        "insert_text", "replace_all_text", "format_text", "insert_table",
        "find_text", "format_matches",
    },
    "drive": {
        "search_files", "list_files", "get_file", "read_file_text", "download_file",
        "export_file", "upload_file", "create_folder", "move_file", "rename_file", "copy_file",
        "share_file", "trash_file",
    },
}
_COMMON = {"list_accounts", "whoami", "auth_status"}


def _tool_names(pkg, env):
    code = (
        "import asyncio, json\n"
        f"from google_workspace_mcp.{pkg} import server\n"
        "print(json.dumps(sorted(t.name for t in asyncio.run(server.mcp.list_tools()))))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return set(json.loads(out.stdout))


def _base_env():
    return {
        k: v for k, v in os.environ.items()
        if k not in ("GOOGLE_MCP_READONLY", "GOOGLE_MCP_TOOLSET")
    }


@pytest.mark.parametrize("pkg", sorted(_CORE_TOOLSET))
def test_core_toolset_registers_expected_tools(pkg):
    names = _tool_names(pkg, {**_base_env(), "GOOGLE_MCP_TOOLSET": "core"})
    assert names == _CORE_TOOLSET[pkg] | _COMMON


@pytest.mark.parametrize("pkg", sorted(_CORE_TOOLSET))
def test_default_toolset_is_all(pkg):
    default = _tool_names(pkg, _base_env())
    explicit_all = _tool_names(pkg, {**_base_env(), "GOOGLE_MCP_TOOLSET": "all"})
    assert default == explicit_all
    assert default > _CORE_TOOLSET[pkg] | _COMMON


def test_core_toolset_combines_with_readonly():
    env = {**_base_env(), "GOOGLE_MCP_TOOLSET": "core", "GOOGLE_MCP_READONLY": "1"}
    names = _tool_names("gmail", env)
    assert {"list_messages", "get_message"} <= names
    assert not names & {"send_message", "trash_message", "list_changes"}


def test_unknown_toolset_warns_and_registers_all():
    code = "from google_workspace_mcp.gmail import server  # noqa: F401\n"
    env = {**_base_env(), "GOOGLE_MCP_TOOLSET": "cor"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert "GOOGLE_MCP_TOOLSET" in out.stderr and out.stdout == ""
    assert _tool_names("gmail", env) == _tool_names("gmail", _base_env())


# --- alignment: every server exposes the common tools ------------------------

@pytest.mark.anyio
async def test_all_servers_expose_common_tools():
    from google_workspace_mcp.calendar import server as c
    from google_workspace_mcp.docs import server as d
    from google_workspace_mcp.drive import server as dr
    from google_workspace_mcp.gmail import server as g
    from google_workspace_mcp.sheets import server as s

    for mod in (g, c, s, d, dr):
        names = {t.name for t in await mod.mcp.list_tools()}
        assert {"list_accounts", "whoami", "auth_status"}.issubset(names)


@pytest.mark.anyio
async def test_workspace_server_carries_every_tool_unchanged():
    """gworkspace-mcp exposes each server's tools once, prefixing only names two
    servers share, with the same schema, annotations and description."""
    from google_workspace_mcp.workspace import server as w

    combined = {t.name: t for t in await w.mcp.list_tools()}
    assert {"list_accounts", "whoami", "auth_status"}.issubset(combined)
    for service, mod in w._SERVICES.items():
        for t in await mod.mcp.list_tools():
            if t.name in ("list_accounts", "whoami", "auth_status"):
                continue
            c = combined.get(t.name) or combined[f"{service}_{t.name}"]
            assert (c.input_schema, c.annotations, c.description) == (
                t.input_schema, t.annotations, t.description
            )
    assert "list_changes" not in combined
    assert {"gmail_list_changes", "drive_list_changes"}.issubset(combined)


@pytest.mark.anyio
async def test_contact_search_on_gmail_and_calendar_only():
    from google_workspace_mcp.calendar import server as c
    from google_workspace_mcp.docs import server as d
    from google_workspace_mcp.drive import server as dr
    from google_workspace_mcp.gmail import server as g
    from google_workspace_mcp.sheets import server as s

    for mod in (g, c):
        tool = _tool_by_name(await mod.mcp.list_tools(), "search_contacts")
        assert tool.annotations.read_only_hint is True
        assert tool.input_schema["required"] == ["query"]
    for mod in (s, d, dr):
        names = {t.name for t in await mod.mcp.list_tools()}
        assert "search_contacts" not in names


def test_readonly_gate_keeps_contact_search():
    code = (
        "import asyncio\n"
        "from google_workspace_mcp.calendar import server\n"
        "names = [t.name for t in asyncio.run(server.mcp.list_tools())]\n"
        "print(('search_contacts' in names, 'create_event' in names))\n"
    )
    env = {**os.environ, "GOOGLE_MCP_READONLY": "1"}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "(True, False)"


def test_google_auth_exposes_setup_and_doctor():
    """Pinned google-auth-core>=0.2.0 must ship setup and doctor subcommands."""
    from google_auth_core.cli import main

    names = {cmd.name for cmd in main.commands.values()}
    assert {"setup", "doctor"}.issubset(names)


# --- MCP tool annotations ----------------------------------------------------

def _tool_by_name(tools, name):
    return next(t for t in tools if t.name == name)


@pytest.mark.anyio
async def test_annotations_read_vs_destructive():
    from google_workspace_mcp.calendar import server as c
    from google_workspace_mcp.docs import server as d
    from google_workspace_mcp.drive import server as dr
    from google_workspace_mcp.gmail import server as g
    from google_workspace_mcp.sheets import server as s

    cases = [
        (g, "get_profile", "delete_message"),
        (c, "list_events", "delete_event"),
        (s, "read_range", "delete_sheet"),
        (d, "read_document", "delete_range"),
        (dr, "search_files", "delete_file"),
    ]
    for mod, read_tool, destructive_tool in cases:
        tools = await mod.mcp.list_tools()
        read_ann = _tool_by_name(tools, read_tool).annotations
        assert read_ann is not None
        assert read_ann.read_only_hint is True

        dest_ann = _tool_by_name(tools, destructive_tool).annotations
        assert dest_ann is not None
        assert dest_ann.read_only_hint is False
        assert dest_ann.destructive_hint is True


@pytest.mark.anyio
async def test_required_ids_are_required_in_schema():
    # Models omit params the schema calls optional; a missing ID must fail as
    # a validation error, not as a Google 404 on an empty string.
    from google_workspace_mcp.calendar import server as c
    from google_workspace_mcp.docs import server as d
    from google_workspace_mcp.drive import server as dr
    from google_workspace_mcp.gmail import server as g
    from google_workspace_mcp.sheets import server as s

    cases = [
        (g, "get_message", {"message_id"}, "format"),
        (g, "reply_to_message", {"message_id", "body"}, "reply_all"),
        (g, "search_contacts", {"query"}, "max_results"),
        (c, "get_event", {"event_id"}, "calendar_id"),
        (c, "delete_event", {"event_id"}, "calendar_id"),
        (s, "read_range", {"spreadsheet_id", "range"}, "max_chars"),
        (s, "delete_sheet", {"spreadsheet_id", "sheet_id"}, None),
        (s, "find_replace", {"spreadsheet_id", "find"}, "replacement"),
        (d, "read_document", {"document_id"}, "max_chars"),
        (d, "delete_range", {"document_id", "start_index", "end_index"}, "segment_id"),
        (dr, "get_file", {"file_id"}, None),
        (dr, "move_file", {"file_id", "new_parent_id"}, None),
        (dr, "batch_trash_files", {"file_ids"}, None),
        (g, "create_filter", {"criteria", "action"}, None),
        (c, "add_attendees", {"event_id", "attendee_emails"}, "send_updates"),
        (c, "create_out_of_office", {"start_time", "end_time"}, "timezone"),
        (d, "populate_table", {"document_id", "table_start_index", "rows"}, "segment_id"),
        (s, "update_range", {"spreadsheet_id", "range", "values"}, "value_input_option"),
        (d, "create_document", {"title"}, "folder_id"),
        (s, "create_spreadsheet", {"title"}, "folder_id"),
    ]
    for mod, name, required, optional in cases:
        schema = _tool_by_name(await mod.mcp.list_tools(), name).input_schema
        assert set(schema.get("required", [])) == required, name
        assert "account" in schema["properties"], name
        if optional:
            assert optional in schema["properties"], name


@pytest.mark.anyio
async def test_annotations_mutating_not_destructive():
    from google_workspace_mcp.calendar import server as c
    from google_workspace_mcp.docs import server as d
    from google_workspace_mcp.drive import server as dr
    from google_workspace_mcp.gmail import server as g
    from google_workspace_mcp.sheets import server as s

    cases = [
        (g, "create_draft"),
        (c, "create_event"),
        (s, "update_range"),
        (d, "append_text"),
        (dr, "upload_file"),
    ]
    for mod, write_tool in cases:
        tools = await mod.mcp.list_tools()
        ann = _tool_by_name(tools, write_tool).annotations
        assert ann is not None
        assert ann.read_only_hint is False
        assert ann.destructive_hint is False


@pytest.mark.anyio
async def test_common_tools_readonly_hint():
    from google_workspace_mcp.calendar import server as c
    from google_workspace_mcp.docs import server as d
    from google_workspace_mcp.drive import server as dr
    from google_workspace_mcp.gmail import server as g
    from google_workspace_mcp.sheets import server as s

    for mod in (g, c, s, d, dr):
        tools = await mod.mcp.list_tools()
        for name in ("list_accounts", "whoami", "auth_status"):
            ann = _tool_by_name(tools, name).annotations
            assert ann is not None
            assert ann.read_only_hint is True


@pytest.mark.anyio
async def test_no_output_schema_regression():
    from google_workspace_mcp.calendar import server as c
    from google_workspace_mcp.docs import server as d
    from google_workspace_mcp.drive import server as dr
    from google_workspace_mcp.gmail import server as g
    from google_workspace_mcp.sheets import server as s

    for mod in (g, c, s, d, dr):
        tools = await mod.mcp.list_tools()
        for tool in tools:
            assert tool.output_schema is None, tool.name


def _parse_tool(result) -> dict:
    assert result.content, "empty tool result"
    return json.loads(result.content[0].text)


@pytest.mark.anyio
async def test_auth_status_resolves_alias(store_env, write_token):
    write_token(store_env, "real@x.com")
    core.set_account_alias("work", "real@x.com")
    mcp = build_server("test-mcp")
    data = _parse_tool(await mcp.call_tool("auth_status", {"account": "work"}))
    assert data["ok"] is True
    assert "real@x.com" in data["data"]
    assert "work" not in data["data"]


@pytest.mark.anyio
async def test_common_tools_honor_service_name(store_env, write_token, monkeypatch):
    write_token(store_env, "gmail-env@x.com")
    write_token(store_env, "store-default@x.com")
    monkeypatch.setenv("GMAIL_ACCOUNT", "gmail-env@x.com")
    mcp = build_server("test-mcp", service_name="gmail")
    listed = _parse_tool(await mcp.call_tool("list_accounts", {}))
    assert listed["data"]["default"] == "gmail-env@x.com"
    me = _parse_tool(await mcp.call_tool("whoami", {}))
    assert me["account"] == "gmail-env@x.com"


@pytest.mark.anyio
async def test_whoami_error_returns_resolved_account(store_env, write_token):
    path = write_token(store_env, "ghost@x.com")
    core.set_account_alias("work", "ghost@x.com")
    path.unlink()
    mcp = build_server("test-mcp")
    data = _parse_tool(await mcp.call_tool("whoami", {"account": "work"}))
    assert data["ok"] is False
    assert data["account"] == "ghost@x.com"



@pytest.mark.anyio
async def test_tool_errors_keep_actionable_message(store_env):
    # mcp 2.x masks non-ToolError exceptions; our auth/argument errors must still
    # reach the model with their text.
    from mcp.server.mcpserver.exceptions import ToolError

    mcp = build_server("test-mcp")

    @runtime.register(mcp)
    def needs_login(account: str | None = None) -> dict:
        """Fails like a tool whose account has no token."""
        raise core.AuthError("Run: google-auth login nobody@x.com")

    @runtime.register(mcp)
    def bad_arg(account: str | None = None) -> dict:
        """Fails like a tool rejecting an argument."""
        raise ValueError("range must be A1 notation")

    with pytest.raises(ToolError, match="google-auth login nobody@x.com"):
        await mcp.call_tool("needs_login", {})
    with pytest.raises(ToolError, match="range must be A1 notation"):
        await mcp.call_tool("bad_arg", {})

@pytest.mark.anyio
async def test_outward_and_overwrite_tools_are_destructive():
    from google_workspace_mcp.docs import server as d
    from google_workspace_mcp.drive import server as dr
    from google_workspace_mcp.gmail import server as g
    from google_workspace_mcp.sheets import server as s

    cases = [
        (g, "send_message", True), (g, "reply_to_message", True), (g, "forward_message", True),
        (g, "send_draft", True), (g, "create_filter", True), (g, "block_sender", False),
        (dr, "share_file", True), (dr, "unshare_file", False), (dr, "update_file_content", False),
        (d, "batch_update", False), (s, "batch_update", False),
    ]
    for mod, name, open_world in cases:
        ann = _tool_by_name(await mod.mcp.list_tools(), name).annotations
        assert ann.destructive_hint is True, name
        assert ann.open_world_hint is open_world, name


def test_drive_document_tools_stay_in_readonly_mode():
    names = _tool_names("drive", {**_base_env(), "GOOGLE_MCP_READONLY": "1"})
    assert {"read_file_text", "read_files", "list_folder_tree"} <= names
    assert "upload_file" not in names
