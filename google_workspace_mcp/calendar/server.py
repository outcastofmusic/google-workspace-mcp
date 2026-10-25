"""Google Calendar MCP server: events, calendars, availability."""
from __future__ import annotations

import datetime

from ..contacts.tools import register_contacts_tools
from ..core import build_server, get_api, ok, register, run_tool
from ..core.fanout import fan_out, merged, tag
from . import busy_sync
from .changes_api import CalendarChangesAPI

mcp = build_server(
    "gcal-mcp",
    "Google Calendar: list/search events, create/update/delete events, manage calendars, and check availability for one or more accounts.",
    service_name="calendar",
)
register_contacts_tools(mcp, service_name="calendar")


def _api(account=None):
    return get_api("calendar", CalendarChangesAPI, account)


# ---------------------------------------------------------------------------
# READ tools
# ---------------------------------------------------------------------------

@register(mcp)
def get_profile(account: str | None = None) -> dict:
    """Get the authenticated user's primary calendar profile."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_profile())
    return ok(resolved, data)


@register(mcp, core=True)
def list_calendars(
    account: str | None = None,
    page_token: str | None = None,
) -> dict:
    """List all calendars accessible to the account.

    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    page = run_tool(lambda: api.list_calendars_page(page_token=page_token))
    return ok(resolved, page["items"], next_page_token=page.get("nextPageToken"))


@register(mcp)
def get_calendar(account: str | None = None, calendar_id: str = "primary") -> dict:
    """Get metadata for a specific calendar."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_calendar(calendar_id))
    return ok(resolved, data)


@register(mcp, core=True)
def list_events(
    account: str | None = None,
    calendar_id: str = "primary",
    max_results: int = 10,
    time_min: str | None = None,
    time_max: str | None = None,
    single_events: bool = True,
    order_by: str = "startTime",
    page_token: str | None = None,
) -> dict:
    """List events on a calendar within an optional time window (RFC3339 timestamps).

    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    page = run_tool(
        lambda: api.list_events_page(
            calendar_id=calendar_id,
            max_results=max_results,
            time_min=time_min,
            time_max=time_max,
            single_events=single_events,
            order_by=order_by,
            page_token=page_token,
        )
    )
    return ok(resolved, page["items"], next_page_token=page.get("nextPageToken"))



