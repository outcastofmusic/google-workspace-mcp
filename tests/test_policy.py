"""Safety policy: recipient allowlist, account checks, audit log, hidden paths."""

import functools
import json
import os
import stat

import anyio
import google_auth_core as core
import pytest

from google_workspace_mcp.core import get_api, ok, register
from google_workspace_mcp.core.paths import safe_dir, safe_path
from google_workspace_mcp.core.policy import recipients


class _FakeMCP:
    def __init__(self):
        self.tools = {}

    def tool(self, annotations=None):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


def _tool(name="send_message", mutating=True):
    """A registered tool that resolves its account like a real one does."""
    mcp = _FakeMCP()

    def tool(account=None, **kwargs):
        _api, resolved = get_api("gmail", lambda acct: object(), account)
        return ok(resolved, kwargs)

    tool.__name__ = name
    register(mcp, mutating=mutating)(tool)
    registered = mcp.tools[name]
    return lambda **kw: anyio.run(functools.partial(registered, **kw))


@pytest.fixture
def one_account(store_env, write_token):
    write_token(store_env, "me@home.com")
    return store_env


@pytest.fixture
def two_accounts(store_env, write_token):
    write_token(store_env, "me@work.com")
    write_token(store_env, "me@home.com")
    return store_env


@pytest.fixture(autouse=True)
def _clean_policy_env(monkeypatch):
    for var in (
        "GOOGLE_MCP_REQUIRE_ACCOUNT",
        "GOOGLE_MCP_READONLY_ACCOUNTS",
        "GOOGLE_MCP_ALLOWED_RECIPIENTS",
        "GOOGLE_MCP_ALLOW_IMPLICIT_RECIPIENTS",
        "GOOGLE_MCP_AUDIT_LOG",
        "GOOGLE_MCP_FILE_ROOT",
        "GOOGLE_MCP_ALLOW_HIDDEN_PATHS",
    ):
        monkeypatch.delenv(var, raising=False)


# --- recipient allowlist ----------------------------------------------------

def test_no_allowlist_allows_any_recipient(one_account):
    assert _tool()(to="anyone@evil.com")["ok"]


def test_allowlist_by_domain_and_address(one_account, monkeypatch):
    monkeypatch.setenv("GOOGLE_MCP_ALLOWED_RECIPIENTS", "home.com, @corp.io, bob@x.com")
    send = _tool()
    assert send(to="Ana <ana@home.com>", cc="dev@eu.corp.io; bob@x.com")["ok"]
    with pytest.raises(core.InvalidArgumentError, match="eve@evil.com"):
        send(to="ana@home.com", cc="eve@evil.com")
    with pytest.raises(core.InvalidArgumentError, match="alice@x.com"):
        send(to="alice@x.com")
    # A look-alike domain is not a subdomain.
    with pytest.raises(core.InvalidArgumentError, match="evilhome.com"):
        send(to="a@evilhome.com")


def test_allowlist_covers_attendees_shares_and_filter_forwards(one_account, monkeypatch):
    monkeypatch.setenv("GOOGLE_MCP_ALLOWED_RECIPIENTS", "home.com")
    with pytest.raises(core.InvalidArgumentError, match="x@evil.com"):
        _tool("add_attendees")(attendee_emails=["a@home.com", "x@evil.com"])
    with pytest.raises(core.InvalidArgumentError, match="x@evil.com"):
        _tool("share_file")(file_id="F", email="x@evil.com")
    with pytest.raises(core.InvalidArgumentError, match="anyone"):
        _tool("share_file")(file_id="F", anyone=True)
    with pytest.raises(core.InvalidArgumentError, match="x@evil.com"):
        _tool("create_filter")(criteria={"from": "a"}, action={"forward": "x@evil.com"})


def test_allowlist_refuses_non_addresses(one_account, monkeypatch):
    monkeypatch.setenv("GOOGLE_MCP_ALLOWED_RECIPIENTS", "home.com")
    with pytest.raises(core.InvalidArgumentError, match="not an email address"):
        _tool()(to="ana@home.com, everyone")


