# Proposal

## Why

mini-web-ui will organise chats in projects, each allowed only some Google accounts and some
Drive folders (decided by the user on 2026-10-25: enforcement in this server, option A). A
project is a client-side idea; the model writes the tool arguments, so only this server, which
sees every call, can refuse a file outside the project's folders or an account outside its list.
Running one server process per project would cost about 50 MB each, against mini-web-ui's
memory goal.

## What Changes

- A client may attach a scope to any tool call in the request's `_meta`, under the key
  `io.github.outcastofmusic.workspace/scope`:
  `{"accounts": ["work"], "drive_folders": [{"account": "me@work.com", "id": "<folder id>"}]}`.
  The model cannot set `_meta`; the client builds it.
- With `accounts` set, every tool refuses an account outside the list (after alias resolution).
  Fan-out tools (`*_all_accounts`, `sync_busy_blocks`) use only the listed accounts.
  `list_accounts` lists only them.
- With `drive_folders` set, every Drive, Docs and Sheets tool refuses a file or folder that is
  not one of those folders or below them, checked through the file's parent chain. Search and
  listing tools return only files inside the scope. `list_drives` and a `drive_id` argument are
  refused. A tool that would create a file outside the scope (no parent given) is refused with a
  message naming the parameter to pass.
- `create_document` and `create_spreadsheet` gain an optional `folder_id`, so a scoped client can
  create a Doc or Sheet inside its folder. (mutating, existing tools; the new argument only.)
- A call with no scope behaves as today. `GOOGLE_MCP_REQUIRE_SCOPE=1` makes the server refuse
  any call without a scope, for a deployment where every client is known to send one.
- The audit log (`GOOGLE_MCP_AUDIT_LOG`) records the scope of each mutating call.
- A refused call returns an error starting `Outside scope:` that names the account or file.

No tool is added. No new dependency.

## Capabilities

### New Capabilities

- `client-scope`: the scope in `_meta`, which tools it limits and how, the parent-chain check,
  filtering of search results, creation inside the scope, the require-scope switch.

### Modified Capabilities

None: `drive-files` behaves the same when no scope is sent, and the scope's effect on its tools
is specified in `client-scope`.

## Impact

- New `core/scope.py`: parse the scope from the request context, check accounts, check file
  ancestry with a per-account parent cache, filter results.
- `core/server.py`: `WorkspaceServer.call_tool` reads `_meta` from the request context and sets
  the scope for the call (a context variable), as `_mutating` is set today.
- `core/runtime.py`: `get_api` checks the account; the `register` wrapper checks the file and
  folder arguments of Drive, Docs and Sheets tools before the tool runs and filters list results.
- `core/fanout.py`, `calendar/busy_sync.py`, `core/common_tools.py`: limit accounts.
- `docs/server.py`, `sheets/server.py`: `folder_id` on create.
- `core/policy.py`: the scope in audit entries.
- Cost: one Drive `files.get` per uncached ancestor (fields `id,parents`), cached for 10 minutes
  per account; a scoped search may return fewer than `page_size` results per page.
