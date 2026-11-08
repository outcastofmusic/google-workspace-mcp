# Tasks

## 1. Code

- [x] 1.1 In `core/runtime.py`, read `GOOGLE_MCP_NO_SEND_DELETE` once at import into `NO_SEND_DELETE`, and give `register()` a `sends_or_deletes` tag that skips registration when both are set. Tag the 21 Gmail tools and the 8 Drive tools of the spec. Verify: `test_no_send_delete_hides_tagged_tools` in `tests/test_integration.py`, parametrized over Gmail and Drive: the tools listed without the flag minus those listed with it equal the tagged set, and the kept writes stay listed. Both fail with the gate removed.
- [x] 1.2 Fail closed: any value but empty, `0`, `false`, `no` or `off` turns the flag on. Verify: `test_no_send_delete_fails_closed_on_unknown_values` and `test_no_send_delete_off_values_list_tagged_tools`; the first failed before the change.
- [x] 1.3 Under the flag, `modify_labels` and `batch_modify_labels` refuse `TRASH` and `SPAM` before calling Gmail. Found by the security review on 2026-11-08. Verify: `test_no_send_delete_refuses_trash_and_spam_labels`, 3 cases, all failed before the change.

## 2. Docs and gates

- [x] 2.1 README "Read-only mode" and CLAUDE.md's gate section name the flag and the tag.
- [x] 2.2 Run `ruff check .` and `pytest` with `GOOGLE_MCP_LIVE` unset. Verify: both pass.
  - Done 2026-11-08: ruff clean; 732 passed, 11 skipped after 1.2 and 1.3.
- [ ] 2.3 Release: bump `pyproject.toml`, `google_workspace_mcp/__version__`, the tag in `.mcp.json` and every `@vX.Y.Z` in the README in one commit; after the PR merges, tag `main`; reinstall with `uv tool install`. Verify: `gmail-mcp` with the flag lists no `send_message`.
