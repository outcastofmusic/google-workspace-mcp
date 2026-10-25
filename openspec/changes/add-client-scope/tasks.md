# Tasks

## 1. Scope plumbing

- [ ] 1.1 Add `core/scope.py` with the scope type, parsing and validation of the `_meta` object, and a `ContextVar`. Verify: unit tests for a valid scope, a missing key, a wrong shape (error names the problem), and alias resolution of `accounts`.
- [ ] 1.2 In `WorkspaceServer.call_tool`, read the scope from `context.request_context.meta`, refuse a call without one when `GOOGLE_MCP_REQUIRE_SCOPE` is on, and set the context variable. Verify: protocol tests through an in-memory client that sends `_meta` show the scope inside a tool's worker thread, and that a call without scope is refused only with the env var set.

## 2. Accounts

- [ ] 2.1 Check the resolved account in `get_api` against the scope. Verify: a read and a write for an account outside the scope both fail with `Outside scope:` before the fake API is called.
- [ ] 2.2 Limit `fan_out`, `busy_sync.sync` and `list_accounts` to scoped accounts. Verify: tests for `search_messages_all_accounts`, `sync_busy_blocks` and `list_accounts` under a one-account scope.

## 3. Drive folders

- [ ] 3.1 Add the ancestry check with the per-account LRU cache (10 minutes, 5,000 entries), cleared for a file this server moves or trashes. Verify: tests with a mocked Drive service for a file in a scope folder, in a subfolder three levels down, outside, shared-only (no parents), a loop in parents, and a cache hit that makes no second request.
- [ ] 3.2 Add `drive_scoped` to `register`, set by the drive, docs and sheets servers, and check the seven parameter names before the tool runs; refuse `drive_id` and `list_drives`. Verify: protocol tests for `read_file_text`, `move_file` out of scope, `update_range` on a spreadsheet outside, and a Gmail tool whose `message_id` is not checked.
- [ ] 3.3 Filter the results of `search_files`, `list_files`, `search_files_all_accounts` and `list_folder_tree`. Verify: a search returning files in and out of scope yields only those in, with `next_page_token` passed through.
- [ ] 3.4 Refuse creation without a parent under a folder scope, and add `folder_id` to `create_document` and `create_spreadsheet`. Verify: tests for each create tool with and without a parent, and `test_required_ids_are_required_in_schema` still passes.

## 4. Audit, docs and checks

- [ ] 4.1 Add the scope to audit entries. Verify: the audit test shows `scope` on a scoped and `null` on an unscoped mutating call.
- [ ] 4.2 Document the scope (key, shape, what each part limits, `GOOGLE_MCP_REQUIRE_SCOPE`, the cost of ancestry checks) in the README's safety section and in CLAUDE.md. Verify: the README example `_meta` parses with the code's parser in a test.
- [ ] 4.3 Run `ruff check .` and `pytest`, then open the PR and verify CI passes on Python 3.10-3.13.
