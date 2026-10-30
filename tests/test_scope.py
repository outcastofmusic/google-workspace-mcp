"""Client scope: accounts and Drive folders a client attaches in a call's _meta."""

from __future__ import annotations

import importlib.util
import json
import re
import threading
from pathlib import Path
from unittest.mock import MagicMock

import google_auth_core as core
import pytest
from mcp import Client

from google_workspace_mcp.core import get_api, ok, register, scope
from google_workspace_mcp.core.scope import SCOPE_KEY
from google_workspace_mcp.core.server import build_server
from google_workspace_mcp.docs import server as docs_server
from google_workspace_mcp.docs.docs_api import DocsAPI
from google_workspace_mcp.drive import drive_api
from google_workspace_mcp.drive import server as drive_server
from google_workspace_mcp.drive.drive_api import DriveAPI
from google_workspace_mcp.gmail import server as gmail_server
from google_workspace_mcp.sheets import server as sheets_server
from google_workspace_mcp.sheets.sheets_api import SheetsAPI
from google_workspace_mcp.slides import server as slides_server
from google_workspace_mcp.slides.slides_api import SlidesAPI

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in (
        "GOOGLE_MCP_REQUIRE_SCOPE",
        "GOOGLE_MCP_REQUIRE_ACCOUNT",
        "GOOGLE_MCP_READONLY_ACCOUNTS",
        "GOOGLE_MCP_ALLOWED_RECIPIENTS",
        "GOOGLE_MCP_AUDIT_LOG",
    ):
        monkeypatch.delenv(var, raising=False)
    scope.clear_cache()
    yield
    scope.clear_cache()


async def call(mcp, name, args=None, sc=None):
    meta = {SCOPE_KEY: sc} if sc is not None else None
    async with Client(mcp) as client:
        return await client.call_tool(name, args or {}, meta=meta)


def text(res) -> str:
    return res.content[0].text


def env(res) -> dict:
    assert not res.is_error, text(res)
    return json.loads(text(res))


class _Using:
    """Set the scope context variable for a direct (non-protocol) call."""

    def __init__(self, sc):
        self.sc = sc

    def __enter__(self):
        self.token = scope.current.set(self.sc)

    def __exit__(self, *exc):
        scope.current.reset(self.token)


# --- 1.1 parsing --------------------------------------------------------------

def test_parse_valid_scope_resolves_aliases(store_env, write_token):
    write_token(store_env, "me@work.com")
    core.set_account_alias("work", "me@work.com")
    sc = scope.parse({
        "accounts": ["work", "me@work.com"],
        "drive_folders": [{"account": "work", "id": "DDB"}],
    })
    assert sc.accounts == ("me@work.com",)
    assert sc.folders == (scope.Folder("me@work.com", "DDB"),)
    assert sc.allows_account("ME@work.com") and not sc.allows_account("me@home.com")
    assert sc.folder_ids("me@work.com") == {"DDB"}
    assert sc.folder_ids("me@home.com") == frozenset()


def test_missing_key_means_no_scope():
    assert scope.from_meta(None) is None
    assert scope.from_meta({"io.modelcontextprotocol/clientInfo": {}}) is None
    only_accounts = scope.parse({"accounts": ["a@x.com"]})
    assert only_accounts.folders is None


@pytest.mark.parametrize("value, problem", [
    (["work"], "must be an object"),
    ({"acounts": ["work"]}, "unknown key"),
    ({"accounts": "work"}, "accounts must be a non-empty list"),
    ({"accounts": []}, "accounts must be a non-empty list"),
    ({"accounts": [""]}, r"accounts\[0\] must be a non-empty string"),
    ({"drive_folders": [{"id": "F"}]}, r"drive_folders\[0\] must be an object"),
    ({"drive_folders": [{"account": "a@x.com", "id": 7}]}, r"drive_folders\[0\]\.id"),
])
def test_wrong_shape_names_the_problem(store_env, value, problem):
    with pytest.raises(core.InvalidArgumentError, match=problem):
        scope.from_meta({SCOPE_KEY: value})


# --- 1.2 the scope over the protocol -----------------------------------------

def _peek_server():
    mcp = build_server("scope-test")

    @register(mcp)
    def peek() -> dict:
        sc = scope.get()
        return {
            "worker": threading.current_thread() is not threading.main_thread(),
            "scope": sc.to_dict() if sc is not None else None,
        }

    return mcp


