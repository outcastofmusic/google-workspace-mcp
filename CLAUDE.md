# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

One PyPI distribution (`google-workspace-suite-mcp`) that ships **five aligned MCP servers** for Google Workspace (Gmail, Calendar, Sheets, Docs, Drive) plus the `google-auth` login CLI. The five servers are separate console-script entry points but share one runtime `core/` and one on-disk credential store, which is what keeps them behaving identically.

## Commands

```bash
pip install -e .[dev]      # editable install with test deps
pytest                     # full suite: unit + protocol (in-memory) + isolation + persistence
ruff check .               # minimal lint (import order, syntax, pyflakes)
pytest tests/test_sheets_server.py            # one file
pytest tests/test_sheets_server.py -k read_range   # one test by name
GOOGLE_MCP_LIVE=1 pytest   # opt-in live smoke tests; hit real Google APIs, never run in CI
```

There is no separate typecheck step. CI runs `pytest` and `ruff check` on push/PR to `main` (Python 3.10–3.13); that is the gate.

Build/release (maintainer): tag `v*` matching `pyproject.toml` version triggers GitHub Actions Trusted Publishing to PyPI. Local `python -m build` plus Keychain token remains a fallback for emergencies. When tagging, also bump the `google-workspace-suite-mcp==X.Y.Z` pin in `.mcp.json` and in the README (`.mcp.json` example plus the `==X.Y.Z` mention below it).

## Architecture

```
google-auth-core (external dep)   shared ~/.google token store + authorized-service cache
                                  (also powers gmail-cli-oauth and google-calendar-cli)
        v
google_workspace_mcp/
  core/      build_server, get_api (account resolution + warm-client cache),
             run_tool (error mapping), register (read-only gate), ok (envelope),
             common_tools (list_accounts / whoami / auth_status),
             scope (client scope from _meta: accounts, Drive folders)
  gmail/     server.py + changes_api.GmailChangesAPI (subclass)  -> gmail-mcp
  calendar/  server.py + changes_api.CalendarChangesAPI        -> gcal-mcp
  sheets/    server.py + sheets_api.SheetsAPI (Sheets v4)      -> gsheets-mcp
  docs/      server.py + docs_api.DocsAPI (Docs v1)            -> gdocs-mcp
  drive/     server.py + drive_api.DriveAPI (Drive v3)         -> gdrive-mcp
             + extract.py (PDF via pypdf; docx/pptx/xlsx via stdlib zip+XML,
               DOCTYPE refused, 200 MiB expansion cap) used by read_file_text
```

Gmail and Calendar reuse the API wrappers from their sibling CLI packages (`gmail_cli`, `google_calendar_cli`); Sheets/Docs/Drive have their own thin wrappers in this repo built on `core.get_service(name, version, account=...)`.

## The core contract every tool follows

Adding or editing a tool means following the exact shape used everywhere (see `sheets/server.py`):

```python
@register(mcp, mutating=True, destructive=True)   # flags optional; see below
def some_tool(account: str | None = None, *, file_id: str, limit: int = 10) -> dict:
    """One-line description shown to the model. First line matters."""
    api, resolved = _api(account)                 # _api = get_api(service, Factory, account)
    data = run_tool(lambda: api.some_call(...))    # run_tool maps exceptions -> GoogleCoreError
    return ok(resolved, data)                       # -> {"ok": True, "account": resolved, "data": ...}
```

Key invariants, all enforced by `core/runtime.py` — do not bypass them:

- **`account` is always the first param**, `str | None`, defaults to the store's default account. It may be an email or an alias; `get_api` resolves it.
- **Required params are keyword-only with no default**, after a bare `*`. Never write `file_id: str = ""`: FastMCP would mark it optional in the schema, models would omit it, and the user gets a Google 404 instead of a validation error. Only give a default when the empty or default value is a real choice (e.g. `replacement: str = ""`). `test_required_ids_are_required_in_schema` guards this.
- **Never build a service client directly in a tool.** Always go through `_api(account)` → `get_api`, which caches one client per `(service, resolved_account)`. This is what guarantees the headline feature: parallel accounts never interfere. A new client type is constructed once and reused for the life of the server.
- **Wrap the actual Google call in `run_tool(lambda: ...)`** so failures become the shared `GoogleCoreError` taxonomy (missing/expired creds produce actionable `Run: google-auth login <account>` messages).
- **Return via `ok(resolved, data)`** — return the *resolved* account, not the raw `account` arg.

