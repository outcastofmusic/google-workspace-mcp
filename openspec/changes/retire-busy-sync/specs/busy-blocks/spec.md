## ADDED Requirements

### Requirement: No busy-block writer
No tool of the Calendar server or of `gworkspace-mcp` SHALL create, move or delete a busy block,
an event whose `extendedProperties.private` holds `gwmcp_block = "1"`. `list_tools` SHALL hold no
`sync_busy_blocks` in any toolset, with or without `GOOGLE_MCP_READONLY`, and the read-only gate
SHALL hide and show the other tools as before.

#### Scenario: Default server
- **WHEN** `gcal-mcp` or `gworkspace-mcp` starts with no `GOOGLE_MCP_READONLY` and `GOOGLE_MCP_TOOLSET` all or core
- **THEN** `list_tools` holds no tool named for busy blocks, and holds `list_events` and `create_event`

#### Scenario: Read-only server
- **WHEN** `gcal-mcp` or `gworkspace-mcp` starts with `GOOGLE_MCP_READONLY=1`
- **THEN** `list_tools` holds no tool named for busy blocks, holds `list_events`, and does not hold `create_event`

### Requirement: Retired busy-sync command
`gcal-busy-sync` SHALL stay installed as a console script. With any arguments it SHALL call no
Google API, print nothing to stdout, print to stderr that it is retired, that bj writes the blocks
through `bj sync`, and how to stop a systemd timer (found by name, not assumed), a cron line, a
launchd agent (unloaded, then its `.plist` removed) and an `--interval` loop, and exit with
status 2.

#### Scenario: Timer left behind
- **WHEN** a systemd timer runs `gcal-busy-sync --interval 10` after the upgrade
- **THEN** the command exits 2 at once, changes no calendar, and its log names `bj sync`, how to find the unit with `systemctl --user list-units --all | grep -i busy`, and `systemctl --user disable --now <unit>.service <unit>.timer`

#### Scenario: Dry run
- **WHEN** the user runs `gcal-busy-sync --dry-run`
- **THEN** it reads no calendar and exits 2 with the same message

### Requirement: Shared block marker
`google_workspace_mcp.calendar.busy_sync` SHALL define `MARKER = "gwmcp_block"` and
`KEY = "gwmcp_key"`, the private extended property names bj writes and reads, and `is_block(event)`,
true when the event's `gwmcp_block` is `"1"`. Their documentation SHALL say bj depends on them.

#### Scenario: bj's block
- **WHEN** `is_block` gets an event whose private properties are `gwmcp_block = "1"` and `gwmcp_key = "kaizen-0123456789abcdef"`
- **THEN** it returns true, and an event without `gwmcp_block = "1"` returns false
