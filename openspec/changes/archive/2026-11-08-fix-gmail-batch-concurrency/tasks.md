# Tasks

- [x] 1.1 Fetch search results in batches of 20 and retry 429-refused messages once. Verify: tests that 50 ids go out as 20, 20 and 10, that two refused ids are retried in one batch after a sleep of at least 1 s, and that a 404 is not retried; they fail on the code before.
- [x] 1.2 Keep a fetch error in the search record. Verify: a test that a message with `error` projects to `id`, `error` and `account`; it fails on the code before.
- [x] 1.3 Release v0.12.1: bump `pyproject.toml`, `google_workspace_mcp/__version__`, the tag in `.mcp.json` and every `@vX.Y.Z` in the README in one commit; tag it. Verify: `ruff check .` and `pytest` pass; a read-only live search of 50 per account counts refused records before and after.
  - Done 2026-11-08: ruff clean; 736 passed, 11 skipped. Live, read-only, 50 per account over 2 accounts: v0.12.0 returned 16/100 and then 19/100 records holding only id and account; this branch returned 0/100 twice, with no `error` records.