def test_allowlist_blocks_implicit_recipients_unless_opted_in(one_account, monkeypatch):
    monkeypatch.setenv("GOOGLE_MCP_ALLOWED_RECIPIENTS", "home.com")
    for name in ("reply_to_message", "send_draft"):
        with pytest.raises(core.InvalidArgumentError, match="ALLOW_IMPLICIT_RECIPIENTS"):
            _tool(name)(message_id="M")
    monkeypatch.setenv("GOOGLE_MCP_ALLOW_IMPLICIT_RECIPIENTS", "1")
    assert _tool("reply_to_message")(message_id="M")["ok"]
    # Explicit extras on a reply are still checked.
    with pytest.raises(core.InvalidArgumentError, match="x@evil.com"):
        _tool("reply_to_message")(message_id="M", additional_cc="x@evil.com")


def test_read_tools_ignore_the_allowlist(one_account, monkeypatch):
    monkeypatch.setenv("GOOGLE_MCP_ALLOWED_RECIPIENTS", "home.com")
    assert _tool("find_available_slots", mutating=False)(attendee_emails=["x@evil.com"])["ok"]


def test_recipients_collects_every_recipient_arg():
    got = recipients("t", {"to": "a@x.com", "bcc": ["b@x.com"], "action": {"forward": "c@x.com"}})
    assert got == ["a@x.com", "b@x.com", "c@x.com"]


# --- account checks -----------------------------------------------------------

def test_single_account_needs_no_explicit_account(one_account):
    assert _tool()(to="a@b.com")["account"] == "me@home.com"


def test_several_accounts_require_explicit_account_for_writes(two_accounts):
    with pytest.raises(core.InvalidArgumentError, match="pass account explicitly"):
        _tool()(to="a@b.com")
    assert _tool()(account="me@work.com", to="a@b.com")["account"] == "me@work.com"
    # Reads still fall back to the default account.
    assert _tool("list_messages", mutating=False)()["ok"]


def test_require_account_can_be_forced_or_disabled(two_accounts, monkeypatch):
    monkeypatch.setenv("GOOGLE_MCP_REQUIRE_ACCOUNT", "0")
    assert _tool()(to="a@b.com")["ok"]
    monkeypatch.setenv("GOOGLE_MCP_REQUIRE_ACCOUNT", "1")
    with pytest.raises(core.InvalidArgumentError, match="pass account explicitly"):
        _tool()(to="a@b.com")


def test_readonly_accounts_block_writes_only(two_accounts, monkeypatch):
    monkeypatch.setenv("GOOGLE_MCP_READONLY_ACCOUNTS", "me@work.com")
    with pytest.raises(core.InvalidArgumentError, match="me@work.com is read-only"):
        _tool()(account="me@work.com", to="a@b.com")
    assert _tool()(account="me@home.com", to="a@b.com")["ok"]
    assert _tool("list_messages", mutating=False)(account="me@work.com")["ok"]


# --- audit log --------------------------------------------------------------

def test_audit_log_records_mutating_calls(one_account, tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("GOOGLE_MCP_AUDIT_LOG", str(log))
    monkeypatch.setenv("GOOGLE_MCP_ALLOWED_RECIPIENTS", "home.com")
    _tool()(to="a@home.com", body="x" * 500)
    with pytest.raises(core.InvalidArgumentError):
        _tool()(to="eve@evil.com")
    _tool("list_messages", mutating=False)()

    lines = [json.loads(line) for line in log.read_text().splitlines()]
    assert [e["ok"] for e in lines] == [True, False]
    assert lines[0]["account"] == "me@home.com"
    assert len(lines[0]["args"]["body"]) < 210
    assert "eve@evil.com" in lines[1]["error"]
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600


# --- hidden and system paths ------------------------------------------------

def test_hidden_paths_refused_by_default(store_env, tmp_path, monkeypatch):
    key = tmp_path / ".ssh" / "id_ed25519"
    key.parent.mkdir()
    key.write_text("secret")
    with pytest.raises(core.InvalidArgumentError, match="hidden or system path"):
        safe_path(str(key))
    with pytest.raises(core.InvalidArgumentError, match="hidden or system path"):
        safe_dir(str(key.parent))
    with pytest.raises(core.InvalidArgumentError, match="hidden or system path"):
        safe_path("/etc/passwd")
    monkeypatch.setenv("GOOGLE_MCP_ALLOW_HIDDEN_PATHS", "1")
    assert safe_path(str(key)) == str(key)


def test_file_root_replaces_the_hidden_path_rule(store_env, tmp_path, monkeypatch):
    root = tmp_path / ".work"
    root.mkdir()
    f = root / "a.txt"
    f.write_text("x")
    monkeypatch.setenv("GOOGLE_MCP_FILE_ROOT", str(root))
    assert safe_path(str(f)) == str(f)
