# Proposal

## Why

The person decided on 2026-11-08 that Eurycleia's `mail` and `drive` agents may label, archive, move and create folders, and may never send, draft, trash, delete, mark spam, change filters, block senders, rename labels or share files. Eurycleia refuses those 29 tools in its host. A server that never lists them adds a second fence. `GOOGLE_MCP_READONLY` hides every write, so it cannot serve a server that must keep `modify_labels` and `move_file`.

## What Changes

- New env flag `GOOGLE_MCP_NO_SEND_DELETE`, on for any value other than empty, `0`, `false`, `no` or `off` (fails closed), read once at import like `GOOGLE_MCP_READONLY`.
- New `register()` tag `sends_or_deletes=True`. With the flag set, `register()` registers no tagged tool, as it registers no mutating tool under `GOOGLE_MCP_READONLY`. `declared_tools` still records the name.
- The tag goes on 21 Gmail tools: `send_message`, `reply_to_message`, `forward_message`, `draft_reply`, `create_draft`, `update_draft`, `send_draft`, `delete_draft`, `trash_message`, `untrash_message`, `delete_message`, `batch_trash_messages`, `batch_untrash_messages`, `batch_delete_messages`, `mark_as_spam`, `unmark_spam`, `update_label`, `delete_label`, `create_filter`, `delete_filter`, `block_sender`.
- The tag goes on 8 Drive tools: `share_file`, `unshare_file`, `trash_file`, `untrash_file`, `delete_file`, `batch_trash_files`, `batch_delete_files`, `delete_comment`.
- No tool is added, removed or renamed. All 29 tagged tools are mutating. Without the flag every server lists the same tools as before.

New dependencies: none.

## Capabilities

### New Capabilities

- `send-delete-gate`: a server started with the flag lists no tool that sends, drafts, trashes, deletes or shares.

### Modified Capabilities

None.

## Impact

- `google_workspace_mcp/core/runtime.py`: the flag and the tag in `register()`.
- `google_workspace_mcp/gmail/server.py` and `drive/server.py`: the tag on 29 decorators. `gworkspace-mcp` imports these servers, so it follows.
- Tests in `tests/test_integration.py`, beside the `GOOGLE_MCP_TOOLSET` gate tests, whose `_base_env` now drops the flag.
- README "Read-only mode" and CLAUDE.md's gate section.
- Eurycleia sets the flag on its `gmail-org` and `gdrive` instances after a reinstall.
