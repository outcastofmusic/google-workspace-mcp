"""Retired busy-block sync: bj writes the blocks, this package writes none."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from google_workspace_mcp.calendar import busy_sync
from google_workspace_mcp.calendar.changes_api import CalendarChangesAPI

# --- the marker bj shares ----------------------------------------------------

def test_marker_names_match_bj():
    # bj (internal/adapters/gcal) writes and finds its blocks by these names.
    assert busy_sync.MARKER == "gwmcp_block"
    assert busy_sync.KEY == "gwmcp_key"


def test_is_block_reads_the_marker():
    block = {"extendedProperties": {"private": {"gwmcp_block": "1", "gwmcp_key": "kaizen-0123456789abcdef"}}}
    assert busy_sync.is_block(block)
    assert not busy_sync.is_block({"summary": "Busy"})
    assert not busy_sync.is_block({"extendedProperties": {"private": {"gwmcp_block": "0"}}})


# --- the retired command -----------------------------------------------------

_STOP_SYSTEMD = (
    "systemctl --user list-units --all | grep -i busy",
    "systemctl --user list-unit-files | grep -i busy",
    "systemctl --user disable --now <unit>.service <unit>.timer",
)


def _assert_refusal(err: str) -> None:
    assert "retired" in err and "bj sync" in err
    for line in _STOP_SYSTEMD:
        assert line in err
    assert "launchctl bootout" in err and ".plist" in err


@pytest.mark.parametrize(
    "argv", [[], ["--dry-run"], ["--accounts", "work", "personal"], ["--help"]],
)
def test_command_refuses_and_writes_nothing(argv, capsys, monkeypatch):
    import google_auth_core

    from google_workspace_mcp.core import runtime

    def no_google(*args, **kwargs):
        raise AssertionError("gcal-busy-sync must not reach Google")

    monkeypatch.setattr(google_auth_core, "get_service", no_google)
    monkeypatch.setattr(runtime, "get_api", no_google)
    assert busy_sync.main(argv) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    _assert_refusal(captured.err)


# Runs main with Google's client factory made to fail, so code that reached
# Google would exit 1 with the AssertionError instead of 2.
_NO_GOOGLE = (
    "import sys, google_auth_core\n"
    "def no_google(*a, **k):\n"
    "    raise AssertionError('gcal-busy-sync must not reach Google')\n"
    "google_auth_core.get_service = no_google\n"
    "from google_workspace_mcp.calendar.busy_sync import main\n"
    "raise SystemExit(main(sys.argv[1:]))\n"
)


def test_interval_loop_exits_at_once():
    # The old code slept and looped forever here; the timeout fails it instead of hanging.
    out = subprocess.run(
        [sys.executable, "-c", _NO_GOOGLE, "--interval", "10"],
        capture_output=True, text=True, timeout=30,
    )
    assert out.returncode == 2, out.stderr
    assert out.stdout == ""
    _assert_refusal(out.stderr)


def test_console_script_exits_2():
    from importlib.metadata import entry_points

    (script,) = entry_points(group="console_scripts", name="gcal-busy-sync")
    assert script.value == "google_workspace_mcp.calendar.busy_sync:main"
    assert script.load() is busy_sync.main
    out = subprocess.run([sys.executable, "-c", _NO_GOOGLE], capture_output=True, text=True, timeout=30)
    assert out.returncode == 2, out.stderr
    assert "bj sync" in out.stderr


def test_write_helpers_are_gone():
    for name in ("sync", "plan", "apply"):
        assert not hasattr(busy_sync, name), name
    for name in ("insert_busy_block", "patch_busy_block", "delete_busy_block", "list_window_events"):
        assert not hasattr(CalendarChangesAPI, name), name


# --- no tool writes blocks ---------------------------------------------------

@pytest.mark.anyio
async def test_calendar_server_has_no_busy_tool():
    from google_workspace_mcp.calendar import server

    names = {t.name for t in await server.mcp.list_tools()}
    assert not {n for n in names if "busy_block" in n}
    assert "freebusy_query" in names  # the read tool for availability stays


@pytest.mark.anyio
async def test_combined_server_has_no_busy_tool():
    from google_workspace_mcp.workspace import server

    names = {t.name for t in await server.mcp.list_tools()}
    assert not {n for n in names if "busy_block" in n}


@pytest.mark.parametrize("readonly", ["0", "1"])
@pytest.mark.parametrize("toolset", ["all", "core"])
@pytest.mark.parametrize("pkg", ["calendar", "workspace"])
def test_no_busy_tool_in_any_mode(pkg, toolset, readonly):
    code = (
        "import asyncio, json\n"
        f"from google_workspace_mcp.{pkg} import server\n"
        "print(json.dumps(sorted(t.name for t in asyncio.run(server.mcp.list_tools()))))\n"
    )
    env = {**os.environ, "GOOGLE_MCP_TOOLSET": toolset}
    env.pop("GOOGLE_MCP_READONLY", None)
    if readonly == "1":
        env["GOOGLE_MCP_READONLY"] = "1"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    names = set(json.loads(out.stdout))
    assert not {n for n in names if "busy_block" in n}
    # The read-only gate is unaffected: reads stay, writes go.
    assert "list_events" in names
    assert ("create_event" in names) is (readonly == "0")