## The read-only gate and destructive markers

`@register` (in `core/runtime.py`) is the only way to register a tool, and its flags drive two behaviors:

- `mutating=True` — the tool is **not registered at all** when `GOOGLE_MCP_READONLY` env is set (`1/true/yes/on`). It never appears in `list_tools`. Read tools use bare `@register(mcp)`.
- `destructive=True` — appends a `[DESTRUCTIVE]` warning to the tool's docstring/description. Deletes, clears, and permanent changes use `mutating=True, destructive=True`.

Mutating calls also pass `core/policy.py` (recipient allowlist, required/read-only accounts, audit log): recipient checks run in `register`'s wrapper, account checks in `get_api`. Policy env vars are read at call time; see the README's "Safety policy" section.

`READONLY` is read once at import, so servers must be launched with the env already set. When adding a write/delete tool, set these flags or it will leak into read-only mode.

## Client scope

A client may limit one call to some accounts and Drive folders through `_meta["io.github.outcastofmusic.workspace/scope"]` (`core/scope.py`; README "Client scope"). `WorkspaceServer.call_tool` parses it from `context.request_context.meta` and sets the `scope.current` ContextVar, which the tool's worker thread inherits. `GOOGLE_MCP_REQUIRE_SCOPE=1` refuses calls without one.

- **Accounts:** `get_api` (through `_client`) refuses a resolved account outside `accounts` for every call. `fanout.target_accounts`, `list_accounts`, `auth_status` and `whoami` also honor it. Never resolve an account and call Google without `get_api`.
- **Drive folders:** the Drive, Docs and Sheets servers rebind `register = functools.partial(_register, drive_scoped=True)`. The `drive_scoped` wrapper checks the seven parameters in `runtime.SCOPED_FILE_PARAMS` (and the same keys inside `items`) through the parent chain before the tool runs, refuses `drive_id` and `list_drives`, requires the parent parameter of create tools (`_CREATE_PARENT`), and filters the file lists of `_FILTERED_TOOLS`. A new Drive-family tool must name files only through those seven parameters; a new create tool must be added to `_CREATE_PARENT`, and a new listing tool to `_FILTERED_TOOLS`. Gmail and Calendar tools are never drive-scoped, so their `message_id`/`event_id` are not read as files.
- The folder check uses `runtime.borrow_api`, which holds the Drive client lock only for the check (or reuses it when the tool already holds it), so it cannot deadlock with the tool's own clients.
- Refusals raise `core.InvalidArgumentError("Outside scope: ...")`. Audit entries carry `scope`.

## Tests

- `tests/conftest.py` provides `store_env` (points the token store at a temp `~/.google` and clears the api cache) and `write_token` (writes a fake unified token + registers the account). Use these for anything touching auth.
- Server tests inject a **FakeAPI** (see `test_gmail_server.py`) or `patch("google_auth_core.get_service", ...)` (see `test_sheets_server.py`) — they never hit the network. The FakeAPI's method signatures must stay in sync with the real wrapper; `tests/test_fake_drift.py` enforces parity for hand-written fakes, and calendar/sheets/docs/drive server mocks use `MagicMock(spec=...)`.
- `test_integration.py` holds the cross-cutting guarantees: multi-account isolation under concurrency, credential persistence across a simulated restart (interactive auth must never run in a server), the read-only gate, and that all five servers expose the common tools. Treat these as the spec.

## Auth model (important context)

Auth is **out-of-band**. The servers are non-interactive token *consumers*: they read and silently refresh tokens from `~/.google/` but never run an OAuth flow. Login happens separately via the `google-auth` CLI (from `google-auth-core`). Users bring their own Google OAuth desktop client at `~/.google/credentials.json`. If a tool ever tries to trigger interactive auth inside a server, that's a bug (`test_integration.py` asserts against it).

## Design docs

`docs/superpowers/specs/` and `docs/superpowers/plans/` hold the design specs and implementation plans (e.g. the Drive server, Sheets content/text editing). Read the relevant spec before extending a subsystem it covers.
