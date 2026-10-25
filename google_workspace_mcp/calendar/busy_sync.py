"""Busy-block sync: mirror every busy event onto the other accounts' calendars.

With several accounts, each calendar only knows its own meetings, so a
colleague booking your work calendar cannot see your personal appointments.
The sync copies each busy event's time slot, and nothing else, into every
other account's calendar as a private "Busy" block. Each account takes part
with one calendar, its primary unless ``calendars`` names another; that
calendar is both the source of its busy time and the target of the blocks.

How it stays safe:

- A block carries ``extendedProperties.private.gwmcp_block = "1"``. The sync
  only ever patches or deletes events with that marker, so it never touches a
  real event, and it never treats a block as a source (no echo loops).
- A block holds no title, description, location or attendees from its source:
  only start, end and a hashed key. Its visibility is private.
- Each block is keyed by its source's iCalUID plus original start, so a meeting
  that is already on several of your calendars gets one block per calendar
  that lacks it, and none on a calendar that has the real event.
- When any account fails to list, the run creates and updates blocks but
  deletes none, so a temporary error never wipes another calendar's blocks.

``plan`` is a pure function over listed events; ``apply`` performs the plan.
"""

from __future__ import annotations

import datetime
import functools
import hashlib
from typing import Any, Callable, Optional

MARKER = "gwmcp_block"
KEY = "gwmcp_key"
_SKIP_EVENT_TYPES = {"workingLocation", "birthday"}


def _utc(raw: str) -> str:
    when = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when.astimezone(datetime.timezone.utc).isoformat()


def _point(value: Optional[dict]) -> Optional[tuple[str, str]]:
    """("dateTime", UTC instant) or ("date", day) for a start/end field."""
    value = value or {}
    if value.get("dateTime"):
        try:
            return ("dateTime", _utc(value["dateTime"]))
        except ValueError:
            return None
    if value.get("date"):
        return ("date", value["date"])
    return None


def is_block(event: dict) -> bool:
    private = (event.get("extendedProperties") or {}).get("private") or {}
    return private.get(MARKER) == "1"


def is_busy(event: dict, *, include_all_day: bool = False) -> bool:
    """True when ``event`` makes its owner unavailable."""
    if is_block(event):
        return False
    if event.get("status") == "cancelled" or event.get("transparency") == "transparent":
        return False
    if event.get("eventType") in _SKIP_EVENT_TYPES:
        return False
    for attendee in event.get("attendees") or []:
        if attendee.get("self") and attendee.get("responseStatus") == "declined":
            return False
    start, end = _point(event.get("start")), _point(event.get("end"))
    if start is None or end is None:
        return False
    if start[0] == "date" and not include_all_day:
        return False
    return True


def block_key(event: dict) -> str:
    """Same key for the same meeting instance on any calendar."""
    uid = event.get("iCalUID") or event.get("id") or ""
    original = _point(event.get("originalStartTime"))
    tail = original[1] if original else ""
    return hashlib.sha256(f"{uid}|{tail}".encode()).hexdigest()[:32]


def _slot(event: dict) -> tuple:
    return (_point(event.get("start")), _point(event.get("end")))


def _field(point: tuple[str, str]) -> dict:
    kind, value = point
    return {kind: value}


def block_body(source: dict, key: str, summary: str) -> dict:
    start, end = _slot(source)
    return {
        "summary": summary,
        "start": _field(start),
        "end": _field(end),
        "transparency": "opaque",
        "visibility": "private",
        "reminders": {"useDefault": False, "overrides": []},
        "extendedProperties": {"private": {MARKER: "1", KEY: key}},
    }


