"""Busy-block sync: busy time on one calendar blocks the other accounts' calendars."""

import datetime
import json

import google_auth_core as core
import pytest

from google_workspace_mcp.calendar import busy_sync
from google_workspace_mcp.calendar.busy_sync import KEY, MARKER, block_key, plan

NOW = datetime.datetime(2026, 10, 26, 6, 0, tzinfo=datetime.timezone.utc)


def ev(id, start="2026-10-26T09:00:00Z", end="2026-10-26T10:00:00Z", **extra):
    out = {
        "id": id,
        "iCalUID": f"{id}@google.com",
        "summary": f"Secret {id}",
        "start": {"dateTime": start},
        "end": {"dateTime": end},
    }
    out.update(extra)
    return out


def block(id, source, start=None, end=None, summary="Busy"):
    s = source["start"] if start is None else {"dateTime": start}
    e = source["end"] if end is None else {"dateTime": end}
    return {
        "id": id,
        "summary": summary,
        "start": s,
        "end": e,
        "extendedProperties": {"private": {MARKER: "1", KEY: block_key(source)}},
    }


# --- plan ----------------------------------------------------------------------

def test_each_busy_event_blocks_every_other_account():
    work, home = ev("w1"), ev("h1", "2026-10-26T18:00:00Z", "2026-10-26T19:00:00Z")
    changes = plan({"work": [work], "home": [home], "side": []})
    assert len(changes["home"]["create"]) == 1
    assert len(changes["work"]["create"]) == 1
    assert len(changes["side"]["create"]) == 2

    body = changes["home"]["create"][0]
    assert body["summary"] == "Busy"
    assert body["visibility"] == "private" and body["transparency"] == "opaque"
    assert body["start"] == {"dateTime": "2026-10-26T09:00:00+00:00"}
    assert body["extendedProperties"]["private"] == {MARKER: "1", KEY: block_key(work)}
    # Nothing from the source event leaks into the block.
    assert "Secret" not in json.dumps(body)
    assert "attendees" not in body and "description" not in body and "location" not in body


@pytest.mark.parametrize("extra", [
    {"transparency": "transparent"},
    {"status": "cancelled"},
    {"eventType": "workingLocation"},
    {"attendees": [{"email": "me@w.com", "self": True, "responseStatus": "declined"}]},
])
def test_free_declined_and_cancelled_events_are_not_busy(extra):
    assert plan({"work": [ev("w1", **extra)], "home": []})["home"]["create"] == []


def test_all_day_events_only_with_flag():
    allday = {"id": "a", "iCalUID": "a", "start": {"date": "2026-10-27"}, "end": {"date": "2026-10-28"}}
    assert plan({"work": [allday], "home": []})["home"]["create"] == []
    body = plan({"work": [allday], "home": []}, include_all_day=True)["home"]["create"][0]
    assert body["start"] == {"date": "2026-10-27"}


def test_meeting_already_on_both_calendars_gets_no_block():
    shared_w = ev("m1", start="2026-10-26T11:00:00+02:00", end="2026-10-26T12:00:00+02:00")
    shared_h = ev("m1", start="2026-10-26T09:00:00Z", end="2026-10-26T10:00:00Z")
    changes = plan({"work": [shared_w], "home": [shared_h]})
    assert changes == {
        "work": {"create": [], "update": [], "delete": []},
        "home": {"create": [], "update": [], "delete": []},
    }


def test_same_meeting_on_two_sources_makes_one_block():
    changes = plan({"work": [ev("m1")], "home": [ev("m1")], "side": []})
    assert len(changes["side"]["create"]) == 1


def test_blocks_are_never_sources():
    src = ev("h1")
    changes = plan({"home": [src], "work": [block("b1", src)]})
    assert changes["home"]["create"] == []  # the work block does not echo back
    assert changes["work"] == {"create": [], "update": [], "delete": []}


def test_up_to_date_block_is_left_alone_even_across_time_zones():
    src = ev("h1")
    same = block("b1", src, start="2026-10-26T11:00:00+02:00", end="2026-10-26T12:00:00+02:00")
    assert plan({"home": [src], "work": [same]})["work"] == {"create": [], "update": [], "delete": []}


def test_moved_source_updates_its_block():
    src = ev("h1", start="2026-10-26T15:00:00Z", end="2026-10-26T16:00:00Z")
    stale = block("b1", src, start="2026-10-26T09:00:00Z", end="2026-10-26T10:00:00Z")
    upd = plan({"home": [src], "work": [stale]})["work"]["update"]
    assert upd[0]["event_id"] == "b1"
    assert upd[0]["body"]["start"] == {"dateTime": "2026-10-26T15:00:00+00:00"}


