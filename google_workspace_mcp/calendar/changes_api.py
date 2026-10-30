"""Calendar syncToken-based change feed (subclass of upstream CalendarAPI)."""
from __future__ import annotations

from google_calendar_cli.api import CalendarAPI
from google_calendar_cli.utils import parse_datetime
from googleapiclient.errors import HttpError

_SYNC_EXPIRED = (
    "sync_token expired; call list_event_changes without sync_token to re-baseline"
)

_EVENT_FIELDS = (
    "nextPageToken,nextSyncToken,"
    "items(id,status,summary,start,end,updated,organizer,recurringEventId)"
)

_STATUS_EVENT_TYPES = {
    "outOfOffice": {
        "summary": "Out of office",
        "properties_key": "outOfOfficeProperties",
        "decline_message": "Declining because I am out of office.",
        "extra": {},
    },
    "focusTime": {
        "summary": "Focus time",
        "properties_key": "focusTimeProperties",
        "decline_message": "Declining because I am in focus time.",
        "extra": {"chatStatus": "doNotDisturb"},
    },
}


def trim_event(event):
    """Return a compact event dict for change-feed responses."""
    return {
        "id": event.get("id"),
        "status": event.get("status"),
        "summary": event.get("summary"),
        "start": event.get("start"),
        "end": event.get("end"),
        "updated": event.get("updated"),
        "organizer": event.get("organizer"),
        "recurringEventId": event.get("recurringEventId"),
    }


class CalendarChangesAPI(CalendarAPI):
    """CalendarAPI plus syncToken-based incremental event listing."""

    def list_event_changes(
        self,
        calendar_id="primary",
        sync_token=None,
        page_token=None,
        max_results=250,
        single_events=False,
    ):
        params = {
            "calendarId": calendar_id,
            "maxResults": max_results,
            "showDeleted": True,
            "singleEvents": single_events,
            "fields": _EVENT_FIELDS,
        }
        if sync_token:
            params["syncToken"] = sync_token
        if page_token:
            params["pageToken"] = page_token
        try:
            result = self.service.events().list(**params).execute()
        except HttpError as error:
            if error.resp.status == 410:
                raise ValueError(_SYNC_EXPIRED) from error
            raise
        return {
            "events": [trim_event(e) for e in result.get("items", [])],
            "next_page_token": result.get("nextPageToken"),
            "new_sync_token": result.get("nextSyncToken"),
        }

    def create_status_event(
        self,
        event_type,
        start_time,
        end_time,
        timezone="UTC",
        calendar_id="primary",
        summary=None,
        decline_message=None,
        auto_decline=True,
    ):
        spec = _STATUS_EVENT_TYPES.get(event_type)
        if spec is None:
            raise ValueError('event_type must be "outOfOffice" or "focusTime"')
        if calendar_id not in (None, "", "primary"):
            raise ValueError("out-of-office and focus time only work on the primary calendar")
        calendar_id = "primary"
        if not start_time or not end_time:
            raise ValueError("start_time and end_time are required")
        start_dt = parse_datetime(start_time)
        end_dt = parse_datetime(end_time)
        if not start_dt or not end_dt:
            raise ValueError("start_time and end_time must be parseable datetimes")
        properties = {
            "autoDeclineMode": (
                "declineOnlyNewConflictingInvitations" if auto_decline else "declineNone"
            ),
            "declineMessage": decline_message or spec["decline_message"],
            **spec["extra"],
        }
        event = {
            "eventType": event_type,
            "summary": summary or spec["summary"],
            "transparency": "opaque",
            "start": {"dateTime": start_dt.isoformat(), "timeZone": timezone},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": timezone},
            spec["properties_key"]: properties,
        }
        return self.service.events().insert(calendarId=calendar_id, body=event).execute()