def _start_key(event: dict) -> datetime.datetime:
    """Sortable start time: timed events by instant, all-day events at 00:00 UTC."""
    start = event.get("start") or {}
    raw = start.get("dateTime") or start.get("date")
    if not raw:
        return datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)
    try:
        when = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.datetime.max.replace(tzinfo=datetime.timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when


@register(mcp)
def list_events_all_accounts(
    accounts: list[str] | None = None,
    *,
    time_min: str,
    time_max: str,
    max_results_per_account: int = 25,
) -> dict:
    """List events from every account's primary calendar (or only `accounts`) as one agenda.

    time_min/time_max are RFC3339. Events are sorted by start time and each
    carries an `account` key. An account that fails is listed under `errors`.
    """
    results, errors = fan_out(
        "calendar",
        CalendarChangesAPI,
        accounts,
        lambda api: api.list_events_page(
            calendar_id="primary", max_results=max_results_per_account,
            time_min=time_min, time_max=time_max, single_events=True,
            order_by="startTime", page_token=None,
        ),
    )
    items = [e for acct, page in results.items() for e in tag(acct, page["items"])]
    items.sort(key=_start_key)
    return merged(items, results, errors)


@register(mcp, mutating=True)
def sync_busy_blocks(
    accounts: list[str] | None = None,
    *,
    days_ahead: int = 14,
    include_all_day: bool = False,
    summary: str = "Busy",
    dry_run: bool = False,
) -> dict:
    """Mirror busy time across accounts: each busy event becomes a private "Busy" block on the other accounts' primary calendars.

    Blocks copy only the time slot. The sync creates, moves and deletes only
    its own blocks, never real events. Run with dry_run=True to see the plan.
    Covers now to `days_ahead` days ahead; all-day events are skipped unless
    include_all_day is set.
    """
    return busy_sync.sync(
        accounts,
        days_ahead=days_ahead,
        include_all_day=include_all_day,
        summary=summary,
        dry_run=dry_run,
    )

@register(mcp, core=True)
def search_events(
    account: str | None = None,
    *,
    query: str,
    calendar_id: str = "primary",
    max_results: int = 10,
    page_token: str | None = None,
) -> dict:
    """Search events on a calendar by text query.

    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    page = run_tool(
        lambda: api.search_events_page(
            query=query, calendar_id=calendar_id, max_results=max_results, page_token=page_token
        )
    )
    return ok(resolved, page["items"], next_page_token=page.get("nextPageToken"))


@register(mcp, core=True)
def get_event(
    account: str | None = None,
    *,
    event_id: str,
    calendar_id: str = "primary",
) -> dict:
    """Get a specific event by its ID."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_event(event_id, calendar_id=calendar_id))
    return ok(resolved, data)


@register(mcp)
def get_recurring_event_instances(
    account: str | None = None,
    *,
    event_id: str,
    calendar_id: str = "primary",
    max_results: int = 250,
    page_token: str | None = None,
) -> dict:
    """Get all instances of a recurring event.

    Pass page_token from a previous response's next_page_token to continue.
    """
    api, resolved = _api(account)
    page = run_tool(
        lambda: api.instances_page(
            event_id, calendar_id=calendar_id, max_results=max_results, page_token=page_token
        )
    )
    return ok(resolved, page["items"], next_page_token=page.get("nextPageToken"))


@register(mcp, core=True)
def freebusy_query(
    account: str | None = None,
    *,
    time_min: str,
    time_max: str,
    calendar_ids: list[str] | None = None,
) -> dict:
    """Query free/busy information for one or more calendars (RFC3339 timestamps)."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.freebusy_query(
            time_min=time_min, time_max=time_max, calendar_ids=calendar_ids
        )
    )
    return ok(resolved, data)


@register(mcp, core=True)
def find_available_slots(
    account: str | None = None,
    *,
    attendee_emails: list[str] | None = None,
    duration_minutes: int = 60,
    time_min: str,
    time_max: str,
    working_hours_start: int = 9,
    working_hours_end: int = 18,
    exclude_weekends: bool = True,
    timezone: str = "UTC",
) -> dict:
    """Find available meeting slots when all attendees are free (RFC3339 time_min/time_max)."""
    from datetime import datetime

    api, resolved = _api(account)
    emails = attendee_emails or []

    def _call():
        # Convert ISO strings to datetime objects as required by the API
        t_min = datetime.fromisoformat(time_min.replace("Z", "+00:00"))
        t_max = datetime.fromisoformat(time_max.replace("Z", "+00:00"))
        slots = api.find_available_slots(
            attendee_emails=emails,
            duration_minutes=duration_minutes,
            time_min=t_min,
            time_max=t_max,
            working_hours_start=working_hours_start,
            working_hours_end=working_hours_end,
            exclude_weekends=exclude_weekends,
            timezone=timezone,
        )
        # Serialize datetime tuples to ISO strings for JSON transport
        return [{"start": s.isoformat(), "end": e.isoformat()} for s, e in slots]

    data = run_tool(_call)
    return ok(resolved, data)


@register(mcp)
def get_colors(account: str | None = None) -> dict:
    """Get available colors for calendars and events."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.get_colors())
    return ok(resolved, data)


@register(mcp)
def list_event_changes(
    account: str | None = None,
    calendar_id: str = "primary",
    sync_token: str | None = None,
    page_token: str | None = None,
    max_results: int = 250,
) -> dict:
    """List events changed since sync_token. First call without sync_token
    establishes a baseline (full walk) and returns new_sync_token to save;
    later calls with it return only changes (status 'cancelled' = deleted).
    Follow next_page_token within a poll; save new_sync_token for the next
    poll. If the token has expired the error says to re-baseline."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.list_event_changes(
            calendar_id=calendar_id,
            sync_token=sync_token,
            page_token=page_token,
            max_results=max_results,
        )
    )
    return ok(resolved, data)


# ---------------------------------------------------------------------------
# WRITE tools (mutating)
# ---------------------------------------------------------------------------

@register(mcp, mutating=True, core=True)
def create_event(
    account: str | None = None,
    *,
    summary: str,
    start_time: str | None = None,
    end_time: str | None = None,
    description: str | None = None,
    location: str | None = None,
    calendar_id: str = "primary",
    attendees: list[str] | None = None,
    recurrence: list[str] | None = None,
    timezone: str = "UTC",
    color_id: str | None = None,
    add_meet: bool = False,
) -> dict:
    """Create a new calendar event."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.create_event(
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
            calendar_id=calendar_id,
            attendees=attendees,
            recurrence=recurrence,
            timezone=timezone,
            color_id=color_id,
            add_meet=add_meet,
        )
    )
    return ok(resolved, data)


