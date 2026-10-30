# Design

## Context

Two programs wrote busy blocks with one marker. This package (since the busy sync was added)
plans blocks per account from the events it lists, keyed by a SHA-256 of the source's iCalUID and
original start, and deletes marked blocks whose key it did not plan. bj (`internal/calsync.Blocks`)
plans blocks from the journal's `^gc-` lines, keyed by the event id, and deletes marked blocks
whose key it did not plan. bj also sees the other laptop's accounts through git, which this
package cannot, so bj is the writer to keep. bj reads `gwmcp_block` and `gwmcp_key` by name, so
the names must not change.

## Goals / Non-Goals

**Goals:** no code in this package creates, moves or deletes a busy block; a schedule left
behind on a laptop says how to stop it; the marker stays documented for bj.

**Non-Goals:** deleting the blocks this package wrote (bj does that on its next sync for local
accounts); a release; changing bj.

## Decisions

| Date | Decision | Alternative rejected |
|---|---|---|
| 2026-10-30 | Remove `sync_busy_blocks` from the servers entirely | A stub tool that refuses with a message pointing to `bj sync`: every client would still pay its schema in context on each turn (the main client runs a local model where schema size matters), and a model that sees a tool tends to call it |
| 2026-10-30 | Add no read-only listing tool (`list_busy_blocks`) | A listing tool to check the switch-over: bj prints each block change and `bj sync --dry-run` shows the plan, `list_events` already returns the private `Busy` events, and a new tool costs schema context for a one-time check |
| 2026-10-30 | Keep `gcal-busy-sync` as a console script that refuses with any arguments and exits 2 | Removing the entry point: a cron job or systemd timer left behind would fail with "command not found" (status 127) and nothing in the log would name bj or the fix. Also rejected: keeping `--dry-run` as a read-only plan, which would describe a writer that no longer exists and disagree with bj's plan |
| 2026-10-30 | Exit status 2, message on stderr, stdout empty | Status 0: a timer that does nothing but succeeds hides that it is still installed; `systemctl --user --failed` and cron mail show a non-zero status |
| 2026-10-30 | Keep `MARKER`, `KEY` and `is_block` in `busy_sync.py`, documented as bj's contract; delete `plan`, `apply`, `sync` and the raw write methods of `CalendarChangesAPI` | Keeping the write code unused "in case": dead write paths can be wired back into a tool by mistake, and the tests that kept them alive would test nothing that runs |
| 2026-10-30 | No version bump | A release now: the user asked for none; the README says a pinned v0.8.1 client still lists the tool until it moves to a newer tag |

**Untrusted content.** The change removes a mutating tool, so an instruction hidden in mail or a
document can no longer make an agent rewrite blocks on every calendar. The retired command reads
no input but its arguments, which it ignores.

## Risks / Trade-offs

- [Clients pinned to v0.8.1 still list the tool] → README step 2; the tool is mutating, so it is
  absent under `GOOGLE_MCP_READONLY` and a confirming client asks first.
- [An `--interval` loop started before the upgrade keeps running old code] → the message and the
  README say `pkill -f gcal-busy-sync`.
- [Blocks on a calendar bj does not sync stay] → README says to delete them by hand.
- [bj changes its marker] → bj's spec names `gwmcp_block` and `gwmcp_key`; `test_marker_names_match_bj`
  pins this side.

## Self-audit, 2026-10-30

No `openspec-audit` skill is installed in this session, so the change was audited by hand
against CLAUDE.md and `openspec/config.yaml`:

| Check | Result |
|---|---|
| Proposal names every tool added or changed, read-only or mutating | One mutating tool removed; none added |
| New dependencies | None |
| Read-only gate | No tool's flags change; a test lists tools for `calendar` and `gworkspace-mcp` with `GOOGLE_MCP_READONLY` 0 and 1 and `GOOGLE_MCP_TOOLSET` all and core |
| Tool contract | No tool added; nothing builds a client outside `get_api` |
| Spec scenarios testable | Each scenario maps to a test in `tests/test_busy_sync.py` |
| Matches bj's spec | `calendar-sync` "Busy blocks" names `gwmcp_block = "1"` and `gwmcp_key`; scenario "Blocks of the fork's busy sync" expects this sync stopped |
| Tasks end with README, CLAUDE.md, ruff, pytest | Yes (3.x) |