async def test_scope_reaches_the_tool_thread(store_env):
    mcp = _peek_server()
    out = env(await call(mcp, "peek", sc={"accounts": ["a@x.com"]}))
    assert out["worker"] is True
    assert out["scope"] == {"accounts": ["a@x.com"], "drive_folders": None}
    assert env(await call(mcp, "peek"))["scope"] is None


async def test_invalid_scope_fails_the_call(store_env):
    res = await call(_peek_server(), "peek", sc={"accounts": "a@x.com"})
    assert res.is_error and "accounts must be a non-empty list" in text(res)


async def test_require_scope_refuses_unscoped_calls_only_when_set(store_env, monkeypatch):
    mcp = _peek_server()
    assert env(await call(mcp, "peek"))["scope"] is None
    monkeypatch.setenv("GOOGLE_MCP_REQUIRE_SCOPE", "1")
    res = await call(mcp, "peek")
    assert res.is_error and "scope is required" in text(res)
    assert env(await call(mcp, "peek", sc={"accounts": ["a@x.com"]}))["scope"]


# --- 2. accounts ---------------------------------------------------------------

@pytest.fixture
def two_accounts(store_env, write_token):
    write_token(store_env, "me@personal.com")
    write_token(store_env, "me@work.com")
    core.set_account_alias("work", "me@work.com")
    core.set_account_alias("personal", "me@personal.com")
    return store_env


async def test_read_and_write_outside_accounts_fail_before_the_api(two_accounts):
    built = []
    mcp = build_server("scope-test")

    def factory(acct):
        built.append(acct)
        return object()

    @register(mcp)
    def read_thing(account: str | None = None) -> dict:
        _api, resolved = get_api("x", factory, account)
        return ok(resolved, "read")

    @register(mcp, mutating=True)
    def write_thing(account: str | None = None) -> dict:
        _api, resolved = get_api("x", factory, account)
        return ok(resolved, "wrote")

    sc = {"accounts": ["work"]}
    for name in ("read_thing", "write_thing"):
        res = await call(mcp, name, {"account": "personal"}, sc)
        assert res.is_error and "Outside scope: account me@personal.com" in text(res)
    assert built == []
    assert env(await call(mcp, "write_thing", {"account": "work"}, sc))["data"] == "wrote"


