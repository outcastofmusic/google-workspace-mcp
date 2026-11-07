# Tasks

## 1. Code

- [x] 1.1 Add `_event_record(event)` to `calendar/server.py` and use it in `list_events`, `search_events`, `get_recurring_event_instances` and `list_events_all_accounts`, after the sort; say in each docstring that it returns short records and that `get_event` returns the whole event. Verify: `test_event_record_keeps_only_listed_keys`, `test_event_record_all_day`, `test_event_record_cuts_description`, `test_event_record_html_description`, `test_event_record_video_link` (Meet, and a Zoom entry point), `test_event_record_attendee_names`, `test_event_record_omits_defaults`, and one test per tool that its `data` holds records and keeps `next_page_token` (and `account` for all accounts).
- [x] 1.2 Check the size on a recorded day: the 15 events of 9 November 2026 give a `list_events` text result of at most 6,000 characters. Verify: `test_recorded_day_fits`, with a fixture of those 15 events with ids, emails and links replaced.
- [x] 1.3 `get_event` returns the raw resource unchanged. Verify: the existing `get_event` test, plus an assertion that `htmlLink` survives.

## 2. Docs and gates

- [x] 2.1 README: the Calendar section says the list tools return short records and names `get_event`. CLAUDE.md: the architecture block names `_event_record`.
- [x] 2.2 Run `ruff check .` and `pytest` with `GOOGLE_MCP_LIVE` unset.
  - Done 2026-11-07: ruff clean; 702 passed, 11 skipped. Seven probes (raw items in `list_events`, raw items in `list_events_all_accounts`, no HTML strip, no video fallback, an off-by-one cut, the owner shown as organizer, the account dropped) each fail a test. The recorded day gives 5,508 characters.
- [x] 2.3 Release v0.10.0: bump `pyproject.toml`, `google_workspace_mcp/__version__`, the tag in `.mcp.json` and every `@vX.Y.Z` in the README in one commit; after the PR merges, tag `main`; reinstall `gcal-mcp` with `uv tool`.
