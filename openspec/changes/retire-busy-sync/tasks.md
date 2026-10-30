# Tasks

## 1. Code

- [x] 1.1 Remove `sync_busy_blocks` from `calendar/server.py`. Verify: `test_calendar_server_has_no_busy_tool`, `test_combined_server_has_no_busy_tool`, and the tool count in `test_tool_count` drops from 32 to 31.
- [x] 1.2 Reduce `calendar/busy_sync.py` to `MARKER`, `KEY`, `is_block` and a `main` that refuses with any arguments, writes the message to stderr and returns 2; delete `plan`, `apply`, `sync`; keep the `gcal-busy-sync` entry point in `pyproject.toml`. Verify: `test_command_refuses_and_writes_nothing` (four argument lists, `google_auth_core.get_service` patched to fail), `test_interval_loop_exits_at_once` (`--interval 10` in a subprocess with a timeout), `test_console_script_exits_2` (the `console_scripts` entry point resolves to `busy_sync:main`), `test_marker_names_match_bj`, `test_is_block_reads_the_marker`.
- [x] 1.3 Delete `list_window_events`, `insert_busy_block`, `patch_busy_block` and `delete_busy_block` from `CalendarChangesAPI`, and the scope test that drove the sync. Verify: `test_write_helpers_are_gone`; `tests/test_fake_drift.py` passes.

## 2. Read-only gate

- [x] 2.1 Check that no busy-block tool appears and the gate still works in every mode. Verify: `test_no_busy_tool_in_any_mode` for `calendar` and `workspace`, toolset all and core, `GOOGLE_MCP_READONLY` 0 and 1; the existing gate tests in `tests/test_integration.py` pass unchanged.

## 3. Docs and gates

- [x] 3.1 README: replace "Busy-time sync across calendars" with "Busy blocks moved to bj" (what changed, the marker, the steps to stop a systemd timer, cron line, launchd agent or `--interval` loop, the pinned-client caveat); fix the install list and the client-scope line. CLAUDE.md: the architecture block names `busy_sync.py` and that bj is the only writer. `openspec/config.yaml`: the command is retired.
- [x] 3.2 Self-audit (no `openspec-audit` skill installed); recorded in design.md.
- [x] 3.3 Run `ruff check .` and `pytest` with `GOOGLE_MCP_LIVE` unset; push and open the PR.
  - Done: ruff clean; 690 passed, 11 skipped. After review, 2026-10-30: the `--interval 10` and entry-point tests fail on the old code (timeout, missing stub) and pass now.
