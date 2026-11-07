# Proposal

## Why

On 2026-11-07 standard questions to the Eurycleia agents found three more results that fail an
agent, after `compact-calendar-events` fixed the calendar list:

- `search_messages` for the last 7 days returned 78,055 characters for 10 messages. 75,140 of them
  are `payload.headers`: all 34 raw headers of each message (`Received`, DKIM, ARC). The result
  overflows Eurycleia's 50,000-character cap, so the agent sees a cut list.
- `find_available_slots` and `create_event`, `create_out_of_office` and `create_focus_time` default
  `timezone` to UTC. For a person in Europe/Athens, slots for working hours 9 to 18 come back as
  11:00 to 20:00 local, and `create_event` with `2026-11-10T10:00:00` books 12:00 local.
- `read_file_text`, `get_message`, `get_thread` and the other paged text tools cap at 100,000
  characters by default. A client that cuts at 50,000 loses the end of the JSON, where
  `next_offset` sits, so the agent cannot read on. One Google Doc returned 39,476 characters.

## What Changes

- **BREAKING** `search_messages` and `search_messages_all_accounts` return short message records
  with the keys `get_message` already uses: `id`, `threadId`, `labelIds`, `subject`, `from`, `to`,
  `date`, `snippet`, plus `cc` when present and `account` from the all-accounts tool. Their
  `format` parameter goes away. `get_message` and `get_thread` still return a message whole.
- The four calendar tools default `timezone` to the calendar's own time zone (the account's
  Calendar setting), and fall back to UTC only when the setting cannot be read. An explicit
  `timezone` still wins.
- The default page for paged text drops from 100,000 to 40,000 characters.

Release v0.11.0. No new dependencies.

## Capabilities

### New Capabilities

- `mail-search`: the shape of the messages the Gmail search tools return.
- `calendar-time-zone`: the time zone a calendar tool assumes when none is given.
- `text-paging`: the default size of a page of text.

### Modified Capabilities

None.

## Impact

- `google_workspace_mcp/gmail/server.py`, `gmail/changes_api.py` (a record projection beside
  `project_message_text`).
- `google_workspace_mcp/calendar/server.py`.
- `google_workspace_mcp/core/runtime.py` (`DEFAULT_MAX_CHARS`) and every docstring that says 100000.
- Tests for each; README and CLAUDE.md.