def test_gone_source_deletes_block_but_never_real_events():
    gone = ev("h-gone")
    work = [ev("w-real"), block("b1", gone), block("b2", ev("h1")), block("b3", ev("h1"))]
    changes = plan({"home": [ev("h1")], "work": work})
    assert sorted(changes["work"]["delete"]) == ["b1", "b3"]  # stale + duplicate
    assert "w-real" not in changes["work"]["delete"]


def test_deletes_can_be_suspended():
    changes = plan({"home": [], "work": [block("b1", ev("h1"))]}, allow_deletes=False)
    assert changes["work"]["delete"] == []


def test_recurring_instances_get_separate_blocks():
    a = ev("r_1", iCalUID="r@google.com", originalStartTime={"dateTime": "2026-10-26T09:00:00Z"})
    b = ev("r_2", start="2026-10-27T09:00:00Z", end="2026-10-27T10:00:00Z",
           iCalUID="r@google.com", originalStartTime={"dateTime": "2026-10-27T09:00:00Z"})
    assert len(plan({"home": [a, b], "work": []})["work"]["create"]) == 2


# --- sync against fake calendars ----------------------------------------------

class FakeCalendar:
    calendars: dict = {}
    calls: list = []

    def __init__(self, account):
        self.account = account

    def list_window_events(self, calendar_id, time_min, time_max):
        if self.account == "me@broken.com":
            raise core.InvalidArgumentError("boom")
        return list(self.calendars.get(self.account, []))

    def insert_busy_block(self, calendar_id, body):
        self.calls.append(("insert", self.account, body["start"]))
        return {"id": "new"}

    def patch_busy_block(self, calendar_id, event_id, body):
        self.calls.append(("patch", self.account, event_id))
        return {"id": event_id}

    def delete_busy_block(self, calendar_id, event_id):
        self.calls.append(("delete", self.account, event_id))
        return True


@pytest.fixture
def fake_calendars(store_env, write_token, monkeypatch):
    write_token(store_env, "me@work.com")
    write_token(store_env, "me@home.com")
    from google_workspace_mcp.calendar import changes_api

    monkeypatch.setattr(changes_api, "CalendarChangesAPI", FakeCalendar)
    FakeCalendar.calls = []
    FakeCalendar.calendars = {
        "me@work.com": [ev("w1"), block("b-old", ev("h-gone"))],
        "me@home.com": [ev("h1", "2026-10-26T18:00:00Z", "2026-10-26T19:00:00Z")],
    }
    return FakeCalendar


def test_sync_applies_the_plan(fake_calendars):
    out = busy_sync.sync(now=NOW)
    assert out["ok"] and out["accounts"] == ["me@home.com", "me@work.com"]
    assert out["planned"]["me@work.com"] == {"create": 1, "update": 0, "delete": 1}
    assert out["applied"]["me@home.com"] == {"created": 1, "updated": 0, "deleted": 0}
    assert ("delete", "me@work.com", "b-old") in fake_calendars.calls
    assert out["window"]["time_min"].startswith("2026-10-26T06:00:00")
    assert out["window"]["time_max"].startswith("2026-11-09T06:00:00")


def test_dry_run_changes_nothing(fake_calendars):
    out = busy_sync.sync(dry_run=True, now=NOW)
    assert out["planned"]["me@home.com"]["create"] == 1
    assert fake_calendars.calls == [] and "applied" not in out


def test_failed_account_suspends_deletes(fake_calendars, store_env, write_token):
    write_token(store_env, "me@broken.com")
    out = busy_sync.sync(now=NOW)
    assert "boom" in out["errors"]["me@broken.com"][0]
    assert "no blocks were deleted" in out["note"]
    assert not any(c[0] == "delete" for c in fake_calendars.calls)


def test_sync_needs_two_accounts_and_a_sane_window(store_env, write_token):
    write_token(store_env, "me@work.com")
    with pytest.raises(core.InvalidArgumentError, match="two accounts"):
        busy_sync.sync()
    with pytest.raises(core.InvalidArgumentError, match="days_ahead"):
        busy_sync.sync(days_ahead=0)


def test_cli_dry_run_prints_json(fake_calendars, capsys):
    assert busy_sync.main(["--dry-run", "--days", "7"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["dry_run"] is True and fake_calendars.calls == []


@pytest.mark.anyio
async def test_tool_is_registered_as_mutating():
    from google_workspace_mcp.calendar import server

    tool = next(t for t in await server.mcp.list_tools() if t.name == "sync_busy_blocks")
    assert tool.annotations.read_only_hint is False
    assert tool.annotations.destructive_hint is False