def _gmail_fake():
    path = Path(__file__).with_name("test_gmail_server.py")
    spec = importlib.util.spec_from_file_location("_gmail_fakes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FakeAPI()


async def test_list_messages_from_personal_in_a_work_project(two_accounts, monkeypatch):
    fake = _gmail_fake()
    monkeypatch.setattr(gmail_server, "GmailChangesAPI", lambda acct: fake)
    res = await call(gmail_server.mcp, "list_messages", {"account": "personal"},
                     {"accounts": ["work"]})
    assert res.is_error and "Outside scope: account me@personal.com" in text(res)
    assert fake.calls == {}


def test_search_all_accounts_uses_only_scoped_ones(two_accounts, monkeypatch):
    class FakeGmail:
        def __init__(self, account):
            self.account = account

        def search_with_details(self, **kwargs):
            return {"items": [{"id": f"m-{self.account}", "internalDate": "1"}]}

    monkeypatch.setattr(gmail_server, "GmailChangesAPI", FakeGmail)
    with _Using(scope.parse({"accounts": ["work"]})):
        out = gmail_server.search_messages_all_accounts(query="x")
        assert out["accounts"] == ["me@work.com"]
        assert [m["account"] for m in out["data"]] == ["me@work.com"]
        named = gmail_server.search_messages_all_accounts(query="x", accounts=["personal"])
    assert named["data"] == [] and "Outside scope" in named["errors"]["me@personal.com"]


async def test_list_accounts_shows_only_scoped_accounts(two_accounts):
    data = env(await call(gmail_server.mcp, "list_accounts", sc={"accounts": ["work"]}))["data"]
    assert data["accounts"] == ["me@work.com"]
    assert data["aliases"] == {"work": "me@work.com"}
    assert data["default"] == "me@work.com"
    unscoped = env(await call(gmail_server.mcp, "list_accounts"))["data"]
    assert unscoped["accounts"] == ["me@personal.com", "me@work.com"]
    res = await call(gmail_server.mcp, "whoami", {"account": "personal"}, {"accounts": ["work"]})
    assert res.is_error and "Outside scope: account me@personal.com" in text(res)


# --- 3.1 ancestry --------------------------------------------------------------

# root
# ├── DDB ── specs ── deep1 ── deep2 ── doc_deep
# │          └── doc_specs
# └── doValue ── doc_dovalue
# shared (no parents), loop_a <-> loop_b
TREE = {
    "root": [],
    "DDB": ["root"],
    "doc_ddb": ["DDB"],
    "specs": ["DDB"],
    "doc_specs": ["specs"],
    "deep1": ["specs"],
    "deep2": ["deep1"],
    "doc_deep": ["deep2"],
    "doValue": ["root"],
    "doc_dovalue": ["doValue"],
    "shared": [],
    "loop_a": ["loop_b"],
    "loop_b": ["loop_a"],
}


class Lookup:
    def __init__(self):
        self.calls = []

    def __call__(self, file_id):
        self.calls.append(file_id)
        return TREE[file_id]


@pytest.mark.parametrize("file_id, inside", [
    ("DDB", True),
    ("doc_ddb", True),
    ("doc_deep", True),  # three folders below DDB
    ("doc_dovalue", False),
    ("shared", False),
    ("loop_a", False),
])
def test_in_folders(file_id, inside):
    assert scope.in_folders("me@work.com", file_id, frozenset({"DDB"}), Lookup()) is inside


def test_cache_hit_makes_no_second_request():
    lookup = Lookup()
    assert scope.in_folders("me@work.com", "doc_deep", frozenset({"DDB"}), lookup)
    first = list(lookup.calls)
    assert first == ["doc_deep", "deep2", "deep1", "specs"]
    assert scope.in_folders("me@work.com", "doc_deep", frozenset({"DDB"}), lookup)
    assert scope.in_folders("me@work.com", "doc_specs", frozenset({"DDB"}), lookup)
    assert lookup.calls == first + ["doc_specs"]
    # Another account has its own view of the parents.
    scope.in_folders("me@home.com", "doc_ddb", frozenset({"DDB"}), lookup)
    assert lookup.calls[-1] == "doc_ddb"


def test_forget_and_expiry_drop_cached_parents(monkeypatch):
    lookup = Lookup()
    scope.in_folders("me@work.com", "doc_ddb", frozenset({"DDB"}), lookup)
    scope.forget(["doc_ddb"])
    scope.in_folders("me@work.com", "doc_ddb", frozenset({"DDB"}), lookup)
    assert lookup.calls == ["doc_ddb", "doc_ddb"]
    now = [1000.0]
    monkeypatch.setattr(scope.time, "monotonic", lambda: now[0])
    scope.remember("me@work.com", "x", ["DDB"])
    now[0] += scope.PARENT_TTL + 1
    assert scope._cached("me@work.com", "x") is None


def test_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(scope, "PARENT_MAX", 3)
    for i in range(5):
        scope.remember("me@work.com", f"f{i}", [])
    assert [k[1] for k in scope._parents] == ["f2", "f3", "f4"]


# --- 3.2-3.4 Drive, Docs and Sheets tools --------------------------------------

DDB = {"drive_folders": [{"account": "me@work.com", "id": "DDB"}]}


def _drive_fake():
    fake = MagicMock(spec=DriveAPI)

    def parents(file_id):
        if file_id not in TREE:
            raise core.NotFoundError(f"File not found: {file_id}")
        return list(TREE[file_id])

    fake.get_parents.side_effect = parents
    return fake


@pytest.fixture
def drive(store_env, write_token, monkeypatch):
    write_token(store_env, "me@work.com")
    fake = _drive_fake()
    monkeypatch.setattr(drive_api, "DriveAPI", lambda acct: fake)
    monkeypatch.setattr(drive_server, "DriveAPI", lambda acct: fake)
    return fake


async def test_no_scope_behaves_as_before(drive):
    drive.read_file_text.return_value = {"id": "doc_dovalue", "text": "hi"}
    assert env(await call(drive_server.mcp, "read_file_text", {"file_id": "doc_dovalue"}))["ok"]
    drive.get_parents.assert_not_called()


async def test_read_file_text_inside_and_outside(drive):
    drive.read_file_text.return_value = {"id": "doc_specs", "text": "spec"}
    out = env(await call(drive_server.mcp, "read_file_text", {"file_id": "doc_specs"}, DDB))
    assert out["data"]["text"] == "spec"
    drive.read_file_text.reset_mock()
    res = await call(drive_server.mcp, "read_file_text", {"file_id": "doc_dovalue"}, DDB)
    assert res.is_error and "Outside scope: file doc_dovalue (file_id)" in text(res)
    drive.read_file_text.assert_not_called()


async def test_move_file_out_of_scope_is_refused(drive):
    res = await call(drive_server.mcp, "move_file",
                     {"file_id": "doc_ddb", "new_parent_id": "doValue"}, DDB)
    assert res.is_error and "Outside scope: file doValue (new_parent_id)" in text(res)
    drive.move_file.assert_not_called()


async def test_batch_items_are_checked(drive):
    res = await call(drive_server.mcp, "batch_move_files", {
        "account": "me@work.com",
        "items": [{"file_id": "doc_ddb", "new_parent_id": "specs"},
                  {"file_id": "doc_dovalue", "new_parent_id": "specs"}],
    }, DDB)
    assert res.is_error and "doc_dovalue (items[1].file_id)" in text(res)
    drive.batch_move_files.assert_not_called()
    res = await call(drive_server.mcp, "batch_create_folders",
                     {"items": [{"name": "x", "parent_id": "DDB"}, {"name": "y"}]}, DDB)
    assert res.is_error and "items[1].parent_id" in text(res)


async def test_drive_id_and_list_drives_are_refused(drive):
    res = await call(drive_server.mcp, "list_drives", {}, DDB)
    assert res.is_error and "Outside scope: list_drives" in text(res)
    res = await call(drive_server.mcp, "search_files", {"name": "x", "drive_id": "SD"}, DDB)
    assert res.is_error and "Outside scope: drive_id" in text(res)
    drive.search_files.assert_not_called()
    # list_changes names changed files anywhere in the Drive.
    res = await call(drive_server.mcp, "list_changes", {"page_token": "1"}, DDB)
    assert res.is_error and "Outside scope: list_changes" in text(res)
    drive.list_changes.assert_not_called()


async def test_an_account_without_folders_reaches_nothing(drive, store_env, write_token):
    write_token(store_env, "me@home.com")
    res = await call(drive_server.mcp, "get_file", {"account": "me@home.com", "file_id": "DDB"},
                     DDB)
    assert res.is_error and "Outside scope: file DDB" in text(res)


async def test_update_range_on_a_spreadsheet_outside(drive, monkeypatch):
    fake = MagicMock(spec=SheetsAPI)
    monkeypatch.setattr(sheets_server, "_api", lambda account=None: (fake, "me@work.com"))
    res = await call(sheets_server.mcp, "update_range",
                     {"spreadsheet_id": "doc_dovalue", "range": "A1", "values": [["x"]]}, DDB)
    assert res.is_error and "Outside scope: file doc_dovalue (spreadsheet_id)" in text(res)
    fake.update_range.assert_not_called()
    fake.update_range.return_value = {"updatedCells": 1}
    assert env(await call(sheets_server.mcp, "update_range",
                          {"spreadsheet_id": "doc_specs", "range": "A1", "values": [["x"]]},
                          DDB))["ok"]


async def test_gmail_message_id_is_not_a_drive_file(store_env, write_token, monkeypatch):
    write_token(store_env, "me@work.com")
    fake = _gmail_fake()
    monkeypatch.setattr(gmail_server, "GmailChangesAPI", lambda acct: fake)
    drive = _drive_fake()
    monkeypatch.setattr(drive_api, "DriveAPI", lambda acct: drive)
    assert env(await call(gmail_server.mcp, "get_message", {"message_id": "doc_dovalue"}, DDB))
    drive.get_parents.assert_not_called()


async def test_search_files_keeps_only_files_in_scope(drive):
    drive.search_files.return_value = {
        "files": [
            {"id": "budget_ddb", "name": "budget", "parents": ["specs"]},
            {"id": "budget_home", "name": "budget", "parents": ["doValue"]},
            {"id": "budget_shared", "name": "budget"},
        ],
        "nextPageToken": "p2",
    }
    out = env(await call(drive_server.mcp, "search_files", {"name": "budget"}, DDB))
    assert [f["id"] for f in out["data"]["files"]] == ["budget_ddb"]
    assert out["next_page_token"] == "p2"
    # The result's parents seed the cache: only ancestors below a root or a
    # scope folder, and the file listed without parents, are looked up.
    assert sorted(c.args[0] for c in drive.get_parents.call_args_list) == [
        "budget_shared", "doValue", "root", "specs",
    ]


async def test_list_files_without_folder_filters_and_with_folder_checks(drive):
    drive.list_files.return_value = {"files": [
        {"id": "doc_ddb", "parents": ["DDB"]}, {"id": "doc_dovalue", "parents": ["doValue"]},
    ]}
    out = env(await call(drive_server.mcp, "list_files", {}, DDB))
    assert [f["id"] for f in out["data"]["files"]] == ["doc_ddb"]
    res = await call(drive_server.mcp, "list_files", {"folder_id": "doValue"}, DDB)
    assert res.is_error and "doValue (folder_id)" in text(res)


async def test_search_all_accounts_keeps_files_of_scoped_folders(store_env, write_token,
                                                                 monkeypatch):
    write_token(store_env, "me@work.com")
    write_token(store_env, "me@home.com")
    fakes = {}

    def factory(acct):
        if acct not in fakes:
            fake = _drive_fake()
            fake.search_files.return_value = {"files": [
                {"id": f"in-{acct}", "parents": ["DDB"], "modifiedTime": "2"},
                {"id": f"out-{acct}", "parents": ["doValue"], "modifiedTime": "1"},
            ]}
            fakes[acct] = fake
        return fakes[acct]

    monkeypatch.setattr(drive_api, "DriveAPI", factory)
    monkeypatch.setattr(drive_server, "DriveAPI", factory)
    out = json.loads(text(await call(drive_server.mcp, "search_files_all_accounts",
                                     {"name": "x"}, DDB)))
    assert [(f["id"], f["account"]) for f in out["data"]] == [("in-me@work.com", "me@work.com")]


async def test_list_folder_tree_only_from_a_scope_folder(drive):
    drive.list_folder_tree.return_value = {"folder": {"id": "specs"}, "files": [],
                                           "truncated": False}
    assert env(await call(drive_server.mcp, "list_folder_tree", {"folder_id": "specs"}, DDB))
    res = await call(drive_server.mcp, "list_folder_tree", {"folder_id": "root"}, DDB)
    assert res.is_error and "Outside scope: file root (folder_id)" in text(res)


@pytest.mark.parametrize("tool, args, param, method", [
    ("upload_file", {"local_path": "/tmp/x.txt"}, "parent_id", "upload_file"),
    ("create_folder", {"name": "n"}, "parent_id", "create_folder"),
    ("copy_file", {"file_id": "doc_ddb"}, "parent_id", "copy_file"),
])
async def test_drive_create_needs_a_parent_in_scope(drive, tool, args, param, method):
    res = await call(drive_server.mcp, tool, args, DDB)
    assert res.is_error and f"Outside scope: {tool} needs {param}" in text(res)
    res = await call(drive_server.mcp, tool, {**args, param: "doValue"}, DDB)
    assert res.is_error and f"doValue ({param})" in text(res)
    getattr(drive, method).assert_not_called()


async def test_create_folder_in_scope_runs(drive):
    drive.create_folder.return_value = {"id": "new"}
    out = env(await call(drive_server.mcp, "create_folder", {"name": "n", "parent_id": "specs"},
                         DDB))
    assert out["data"]["id"] == "new"


@pytest.mark.parametrize("tool, args", [
    ("create_document", {"title": "t"}),
    ("create_document_from_markdown", {"title": "t", "markdown": "# x"}),
])
async def test_docs_create_needs_folder_id(drive, monkeypatch, tool, args):
    fake = MagicMock(spec=DocsAPI)
    getattr(fake, tool).return_value = {"documentId": "D"}
    monkeypatch.setattr(docs_server, "_api", lambda account=None: (fake, "me@work.com"))
    res = await call(docs_server.mcp, tool, args, DDB)
    assert res.is_error and f"Outside scope: {tool} needs folder_id" in text(res)
    getattr(fake, tool).assert_not_called()
    assert env(await call(docs_server.mcp, tool, {**args, "folder_id": "DDB"}, DDB))["ok"]
    assert getattr(fake, tool).call_args.args[-1] == "DDB"


async def test_create_spreadsheet_in_the_project_folder(drive, monkeypatch):
    fake = MagicMock(spec=SheetsAPI)
    fake.create_spreadsheet.return_value = {"spreadsheetId": "S"}
    monkeypatch.setattr(sheets_server, "_api", lambda account=None: (fake, "me@work.com"))
    res = await call(sheets_server.mcp, "create_spreadsheet", {"title": "t"}, DDB)
    assert res.is_error and "needs folder_id" in text(res)
    out = env(await call(sheets_server.mcp, "create_spreadsheet",
                         {"title": "t", "folder_id": "DDB"}, DDB))
    assert out["data"]["spreadsheetId"] == "S"
    fake.create_spreadsheet.assert_called_once_with("t", "DDB")
    # Without a scope, folder_id is optional as before.
    assert env(await call(sheets_server.mcp, "create_spreadsheet", {"title": "t"}))["ok"]


async def test_deck_outside_the_project(drive, monkeypatch):
    """Scenario: read_presentation on a deck in doValue/ under a DDB scope."""
    fake = MagicMock(spec=SlidesAPI)
    fake.read_presentation.return_value = {"presentationId": "doc_specs", "slides": []}
    monkeypatch.setattr(slides_server, "_api", lambda account=None: (fake, "me@work.com"))
    res = await call(slides_server.mcp, "read_presentation", {"presentation_id": "doc_dovalue"}, DDB)
    assert res.is_error and "Outside scope: file doc_dovalue (presentation_id)" in text(res)
    fake.read_presentation.assert_not_called()
    assert env(await call(slides_server.mcp, "read_presentation",
                          {"presentation_id": "doc_specs"}, DDB))["ok"]
    res = await call(slides_server.mcp, "update_slide_text",
                     {"presentation_id": "doc_dovalue", "slide_id": "s", "title": "x"}, DDB)
    assert res.is_error and "doc_dovalue (presentation_id)" in text(res)
    fake.update_slide_text.assert_not_called()


@pytest.mark.parametrize("tool, args", [
    ("create_presentation", {"title": "t"}),
    ("create_presentation_from_outline", {"title": "t", "slides": [{"title": "a"}]}),
])
async def test_slides_create_needs_folder_id_in_scope(drive, monkeypatch, tool, args):
    fake = MagicMock(spec=SlidesAPI)
    getattr(fake, tool).return_value = {"presentationId": "P"}
    monkeypatch.setattr(slides_server, "_api", lambda account=None: (fake, "me@work.com"))
    res = await call(slides_server.mcp, tool, args, DDB)
    assert res.is_error and f"Outside scope: {tool} needs folder_id" in text(res)
    res = await call(slides_server.mcp, tool, {**args, "folder_id": "doValue"}, DDB)
    assert res.is_error and "doValue (folder_id)" in text(res)
    getattr(fake, tool).assert_not_called()
    assert env(await call(slides_server.mcp, tool, {**args, "folder_id": "specs"}, DDB))["ok"]
    assert getattr(fake, tool).call_args.args[-1] == "specs"
    # Without a scope, folder_id stays optional.
    assert env(await call(slides_server.mcp, tool, args))["ok"]


def _with_drive(monkeypatch, primary_name):
    primary, drive_svc = MagicMock(), MagicMock()
    monkeypatch.setattr(
        "google_auth_core.get_service",
        lambda name, version="v1", account=None: drive_svc if name == "drive" else primary,
    )
    drive_svc.files().get().execute.return_value = {"parents": ["root"]}
    return primary, drive_svc


def test_docs_create_document_moves_into_folder(monkeypatch):
    docs_svc, drive_svc = _with_drive(monkeypatch, "docs")
    docs_svc.documents().create().execute.return_value = {"documentId": "D1"}
    assert DocsAPI("x@x.com").create_document("Hi", "DDB")["documentId"] == "D1"
    drive_svc.files().update.assert_called_with(
        fileId="D1", addParents="DDB", removeParents="root", fields="id, parents",
        supportsAllDrives=True,
    )


def test_sheets_create_trashes_the_file_when_the_move_fails(monkeypatch):
    sheets_svc, drive_svc = _with_drive(monkeypatch, "sheets")
    sheets_svc.spreadsheets().create().execute.return_value = {"spreadsheetId": "S1"}
    calls = []

    def update(**kwargs):
        calls.append(kwargs)
        request = MagicMock()
        if "addParents" in kwargs:
            request.execute.side_effect = RuntimeError("move failed")
        return request

    drive_svc.files().update.side_effect = update
    with pytest.raises(RuntimeError, match="move failed"):
        SheetsAPI("x@x.com").create_spreadsheet("T", "DDB")
    assert calls[-1] == {"fileId": "S1", "body": {"trashed": True}, "supportsAllDrives": True}


async def test_mutating_calls_forget_cached_parents(drive):
    drive.move_file.return_value = {"id": "doc_ddb"}
    await call(drive_server.mcp, "get_file", {"file_id": "doc_ddb"}, DDB)
    assert scope._cached("me@work.com", "doc_ddb") == ("DDB",)
    assert env(await call(drive_server.mcp, "move_file",
                          {"file_id": "doc_ddb", "new_parent_id": "specs"}, DDB))
    assert scope._cached("me@work.com", "doc_ddb") is None


# --- 4.1 audit -------------------------------------------------------------------

async def test_audit_records_the_scope(drive, tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("GOOGLE_MCP_AUDIT_LOG", str(log))
    fake = MagicMock(spec=DocsAPI)
    fake.create_document_from_markdown.return_value = {"documentId": "D"}
    monkeypatch.setattr(docs_server, "_api", lambda account=None: (fake, "me@work.com"))
    args = {"title": "t", "markdown": "x", "folder_id": "DDB"}
    sc = {"accounts": ["me@work.com"], **DDB}
    assert env(await call(docs_server.mcp, "create_document_from_markdown", args, sc))
    assert env(await call(docs_server.mcp, "create_document_from_markdown", args))
    scoped, unscoped = [json.loads(line) for line in log.read_text().splitlines()]
    assert scoped["tool"] == "create_document_from_markdown"
    assert scoped["scope"] == {"accounts": ["me@work.com"], "drive_folders": DDB["drive_folders"]}
    assert unscoped["scope"] is None


# --- 4.2 the README example ------------------------------------------------------

def test_readme_example_meta_parses(store_env):
    readme = (ROOT / "README.md").read_text()
    blocks = re.findall(r"```json\n(.*?)```", readme, re.S)
    examples = [json.loads(b) for b in blocks if SCOPE_KEY in b]
    assert examples, "README has no _meta scope example"
    for example in examples:
        sc = scope.from_meta(example["_meta"])
        assert sc is not None and sc.accounts and sc.folders


async def test_combined_server_keeps_the_folder_check(drive):
    from google_workspace_mcp.workspace import server as workspace

    res = await call(workspace.mcp, "read_file_text", {"file_id": "doc_dovalue"}, DDB)
    assert res.is_error and "Outside scope: file doc_dovalue" in text(res)
    drive.read_file_text.assert_not_called()


async def test_batch_update_cannot_name_files_outside_the_scope(drive, monkeypatch):
    """A raw request that embeds another file (a linked Sheets chart, a Drive
    video) is checked like a parameter."""
    fake = MagicMock(spec=SlidesAPI)
    monkeypatch.setattr(slides_server, "_api", lambda account=None: (fake, "me@work.com"))
    for request in ({"createSheetsChart": {"spreadsheetId": "doc_dovalue", "chartId": 1, "linkingMode": "LINKED"}},
                    {"createVideo": {"source": "DRIVE", "id": "doc_dovalue"}}):
        for requests in ([request], json.dumps([request])):
            res = await call(slides_server.mcp, "batch_update",
                             {"presentation_id": "doc_specs", "requests": requests}, DDB)
            assert res.is_error and "Outside scope: file doc_dovalue" in text(res)
    fake.batch_update.assert_not_called()
    fake.batch_update.return_value = {"replies": []}
    ok_request = [{"createSheetsChart": {"spreadsheetId": "doc_specs", "chartId": 1}}]
    assert env(await call(slides_server.mcp, "batch_update",
                          {"presentation_id": "doc_specs", "requests": ok_request}, DDB))["ok"]
