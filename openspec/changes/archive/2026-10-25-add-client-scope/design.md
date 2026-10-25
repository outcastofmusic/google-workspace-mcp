# Design

## Context

See proposal.md - Why. Facts of the current code and SDK:

- mcp 2.3.0 keeps unknown keys of a request's `_meta`: `CallToolRequestParams.model_validate`
  with `{"_meta": {"io.github.outcastofmusic.workspace/scope": {...}}}` returns them in `.meta`
  (checked 2026-10-25). `MCPServer` builds a `Context` per call whose `request_context.meta`
  holds them, and `WorkspaceServer.call_tool` already wraps every call.
- The go-sdk that mini-web-ui uses (v1.8.0) sends `_meta` through `CallToolParams.Meta`.
- `register` wraps each tool with `_retry_scope` and `_policy_scope`; `_mutating` is a context
  variable that `get_api` reads. Tools run in a worker thread through `anyio.to_thread`, which
  copies context variables into the thread.
- Drive, Docs and Sheets tools name files only through the seven parameter names listed in the
  spec; Docs and Sheets ids are Drive file ids.
- `fan_out` resolves `accounts` (or every configured account) itself; `busy_sync.sync` uses
  `target_accounts` too.

## Goals / Non-Goals

**Goals:**
- One server process serves every project of a client, each call limited to its own scope.
- No argument a model writes can reach outside the scope.
- No cost for clients that send no scope.

**Non-Goals:**
- Authenticating the client. Over stdio the server belongs to the process that started it; over
  HTTP the scope is only as trustworthy as whoever can connect.
- Scoping Gmail labels or calendars. Accounts are the boundary for mail and calendar.
- Scoping local file paths; `GOOGLE_MCP_FILE_ROOT` already does that per process.

## Decisions

| Date | Decision | Rejected alternative |
|---|---|---|
| 2026-10-25 | The scope travels in `_meta` under `io.github.outcastofmusic.workspace/scope` (reverse-DNS prefix, as MCP recommends for `_meta` keys) | A tool argument, which the model writes. An environment variable, which needs one process per project (~50 MB each) |
| 2026-10-25 | `WorkspaceServer.call_tool` parses the scope from `context.request_context.meta` and sets a `ContextVar` for the call; `anyio.to_thread` carries it into the tool's thread (checked 2026-10-25 with anyio 4) | Passing the scope through every tool's signature, which changes ~190 tools and their schemas |
| 2026-10-25 | Account check in `get_api`, after alias resolution, for every call (reads and writes). Fan-out helpers intersect their account list with the scope | Checking only mutating calls, which would still let a work project read personal mail |
| 2026-10-25 | Folder check in the `register` wrapper, before the tool runs, over the seven parameter names, for tools registered from the drive, docs and sheets servers (a `drive_scoped=True` flag on `register` that their modules set by default) | Per-tool checks, which a new tool can forget. Matching parameter names on every server, which would misread a Gmail `message_id` |
| 2026-10-25 | Ancestry: walk `files.get(fields="id,parents", supportsAllDrives=True)` up from the file until a scope folder or a root, at most 25 levels. Cache `file -> parents` per account for 10 minutes, at most 5,000 entries (LRU), so a folder of 10 documents costs about one request per new ancestor | Listing the scope folders' trees once, which goes stale as files are added. Drive's `'id' in parents` query, which only checks direct children |
| 2026-10-25 | Listing and search filter results after the Google call, using the same ancestry check; a page may hold fewer files, and `next_page_token` is passed through | Rewriting the query to `'<folder>' in parents`, which misses files in subfolders |
| 2026-10-25 | Creation without a parent is refused under a folder scope, and `create_document` / `create_spreadsheet` gain `folder_id`. The Docs and Sheets create calls cannot set a parent, so the new file is moved into the folder with a Drive `files.update(addParents, removeParents)` right after creation; if the move fails the file is trashed and the call fails, so nothing is left outside the scope. (`create_document_from_markdown` already sets `parents` at creation, since it creates through Drive) | Silently placing new files in the first scope folder, which hides where a file went |
| 2026-10-25 | `GOOGLE_MCP_REQUIRE_SCOPE` is off by default | On by default, which breaks every client that does not know about scopes, such as Claude Desktop |
| 2026-10-25 | The audit entry gains a `scope` field | Logging the scope only when a call is refused, which loses the context of allowed writes |

### Untrusted content

A document can tell the model to read or move files elsewhere; the scope makes those calls fail
before Google is asked. A file id inside a document's text is checked like any other argument.
A shortcut file inside the scope that points outside it is checked by its own parents, so the
target is refused when read through `read_file_text` on the target's id.

## Risks / Trade-offs

- [The parent cache is stale for up to 10 minutes after a file moves out of the scope] → the
  cache is cleared for a file whenever this server moves or trashes it; moves made elsewhere
  are covered after the TTL.
- [A scoped `search_files` over a large Drive returns sparse pages] → the client should search
  with `parent_id` of a scope folder; `list_folder_tree` is the better tool for a project.
- [A client that forgets the scope gets full access] → `GOOGLE_MCP_REQUIRE_SCOPE=1` for
  deployments where every client sends one.

## Migration Plan

Additive. Without `_meta` scope or the new env var, behaviour is unchanged.
