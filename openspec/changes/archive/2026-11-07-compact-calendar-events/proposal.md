# Proposal

## Why

On 2026-11-07 the Eurycleia calendar agent answered "what's on my calendar?" with 14 Calendar
calls in 6 model rounds, about 80 s. Its first `list_events` call, 50 events over nine days,
returned 50,058 characters: Google's raw event resource, with `etag`, `htmlLink`, `iCalUID`,
`creator`, `organizer`, `conferenceData`, timestamps and full descriptions. Eurycleia cuts a tool
result at 50,000 characters, so the agent read the range again one day at a time and fed about
150,000 characters of JSON to a local model with a 200k context. One week of events fits in a
few KB once each event keeps what an agent reads from it.

## What Changes

- **Changed tools (all read-only):** `list_events`, `search_events`,
  `get_recurring_event_instances` and `list_events_all_accounts` return one short record per
  event: id, title, start, end, and only when present the status, location, the description as
  plain text cut at 200 characters, the attendees with their names and responses, the organizer
  when it is someone else, the video link (Meet, or another conference's), the recurring event's
  id and a non-default event type. Their docstrings say so
  and name `get_event` for the whole event.
- **Unchanged:** `get_event` returns the full resource, so every field stays reachable. No write
  tool changes; `create_event`, `update_event` and the rest take and return what they did.
- **BREAKING** for a client that read raw fields such as `htmlLink` or `conferenceData` from a
  list tool: it gets them from `get_event` now.
- A release, v0.10.0, after the change merges. The user's machine runs v0.8.1, so the reinstall
  also brings v0.9.0's Slides server and the retired busy sync; no timer or cron line runs
  `gcal-busy-sync` there (checked 2026-11-07).

New dependencies: none.

## Capabilities

### New Capabilities

- `calendar-events`: the shape of the events the Calendar list tools return.

### Modified Capabilities

None.

## Impact

- `google_workspace_mcp/calendar/server.py`: one projection function, used by the four tools.
- `tests/test_calendar_server.py`: the projection and each tool's use of it.
- README and CLAUDE.md: the list tools' output and the `get_event` pointer.
- Clients: Eurycleia's calendar agent (`data/agents/calendar.json`) and mini-web-ui chats that
  use `gcal-mcp`. Neither reads a dropped field from a list tool.
