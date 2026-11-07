"""Tests for the Google Calendar MCP server."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from google_workspace_mcp.calendar import server
from google_workspace_mcp.calendar.changes_api import CalendarChangesAPI


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def fake_api(monkeypatch):
    """Monkeypatch _api so no real auth or network calls occur."""
    fake = MagicMock(spec=CalendarChangesAPI)
    monkeypatch.setattr(server, "_api", lambda account=None: (fake, "test@x.com"))
    return fake


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse(result) -> dict:
    """Extract the ok-envelope dict from an MCPServer call_tool result."""
    assert len(result.content) == 1
    return json.loads(result.content[0].text)


# ---------------------------------------------------------------------------
# 1. Tool list
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    # common
    "list_accounts", "auth_status", "whoami",
    # read
    "get_profile", "list_calendars", "get_calendar", "list_events",
    "search_events", "get_event", "get_recurring_event_instances",
    "freebusy_query", "find_available_slots", "get_colors", "list_event_changes",
    "search_contacts",
    # write
    "create_event", "create_out_of_office", "create_focus_time",
    "update_event", "quick_add_event", "move_event",
    "add_attendees", "remove_attendees", "respond_to_event", "propose_new_time",
    "create_calendar", "update_calendar",
    # destructive
    "delete_event", "delete_calendar", "clear_calendar",
}


@pytest.mark.anyio
async def test_tool_list_includes_expected_names():
    tools = await server.mcp.list_tools()
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS.issubset(names), f"Missing: {EXPECTED_TOOLS - names}"


@pytest.mark.anyio
async def test_tool_count():
    tools = await server.mcp.list_tools()
    # 3 common + 13 read (incl. list_events_all_accounts)
    # + 12 write + 3 destructive = 31 (sync_busy_blocks retired: bj writes busy blocks)
    assert len(tools) == 31


# ---------------------------------------------------------------------------
# 2. Read tool: list_events
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_list_events_returns_envelope(fake_api):
    fake_api.list_events_page.return_value = {"items": [
        {"id": "ev1", "summary": "Standup", "etag": '"1"', "htmlLink": "https://x",
         "start": {"dateTime": "2025-01-06T09:00:00Z"}, "end": {"dateTime": "2025-01-06T09:15:00Z"}}]}

    result = await server.mcp.call_tool(
        "list_events",
        {"calendar_id": "primary", "max_results": 5},
    )
    data = _parse(result)

    assert data["ok"] is True
    assert data["account"] == "test@x.com"
    assert data["data"] == [{"id": "ev1", "summary": "Standup",
                             "start": "2025-01-06T09:00:00Z", "end": "2025-01-06T09:15:00Z"}]
    assert "next_page_token" not in data


@pytest.mark.anyio
async def test_list_events_passes_args_to_api(fake_api):
    fake_api.list_events_page.return_value = {"items": []}

    await server.mcp.call_tool(
        "list_events",
        {
            "calendar_id": "work@example.com",
            "max_results": 20,
            "time_min": "2025-01-01T00:00:00Z",
            "time_max": "2025-01-31T23:59:59Z",
            "single_events": True,
            "order_by": "startTime",
        },
    )

    fake_api.list_events_page.assert_called_once_with(
        calendar_id="work@example.com",
        max_results=20,
        time_min="2025-01-01T00:00:00Z",
        time_max="2025-01-31T23:59:59Z",
        single_events=True,
        order_by="startTime",
        page_token=None,
    )


@pytest.mark.anyio
async def test_list_events_next_page_token_in_envelope(fake_api):
    fake_api.list_events_page.return_value = {
        "items": [{"id": "ev1"}],
        "nextPageToken": "cal_tok_2",
    }

    data = _parse(await server.mcp.call_tool("list_events", {"max_results": 1}))

    assert data["next_page_token"] == "cal_tok_2"


@pytest.mark.anyio
async def test_list_events_page_token_passed_through(fake_api):
    fake_api.list_events_page.return_value = {"items": []}

    await server.mcp.call_tool("list_events", {"page_token": "cal_tok_2"})

    fake_api.list_events_page.assert_called_once()
    assert fake_api.list_events_page.call_args.kwargs["page_token"] == "cal_tok_2"


# ---------------------------------------------------------------------------
# 3. Write tool: create_event
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_create_event_calls_api_with_correct_args(fake_api):
    fake_api.create_event.return_value = {"id": "new_event_123", "summary": "Team lunch"}

    result = await server.mcp.call_tool(
        "create_event",
        {
            "summary": "Team lunch",
            "start_time": "2025-06-10T12:00:00",
            "end_time": "2025-06-10T13:00:00",
            "description": "All-hands lunch",
            "location": "HQ",
            "calendar_id": "primary",
            "attendees": ["alice@x.com", "bob@x.com"],
            "timezone": "UTC",
        },
    )
    data = _parse(result)

    assert data["ok"] is True
    assert data["data"]["id"] == "new_event_123"

    fake_api.create_event.assert_called_once_with(
        summary="Team lunch",
        start_time="2025-06-10T12:00:00",
        end_time="2025-06-10T13:00:00",
        description="All-hands lunch",
        location="HQ",
        calendar_id="primary",
        attendees=["alice@x.com", "bob@x.com"],
        recurrence=None,
        timezone="UTC",
        color_id=None,
        add_meet=False,
    )


@pytest.mark.anyio
async def test_create_out_of_office_calls_status_event(fake_api):
    fake_api.create_status_event.return_value = {
        "id": "ooo1",
        "eventType": "outOfOffice",
    }

    result = await server.mcp.call_tool(
        "create_out_of_office",
        {
            "start_time": "2025-06-16T09:00:00",
            "end_time": "2025-06-20T18:00:00",
            "timezone": "UTC",
            "calendar_id": "primary",
        },
    )
    data = _parse(result)

    assert data["ok"] is True
    fake_api.create_status_event.assert_called_once_with(
        event_type="outOfOffice",
        start_time="2025-06-16T09:00:00",
        end_time="2025-06-20T18:00:00",
        timezone="UTC",
        calendar_id="primary",
        summary=None,
        decline_message=None,
        auto_decline=True,
    )


@pytest.mark.anyio
async def test_create_focus_time_calls_status_event(fake_api):
    fake_api.create_status_event.return_value = {
        "id": "ft1",
        "eventType": "focusTime",
    }

    result = await server.mcp.call_tool(
        "create_focus_time",
        {
            "start_time": "2025-06-16T09:00:00",
            "end_time": "2025-06-16T12:00:00",
            "timezone": "America/New_York",
            "summary": "Deep work",
            "auto_decline": False,
        },
    )
    data = _parse(result)

    assert data["ok"] is True
    fake_api.create_status_event.assert_called_once_with(
        event_type="focusTime",
        start_time="2025-06-16T09:00:00",
        end_time="2025-06-16T12:00:00",
        timezone="America/New_York",
        calendar_id="primary",
        summary="Deep work",
        decline_message=None,
        auto_decline=False,
    )


# ---------------------------------------------------------------------------
# 4. Destructive tool: delete_event
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_delete_event_calls_api(fake_api):
    fake_api.delete_event.return_value = True

    result = await server.mcp.call_tool(
        "delete_event",
        {"event_id": "abc123", "calendar_id": "primary"},
    )
    data = _parse(result)

    assert data["ok"] is True
    assert data["data"] is True
    fake_api.delete_event.assert_called_once_with("abc123", calendar_id="primary")


@pytest.mark.anyio
async def test_delete_event_docstring_has_destructive_marker():
    tools = await server.mcp.list_tools()
    delete_tool = next(t for t in tools if t.name == "delete_event")
    assert "DESTRUCTIVE" in delete_tool.description


# ---------------------------------------------------------------------------
# 5. Additional read tools
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_search_events(fake_api):
    fake_api.search_events_page.return_value = {"items": [{"id": "s1", "summary": "Budget", "iCalUID": "u@g"}]}

    result = await server.mcp.call_tool(
        "search_events",
        {"query": "budget", "calendar_id": "primary", "max_results": 3},
    )
    data = _parse(result)
    assert data["ok"] is True
    assert data["data"] == [{"id": "s1", "summary": "Budget", "start": None, "end": None}]
    fake_api.search_events_page.assert_called_once_with(
        query="budget", calendar_id="primary", max_results=3, page_token=None
    )


@pytest.mark.anyio
async def test_get_event(fake_api):
    fake_api.get_event.return_value = {"id": "ev99", "summary": "Dentist"}

    result = await server.mcp.call_tool(
        "get_event",
        {"event_id": "ev99", "calendar_id": "primary"},
    )
    data = _parse(result)
    assert data["data"]["summary"] == "Dentist"
    fake_api.get_event.assert_called_once_with("ev99", calendar_id="primary")


@pytest.mark.anyio
async def test_list_calendars(fake_api):
    fake_api.list_calendars_page.return_value = {"items": [{"id": "primary"}, {"id": "work"}]}

    result = await server.mcp.call_tool("list_calendars", {})
    data = _parse(result)
    assert len(data["data"]) == 2


@pytest.mark.anyio
async def test_freebusy_query(fake_api):
    fake_api.freebusy_query.return_value = {"calendars": {}}

    result = await server.mcp.call_tool(
        "freebusy_query",
        {
            "time_min": "2025-06-10T09:00:00Z",
            "time_max": "2025-06-10T17:00:00Z",
            "calendar_ids": ["primary"],
        },
    )
    data = _parse(result)
    assert data["ok"] is True
    fake_api.freebusy_query.assert_called_once_with(
        time_min="2025-06-10T09:00:00Z",
        time_max="2025-06-10T17:00:00Z",
        calendar_ids=["primary"],
    )


# ---------------------------------------------------------------------------
# 6. Write tools
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_quick_add_event(fake_api):
    fake_api.quick_add_event.return_value = {"id": "qa1", "summary": "Lunch tomorrow"}

    result = await server.mcp.call_tool(
        "quick_add_event",
        {"text": "Lunch tomorrow 12pm", "calendar_id": "primary"},
    )
    data = _parse(result)
    assert data["data"]["id"] == "qa1"
    fake_api.quick_add_event.assert_called_once_with(
        text="Lunch tomorrow 12pm", calendar_id="primary"
    )


@pytest.mark.anyio
async def test_update_event(fake_api):
    fake_api.update_event.return_value = {"id": "ev5", "summary": "Updated"}

    result = await server.mcp.call_tool(
        "update_event",
        {"event_id": "ev5", "summary": "Updated", "calendar_id": "primary"},
    )
    data = _parse(result)
    assert data["data"]["summary"] == "Updated"


@pytest.mark.anyio
async def test_create_calendar(fake_api):
    fake_api.create_calendar.return_value = {"id": "newcal", "summary": "Work"}

    result = await server.mcp.call_tool(
        "create_calendar",
        {"summary": "Work", "timezone": "America/New_York"},
    )
    data = _parse(result)
    assert data["ok"] is True
    fake_api.create_calendar.assert_called_once_with(
        summary="Work",
        description=None,
        timezone="America/New_York",
        color_id=None,
    )


@pytest.mark.anyio
async def test_delete_calendar(fake_api):
    fake_api.delete_calendar.return_value = True

    result = await server.mcp.call_tool(
        "delete_calendar", {"calendar_id": "oldcal@group.calendar.google.com"}
    )
    data = _parse(result)
    assert data["ok"] is True
    assert data["data"] is True
    fake_api.delete_calendar.assert_called_once_with(
        "oldcal@group.calendar.google.com"
    )


# ---------------------------------------------------------------------------
# Short event records (compact-calendar-events)
# ---------------------------------------------------------------------------

RECORD_KEYS = {"id", "summary", "start", "end", "status", "location", "description", "attendees",
               "organizer", "video", "recurring_event_id", "event_type"}

FULL_EVENT = {
    "kind": "calendar#event", "etag": '"3587631703863294"', "id": "ev1", "status": "tentative",
    "htmlLink": "https://www.google.com/calendar/event?eid=abc", "created": "2025-12-01T09:00:00.000Z",
    "updated": "2025-12-01T09:00:00.000Z", "summary": "Budget review", "location": "Room 4",
    "description": "Agenda", "creator": {"email": "boss@example.org"},
    "organizer": {"email": "boss@example.org", "displayName": "Boss"},
    "start": {"dateTime": "2026-01-05T10:00:00+02:00", "timeZone": "Europe/Athens"},
    "end": {"dateTime": "2026-01-05T11:00:00+02:00", "timeZone": "Europe/Athens"},
    "recurringEventId": "series1", "originalStartTime": {"dateTime": "2026-01-05T10:00:00+02:00"},
    "iCalUID": "series1@google.com", "sequence": 2, "reminders": {"useDefault": True},
    "attendees": [{"email": "me@example.org", "self": True, "responseStatus": "accepted"},
                  {"email": "maria@example.org", "displayName": "Maria", "responseStatus": "needsAction"}],
    "hangoutLink": "https://meet.google.com/abc-defg-hij",
    "conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/abc-defg-hij"}]},
    "eventType": "focusTime",
}


def test_event_record_keeps_only_listed_keys():
    rec = server._event_record(FULL_EVENT)
    assert set(rec) <= RECORD_KEYS
    assert rec == {
        "id": "ev1", "summary": "Budget review", "start": "2026-01-05T10:00:00+02:00",
        "end": "2026-01-05T11:00:00+02:00", "status": "tentative", "location": "Room 4",
        "description": "Agenda", "organizer": "boss@example.org",
        "attendees": [{"email": "me@example.org", "response": "accepted"},
                      {"email": "maria@example.org", "response": "needsAction", "name": "Maria"}],
        "video": "https://meet.google.com/abc-defg-hij", "recurring_event_id": "series1",
        "event_type": "focusTime",
    }


def test_event_record_omits_defaults():
    rec = server._event_record({"id": "e", "summary": "Lunch", "status": "confirmed", "eventType": "default",
                                "organizer": {"email": "me@example.org", "self": True},
                                "start": {"dateTime": "2026-01-05T13:00:00Z"}, "end": {"dateTime": "2026-01-05T14:00:00Z"}})
    assert rec == {"id": "e", "summary": "Lunch", "start": "2026-01-05T13:00:00Z", "end": "2026-01-05T14:00:00Z"}


def test_event_record_all_day():
    rec = server._event_record({"id": "e", "start": {"date": "2026-11-12"}, "end": {"date": "2026-11-13"}})
    assert (rec["start"], rec["end"]) == ("2026-11-12", "2026-11-13")


def test_event_record_cuts_description():
    rec = server._event_record({"id": "e", "description": "x" * 2000})
    assert rec["description"] == "x" * 200 + "…"
    assert server._event_record({"id": "e", "description": "y" * 200})["description"] == "y" * 200


def test_event_record_html_description():
    rec = server._event_record({"id": "e", "description": "<p>Room <b>4</b></p><p>Bring&nbsp;slides</p>"})
    assert rec["description"] == "Room 4 Bring slides"
    assert "description" not in server._event_record({"id": "e", "description": "<p></p>"})


def test_event_record_video_link():
    zoom = {"id": "e", "conferenceData": {"entryPoints": [
        {"entryPointType": "phone", "uri": "tel:+1-555"}, {"entryPointType": "video", "uri": "https://zoom.us/j/1"}]}}
    assert server._event_record(zoom)["video"] == "https://zoom.us/j/1"
    assert "video" not in server._event_record({"id": "e"})


def test_event_record_attendee_names():
    rec = server._event_record({"id": "e", "attendees": [{"email": "a@x.org", "displayName": "Ann", "responseStatus": "declined"}]})
    assert rec["attendees"] == [{"email": "a@x.org", "response": "declined", "name": "Ann"}]


@pytest.mark.anyio
async def test_recorded_day_fits(fake_api):
    # The 15 events of a work Monday as Google returned them on 2026-11-07, with ids, emails,
    # links and dates replaced; raw, the tool's result held 21,412 characters.
    raw = json.loads((Path(__file__).parent / "fixtures" / "calendar_day.json").read_text())
    assert len(raw) == 15
    fake_api.list_events_page.return_value = {"items": raw}
    result = await server.mcp.call_tool("list_events", {"max_results": 50})
    text = result.content[0].text
    assert len(text) <= 6000, len(text)
    assert all(set(r) <= RECORD_KEYS for r in json.loads(text)["data"])


@pytest.mark.anyio
@pytest.mark.parametrize("tool,method,args", [
    ("list_events", "list_events_page", {}),
    ("search_events", "search_events_page", {"query": "x"}),
    ("get_recurring_event_instances", "instances_page", {"event_id": "series1"}),
])
async def test_list_tools_return_records_and_keep_paging(fake_api, tool, method, args):
    getattr(fake_api, method).return_value = {"items": [FULL_EVENT], "nextPageToken": "tok2"}
    data = _parse(await server.mcp.call_tool(tool, args))
    assert data["data"] == [server._event_record(FULL_EVENT)]
    assert "htmlLink" not in data["data"][0]
    assert data["next_page_token"] == "tok2"


@pytest.mark.anyio
async def test_get_event_returns_whole_event(fake_api):
    fake_api.get_event.return_value = FULL_EVENT
    data = _parse(await server.mcp.call_tool("get_event", {"event_id": "ev1"}))
    assert data["data"] == FULL_EVENT


@pytest.mark.anyio
async def test_list_tool_descriptions_name_get_event():
    tools = {t.name: t for t in await server.mcp.list_tools()}
    for name in ("list_events", "search_events", "get_recurring_event_instances", "list_events_all_accounts"):
        assert "short record" in tools[name].description and "get_event" in tools[name].description, name
