"""Cross-account tools: one call searches every configured account."""

import google_auth_core as core
import pytest

from google_workspace_mcp.calendar import server as calendar
from google_workspace_mcp.drive import server as drive
from google_workspace_mcp.gmail import server as gmail


@pytest.fixture
def two_accounts(store_env, write_token):
    write_token(store_env, "me@work.com")
    write_token(store_env, "me@home.com")
    return store_env


class FakeGmail:
    def __init__(self, account):
        self.account = account

    def search_with_details(self, max_results=10, label_ids=None, query=None, format="metadata",
                            page_token=None):
        if self.account == "me@broken.com":
            raise core.InvalidArgumentError("boom")
        date = {"me@work.com": "2000", "me@home.com": "3000"}[self.account]
        return {"items": [{"id": f"m-{self.account}", "internalDate": date, "q": query}]}


class FakeCalendar:
    def __init__(self, account):
        self.account = account

    def list_events_page(self, **kwargs):
        if self.account == "me@work.com":
            return {"items": [
                {"id": "w1", "start": {"dateTime": "2026-10-26T09:00:00+02:00"}},
                {"id": "w2", "start": {"dateTime": "2026-10-26T12:00:00Z"}},
            ]}
        return {"items": [
            {"id": "h1", "start": {"date": "2026-10-26"}},
            {"id": "h2", "start": {"dateTime": "2026-10-26T08:30:00+01:00"}},
        ]}


class FakeDrive:
    def __init__(self, account):
        self.account = account

    def search_files(self, **kwargs):
        t = {"me@work.com": "2026-10-01T00:00:00Z", "me@home.com": "2026-10-20T00:00:00Z"}
        return {"files": [{"id": f"f-{self.account}", "modifiedTime": t[self.account]}]}


def test_gmail_search_merges_every_account_newest_first(two_accounts, monkeypatch):
    monkeypatch.setattr(gmail, "GmailChangesAPI", FakeGmail)
    env = gmail.search_messages_all_accounts(query="invoice")
    assert env["ok"] and env["accounts"] == ["me@home.com", "me@work.com"]
    assert [(m["id"], m["account"]) for m in env["data"]] == [
        ("m-me@home.com", "me@home.com"),
        ("m-me@work.com", "me@work.com"),
    ]
    assert all(m["q"] == "invoice" for m in env["data"])
    assert "errors" not in env


def test_gmail_search_limits_to_listed_accounts_and_resolves_aliases(two_accounts, monkeypatch):
    monkeypatch.setattr(gmail, "GmailChangesAPI", FakeGmail)
    core.set_account_alias("work", "me@work.com")
    env = gmail.search_messages_all_accounts(accounts=["work"], query="x")
    assert env["accounts"] == ["me@work.com"]
    assert [m["account"] for m in env["data"]] == ["me@work.com"]


def test_failing_account_is_reported_not_fatal(two_accounts, write_token, monkeypatch):
    write_token(two_accounts, "me@broken.com")
    monkeypatch.setattr(gmail, "GmailChangesAPI", FakeGmail)
    env = gmail.search_messages_all_accounts(query="x")
    assert env["ok"]
    assert "boom" in env["errors"]["me@broken.com"]
    assert {m["account"] for m in env["data"]} == {"me@work.com", "me@home.com"}


def test_unknown_account_fails_cleanly(two_accounts, monkeypatch):
    monkeypatch.setattr(gmail, "GmailChangesAPI", FakeGmail)
    env = gmail.search_messages_all_accounts(accounts=["ghost@x.com"], query="x")
    assert env["ok"] is False
    assert env["data"] == [] and "ghost@x.com" in env["errors"]


def test_calendar_agenda_sorted_by_instant_across_accounts(two_accounts, monkeypatch):
    monkeypatch.setattr(calendar, "CalendarChangesAPI", FakeCalendar)
    env = calendar.list_events_all_accounts(
        time_min="2026-10-26T00:00:00Z", time_max="2026-10-27T00:00:00Z"
    )
    # h1 all-day 00:00Z, w1 07:00Z, h2 07:30Z, w2 12:00Z
    assert [e["id"] for e in env["data"]] == ["h1", "w1", "h2", "w2"]
    assert {e["id"]: e["account"] for e in env["data"]}["w1"] == "me@work.com"


def test_drive_search_merges_newest_first(two_accounts, monkeypatch):
    monkeypatch.setattr(drive, "DriveAPI", FakeDrive)
    env = drive.search_files_all_accounts(name="report")
    assert [f["id"] for f in env["data"]] == ["f-me@home.com", "f-me@work.com"]


@pytest.mark.anyio
async def test_fanout_tools_are_read_only_and_take_accounts_list():
    for mod, name in (
        (gmail, "search_messages_all_accounts"),
        (calendar, "list_events_all_accounts"),
        (drive, "search_files_all_accounts"),
    ):
        tool = next(t for t in await mod.mcp.list_tools() if t.name == name)
        assert tool.annotations.read_only_hint is True, name
        assert "accounts" in tool.input_schema["properties"], name
        assert "accounts" not in tool.input_schema.get("required", []), name