@register(mcp, mutating=True)
def create_out_of_office(
    account: str | None = None,
    *,
    start_time: str,
    end_time: str,
    timezone: str = "UTC",
    calendar_id: str = "primary",
    summary: str | None = None,
    decline_message: str | None = None,
    auto_decline: bool = True,
) -> dict:
    """Create an out-of-office block on the primary calendar. Declines conflicting invites when auto_decline=True (default)."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.create_status_event(
            event_type="outOfOffice",
            start_time=start_time,
            end_time=end_time,
            timezone=timezone,
            calendar_id=calendar_id,
            summary=summary,
            decline_message=decline_message,
            auto_decline=auto_decline,
        )
    )
    return ok(resolved, data)


@register(mcp, mutating=True)
def create_focus_time(
    account: str | None = None,
    *,
    start_time: str,
    end_time: str,
    timezone: str = "UTC",
    calendar_id: str = "primary",
    summary: str | None = None,
    decline_message: str | None = None,
    auto_decline: bool = True,
) -> dict:
    """Create a focus-time block on the primary calendar. Declines conflicting invites when auto_decline=True (default)."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.create_status_event(
            event_type="focusTime",
            start_time=start_time,
            end_time=end_time,
            timezone=timezone,
            calendar_id=calendar_id,
            summary=summary,
            decline_message=decline_message,
            auto_decline=auto_decline,
        )
    )
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def update_event(
    account: str | None = None,
    *,
    event_id: str,
    calendar_id: str = "primary",
    summary: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    description: str | None = None,
    location: str | None = None,
    attendees: list[str] | None = None,
    recurrence: list[str] | None = None,
    timezone: str | None = None,
    color_id: str | None = None,
    add_meet: bool = False,
    remove_meet: bool = False,
) -> dict:
    """Update fields on an existing calendar event."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.update_event(
            event_id=event_id,
            summary=summary,
            start_time=start_time,
            end_time=end_time,
            description=description,
            location=location,
            calendar_id=calendar_id,
            attendees=attendees,
            recurrence=recurrence,
            timezone=timezone,
            color_id=color_id,
            add_meet=add_meet,
            remove_meet=remove_meet,
        )
    )
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def quick_add_event(
    account: str | None = None,
    *,
    text: str,
    calendar_id: str = "primary",
) -> dict:
    """Create an event from a natural-language description (e.g. 'Meeting tomorrow 3pm')."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.quick_add_event(text=text, calendar_id=calendar_id))
    return ok(resolved, data)


