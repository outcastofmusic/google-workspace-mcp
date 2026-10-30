"""Busy blocks: the marker bj writes, and the retired ``gcal-busy-sync`` command.

bj (github.com/outcastofmusic/bujo-md) writes the busy blocks now: ``bj sync``
keeps one private "Busy" event on each local account's calendar for every
timed event of the other accounts, and deletes every marked block it did not
write. Two writers would delete each other's blocks, so this package writes
none. The ``sync_busy_blocks`` tool is gone, and ``gcal-busy-sync`` refuses to
run (change ``retire-busy-sync``, 2026-10-30).

The marker below is the contract bj shares. A block is an event whose
``extendedProperties.private`` holds ``gwmcp_block = "1"``; bj stores its event
id, ``<alias>-<16 hex digits>`` (``kaizen-0123456789abcdef``), under
``gwmcp_key``. Older versions of this package
stored a 32-hex-digit hash there, so bj treats those blocks as not its own and
deletes them. Keep both names unchanged, or bj stops recognizing its blocks.
"""

from __future__ import annotations

import sys
from typing import Optional

MARKER = "gwmcp_block"
KEY = "gwmcp_key"

RETIRED_MESSAGE = """\
gcal-busy-sync is retired and changed nothing.
bj now writes the busy blocks: run `bj sync` (see the Calendars section of bj's README).
Two writers delete each other's blocks, so stop this command's schedule:
  systemd: find the unit with
             systemctl --user list-units --all | grep -i busy
             systemctl --user list-unit-files | grep -i busy
           then: systemctl --user disable --now <unit>.service <unit>.timer
  cron:    crontab -e, then delete the gcal-busy-sync line
  launchd: launchctl bootout gui/$(id -u) <path to the agent's .plist>,
           then delete the .plist or move it out of ~/Library/LaunchAgents
  a loop started with --interval: pkill -f gcal-busy-sync
Details: "Busy blocks moved to bj" in the google-workspace-mcp README."""


def is_block(event: dict) -> bool:
    """True when ``event`` carries the busy-block marker (bj's or an older fork's)."""
    private = (event.get("extendedProperties") or {}).get("private") or {}
    return private.get(MARKER) == "1"


def main(argv: Optional[list[str]] = None) -> int:
    """``gcal-busy-sync``: refuse with any arguments, write nothing, exit 2.

    The entry point stays so that a cron job or timer left behind prints how
    to stop it, rather than "command not found".
    """
    print(RETIRED_MESSAGE, file=sys.stderr, flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
