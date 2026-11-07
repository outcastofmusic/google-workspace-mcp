# Tasks

## 1. Mail search

- [x] 1.1 `project_message_record(message)` in `gmail/changes_api.py`, reusing `_header_value`; used by `search_messages` and `search_messages_all_accounts` (after `tag`); drop `format`; docstrings name `get_message` for the whole message. Verify: `test_message_record_keys`, `test_message_record_cc_only_when_present`, `test_search_messages_returns_records`, the fan-out test, and `test_recorded_search_fits` (fixture of the 10 messages' headers, ids, addresses and subjects replaced, at most 8,000 characters).

## 2. Calendar time zone

- [x] 2.1 `_zone(api, timezone)` in `calendar/server.py`: the given zone, else the cached account setting, else UTC; used by the four tools, whose `timezone` default becomes None. Verify: `test_slots_default_to_calendar_zone`, `test_create_event_defaults_to_calendar_zone`, `test_zone_falls_back_to_utc`, `test_explicit_zone_wins`.

## 3. Text paging

- [x] 3.1 `DEFAULT_MAX_CHARS = 40_000`; `read_files`' `max_chars_per_file` and `max_chars_total` default to it; every docstring that says 100000 says 40000. Verify: `test_default_page_is_40000` on `read_file_text` with a 60,000-character text.

## 4. Docs, gates, release

- [x] 4.1 README (Gmail search records, the calendar zone, the page size) and CLAUDE.md.
- [x] 4.2 `ruff check .` and `pytest`.
  - Done 2026-11-07: ruff clean; 719 passed, 11 skipped. Eight probes (raw search items in either tool, `cc` always set, the given zone ignored, no cache, UTC slots, the explicit zone losing, a 100,000 page) each fail a test. The recorded search gives 5,278 characters against 77,699.
- [x] 4.3 Release v0.11.0 and reinstall with `uv tool`.