@register(mcp, mutating=True)
def move_event(
    account: str | None = None,
    *,
    event_id: str,
    destination_calendar_id: str,
    calendar_id: str = "primary",
) -> dict:
    """Move an event from one calendar to another."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.move_event(
            event_id=event_id,
            destination_calendar_id=destination_calendar_id,
            calendar_id=calendar_id,
        )
    )
    return ok(resolved, data)


@register(mcp, mutating=True)
def add_attendees(
    account: str | None = None,
    *,
    event_id: str,
    attendee_emails: list[str],
    calendar_id: str = "primary",
    send_updates: str = "all",
) -> dict:
    """Add attendees to an existing event."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.add_attendees(
            event_id=event_id,
            attendee_emails=attendee_emails,
            calendar_id=calendar_id,
            send_updates=send_updates,
        )
    )
    return ok(resolved, data)


@register(mcp, mutating=True)
def remove_attendees(
    account: str | None = None,
    *,
    event_id: str,
    attendee_emails: list[str],
    calendar_id: str = "primary",
    send_updates: str = "all",
) -> dict:
    """Remove attendees from an existing event."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.remove_attendees(
            event_id=event_id,
            attendee_emails=attendee_emails,
            calendar_id=calendar_id,
            send_updates=send_updates,
        )
    )
    return ok(resolved, data)


@register(mcp, mutating=True, core=True)
def respond_to_event(
    account: str | None = None,
    *,
    event_id: str,
    response: str,
    calendar_id: str = "primary",
    send_updates: str = "all",
) -> dict:
    """Respond to a calendar invitation: accept / decline / tentatively accept.

    response: one of "accepted", "declined", "tentative", "needsAction".
    """
    api, resolved = _api(account)
    data = run_tool(lambda: api.respond_to_event(event_id, response, calendar_id=calendar_id, send_updates=send_updates))
    return ok(resolved, data)


@register(mcp, mutating=True)
def propose_new_time(
    account: str | None = None,
    *,
    event_id: str,
    new_start_time: str,
    new_end_time: str,
    calendar_id: str = "primary",
) -> dict:
    """Propose a new time for an event as an attendee (RFC3339 timestamps)."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.propose_new_time(
            event_id=event_id,
            new_start_time=new_start_time,
            new_end_time=new_end_time,
            calendar_id=calendar_id,
        )
    )
    return ok(resolved, data)


@register(mcp, mutating=True)
def create_calendar(
    account: str | None = None,
    *,
    summary: str,
    description: str | None = None,
    timezone: str | None = None,
    color_id: str | None = None,
) -> dict:
    """Create a new secondary calendar."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.create_calendar(
            summary=summary,
            description=description,
            timezone=timezone,
            color_id=color_id,
        )
    )
    return ok(resolved, data)


@register(mcp, mutating=True)
def update_calendar(
    account: str | None = None,
    *,
    calendar_id: str,
    summary: str | None = None,
    description: str | None = None,
    timezone: str | None = None,
    color_id: str | None = None,
) -> dict:
    """Update metadata (name, description, timezone, color) for a calendar."""
    api, resolved = _api(account)
    data = run_tool(
        lambda: api.update_calendar(
            calendar_id=calendar_id,
            summary=summary,
            description=description,
            timezone=timezone,
            color_id=color_id,
        )
    )
    return ok(resolved, data)


# ---------------------------------------------------------------------------
# DESTRUCTIVE tools (mutating + destructive)
# ---------------------------------------------------------------------------

@register(mcp, mutating=True, destructive=True, core=True)
def delete_event(
    account: str | None = None,
    *,
    event_id: str,
    calendar_id: str = "primary",
) -> dict:
    """Permanently delete a calendar event."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_event(event_id, calendar_id=calendar_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def delete_calendar(
    account: str | None = None,
    *,
    calendar_id: str,
) -> dict:
    """Permanently delete a secondary calendar and all its events."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.delete_calendar(calendar_id))
    return ok(resolved, data)


@register(mcp, mutating=True, destructive=True)
def clear_calendar(
    account: str | None = None,
    *,
    calendar_id: str,
) -> dict:
    """Remove all events from a calendar (primary calendar only)."""
    api, resolved = _api(account)
    data = run_tool(lambda: api.clear_calendar(calendar_id))
    return ok(resolved, data)


def main():
    mcp.run()
