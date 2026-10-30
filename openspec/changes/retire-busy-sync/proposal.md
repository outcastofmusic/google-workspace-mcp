# Proposal

## Why

bj's calendar mirror (bujo-md, capability `calendar-sync`, requirement "Busy blocks") keeps one
private `Busy` event on each local account's calendar for every timed event of the other accounts.
It marks each block with `extendedProperties.private.gwmcp_block = "1"`, stores its event id under
`gwmcp_key`, and deletes every marked block it did not write. This package's `sync_busy_blocks`
tool and `gcal-busy-sync` command use the same marker, store a hash under `gwmcp_key`, and also
delete every marked block they did not plan. With both running, each run deletes the other's
blocks and creates its own, so every busy slot is blocked twice or flickers between runs. bj's
README already tells the user to stop this sync. This change makes bj the only writer.

## What Changes

- **Removed tool:** `sync_busy_blocks` (Calendar server and `gworkspace-mcp`; mutating). No tool
  replaces it. No tool is added or changed otherwise; `freebusy_query` and
  `find_available_slots` (read-only) stay.
- **Retired command:** `gcal-busy-sync` stays as a console script that, with any arguments, calls
  no Google API, prints to stderr that bj writes the blocks (`bj sync`) and how to stop a systemd
  timer, cron line, launchd agent or `--interval` loop, and exits with status 2.
- **Kept contract:** `google_workspace_mcp.calendar.busy_sync` keeps `MARKER = "gwmcp_block"`,
  `KEY = "gwmcp_key"` and `is_block`, documented as the names bj shares.
- **Removed code:** `busy_sync.plan`, `apply`, `sync` and the four raw methods of
  `CalendarChangesAPI` that only the sync used (`list_window_events`, `insert_busy_block`,
  `patch_busy_block`, `delete_busy_block`).
- README: the "Busy-time sync" section becomes "Busy blocks moved to bj", with the steps to stop an
  installed schedule on each computer. CLAUDE.md and `openspec/config.yaml` say the command is
  retired.
- No version bump and no release.

New dependencies: none.

## Capabilities

### New Capabilities

- `busy-blocks`: what this package does about busy blocks now that bj writes them.

### Modified Capabilities

None. `client-scope` never named `sync_busy_blocks`; the README's scope section did and is updated.

## Impact

`google_workspace_mcp/calendar/{busy_sync,server,changes_api}.py`, `tests/test_busy_sync.py`,
`tests/test_calendar_server.py` (tool count 32 to 31), `tests/test_scope.py` (the busy-sync scope
test goes with the sync), README, CLAUDE.md, `openspec/config.yaml`.

Users: until a release holds this change, clients pin `@main` or a commit SHA, or stay on v0.8.1
and never call `sync_busy_blocks`. A client pinned to v0.8.1 or earlier still lists it until it moves to a
version with this change. An installed schedule keeps failing with status 2 until the user stops
it; the message says how.