def plan(
    events: dict[str, list[dict]],
    *,
    include_all_day: bool = False,
    summary: str = "Busy",
    allow_deletes: bool = True,
) -> dict[str, dict]:
    """Work out the blocks each account's calendar needs.

    ``events`` maps each account to the events listed from its calendar for
    the sync window (blocks included). Returns, per account,
    ``{"create": [body], "update": [{"event_id", "body"}], "delete": [event_id]}``.
    """
    busy: dict[str, dict[str, dict]] = {}
    blocks: dict[str, dict[str, list[dict]]] = {}
    for account, items in events.items():
        busy[account] = {}
        blocks[account] = {}
        for ev in items:
            if is_block(ev):
                key = ((ev.get("extendedProperties") or {}).get("private") or {}).get(KEY, "")
                blocks[account].setdefault(key, []).append(ev)
            elif is_busy(ev, include_all_day=include_all_day):
                busy[account].setdefault(block_key(ev), ev)

    out: dict[str, dict] = {}
    for target in events:
        wanted: dict[str, dict] = {}
        for source, items in busy.items():
            if source == target:
                continue
            for key, ev in items.items():
                if key not in busy[target]:
                    wanted.setdefault(key, ev)
        changes: dict[str, list] = {"create": [], "update": [], "delete": []}
        for key, ev in sorted(wanted.items()):
            body = block_body(ev, key, summary)
            existing = blocks[target].get(key, [])
            if not existing:
                changes["create"].append(body)
                continue
            keep = existing[0]
            if _slot(keep) != _slot(ev) or keep.get("summary") != summary:
                changes["update"].append({"event_id": keep["id"], "body": body})
            if allow_deletes:
                changes["delete"].extend(e["id"] for e in existing[1:])
        if allow_deletes:
            for key, existing in blocks[target].items():
                if key not in wanted:
                    changes["delete"].extend(e["id"] for e in existing)
        out[target] = changes
    return out


def apply(
    changes: dict[str, dict],
    api_for: Callable[[str], Any],
    run: Callable[[Callable[[], Any]], Any],
    calendar_for: Callable[[str], str] = lambda account: "primary",
) -> tuple[dict, dict]:
    """Perform ``changes`` on each account's calendar (``calendar_for(account)``).

    Returns (counts per account, errors per account).
    """
    counts: dict[str, dict] = {}
    errors: dict[str, list] = {}
    for account, todo in changes.items():
        done = {"created": 0, "updated": 0, "deleted": 0}
        counts[account] = done
        if not any(todo.values()):
            continue
        calendar_id = calendar_for(account)
        try:
            api = api_for(account)
        except Exception as e:  # noqa: BLE001 - report per account
            errors.setdefault(account, []).append(str(e))
            continue
        steps: list[tuple[str, Callable[[], Any]]] = []
        for body in todo["create"]:
            steps.append(("created", functools.partial(api.insert_busy_block, calendar_id, body)))
        for upd in todo["update"]:
            steps.append((
                "updated",
                functools.partial(api.patch_busy_block, calendar_id, upd["event_id"], upd["body"]),
            ))
        for event_id in todo["delete"]:
            steps.append(("deleted", functools.partial(api.delete_busy_block, calendar_id, event_id)))
        for label, step in steps:
            try:
                run(step)
                done[label] += 1
            except Exception as e:  # noqa: BLE001 - one bad event must not stop the rest
                errors.setdefault(account, []).append(str(e))
    return counts, errors


def _calendar_map(calendars: Optional[dict[str, str]], names: list[str]) -> dict[str, str]:
    """Resolve ``calendars`` keys to accounts and check each is in the sync."""
    import google_auth_core as core

    out: dict[str, str] = {}
    for key, calendar_id in (calendars or {}).items():
        if not isinstance(calendar_id, str) or not calendar_id.strip():
            raise core.InvalidArgumentError(f"calendar ID for {key} is empty")
        try:
            account = core.resolve_account(key)
        except Exception:  # noqa: BLE001 - reported below as not in the sync
            account = key
        if account not in names:
            raise core.InvalidArgumentError(
                f"calendars names {key}, which is not one of the synced accounts: "
                + ", ".join(names)
            )
        if account in out and out[account] != calendar_id.strip():
            raise core.InvalidArgumentError(f"two calendars given for {account}; use one")
        out[account] = calendar_id.strip()
    return out


def sync(
    accounts: Optional[list[str]] = None,
    *,
    calendars: Optional[dict[str, str]] = None,
    days_ahead: int = 14,
    include_all_day: bool = False,
    summary: str = "Busy",
    dry_run: bool = False,
    now: Optional[datetime.datetime] = None,
) -> dict:
    """List each account's calendar, then create, update and delete blocks.

    ``calendars`` maps an account (email or alias) to the one calendar ID it
    syncs; accounts it leaves out use their primary calendar.
    """
    import google_auth_core as core

    from ..core.fanout import target_accounts
    from ..core.runtime import get_api, run_tool
    from .changes_api import CalendarChangesAPI

    if days_ahead < 1 or days_ahead > 90:
        raise core.InvalidArgumentError("days_ahead must be between 1 and 90")
    names = target_accounts(accounts)
    if len(names) < 2:
        raise core.InvalidArgumentError("busy-block sync needs at least two accounts")
    chosen = _calendar_map(calendars, names)
    start = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(
        datetime.timezone.utc
    )
    time_min = start.isoformat()
    time_max = (start + datetime.timedelta(days=days_ahead)).isoformat()

    apis: dict[str, Any] = {}
    events: dict[str, list] = {}
    list_errors: dict[str, str] = {}
    for name in names:
        try:
            api, resolved = get_api("calendar", CalendarChangesAPI, name)
            events[resolved] = run_tool(
                lambda: api.list_window_events(
                    chosen.get(resolved, "primary"), time_min, time_max
                )
            )
            apis[resolved] = api
        except core.GoogleCoreError as e:
            list_errors[name] = str(e)

    changes = plan(
        events,
        include_all_day=include_all_day,
        summary=summary,
        allow_deletes=not list_errors,
    )
    planned = {
        acct: {k: len(v) for k, v in todo.items()} for acct, todo in changes.items()
    }
    out: dict[str, Any] = {
        "ok": bool(events),
        "accounts": sorted([*events, *list_errors]),
        "calendars": {acct: chosen.get(acct, "primary") for acct in sorted(events)},
        "window": {"time_min": time_min, "time_max": time_max},
        "dry_run": dry_run,
        "planned": planned,
    }
    if list_errors:
        out["errors"] = {k: [v] for k, v in list_errors.items()}
        out["note"] = "Some accounts could not be listed, so no blocks were deleted this run."
    if dry_run or not events:
        return out
    counts, apply_errors = apply(
        changes, apis.__getitem__, run_tool, lambda acct: chosen.get(acct, "primary")
    )
    out["applied"] = counts
    for acct, errs in apply_errors.items():
        out.setdefault("errors", {}).setdefault(acct, []).extend(errs)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    """``gcal-busy-sync``: run the sync once, or every ``--interval`` minutes."""
    import argparse
    import json
    import time

    parser = argparse.ArgumentParser(
        prog="gcal-busy-sync",
        description="Mirror busy time across your Google accounts' calendars.",
    )
    parser.add_argument("--accounts", nargs="+", help="emails or aliases (default: all)")
    parser.add_argument(
        "--calendar",
        action="append",
        default=[],
        metavar="ACCOUNT=CALENDAR_ID",
        help="sync this calendar instead of ACCOUNT's primary (repeat per account)",
    )
    parser.add_argument("--days", type=int, default=14, help="days ahead to sync (1-90)")
    parser.add_argument("--include-all-day", action="store_true", help="also block all-day events")
    parser.add_argument("--summary", default="Busy", help="title of the blocks")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, change nothing")
    parser.add_argument("--interval", type=int, default=0, help="repeat every N minutes")
    args = parser.parse_args(argv)
    calendars = {}
    for item in args.calendar:
        account, sep, calendar_id = item.partition("=")
        if not sep or not account or not calendar_id:
            parser.error(f"--calendar expects ACCOUNT=CALENDAR_ID, got {item!r}")
        calendars[account] = calendar_id

    while True:
        try:
            result = sync(
                args.accounts,
                calendars=calendars or None,
                days_ahead=args.days,
                include_all_day=args.include_all_day,
                summary=args.summary,
                dry_run=args.dry_run,
            )
        except Exception as e:  # noqa: BLE001 - CLI: print and keep looping
            result = {"ok": False, "error": str(e)}
        print(json.dumps(result, indent=2), flush=True)
        if args.interval <= 0:
            return 0 if result.get("ok") else 1
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    raise SystemExit(main())
