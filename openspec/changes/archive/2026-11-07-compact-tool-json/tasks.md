# Tasks

## 1. Code

- [x] 1.1 In `core/runtime.py`, wrap the tool `register` hands to `mcp.tool` so a dict or list result becomes `CallToolResult(content=[TextContent(type="text", text=pydantic_core.to_json(result, fallback=str).decode())])`; other results pass through; `functools.wraps` keeps the schema. Verify: `test_dict_result_is_compact_json`, `test_non_ascii_unescaped`, `test_string_result_unchanged`, `test_direct_call_returns_dict`, `test_schema_unchanged_by_wrapper`.
- [x] 1.2 Check every server. Verify: a test in `tests/test_integration.py` that a read tool of each of the six servers and of `gworkspace-mcp` returns text with no newline outside strings, and `test_recorded_day_compact` (the calendar fixture at most 4,700 characters).

## 2. Docs and gates

- [x] 2.1 README "Architecture": results reach the client as compact JSON. CLAUDE.md: the contract section names the wrapper.
- [x] 2.2 Run `ruff check .` and `pytest` with `GOOGLE_MCP_LIVE` unset.
  - Done 2026-11-07: ruff clean; 710 passed, 11 skipped. With the wrapper removed, the four new output tests and the nine policy tests fail. The fixture day is 4,635 characters. `tests/test_policy.py`'s helper now parses the wrapper's text, as a client does.
- [x] 2.3 Release v0.10.1: bump `pyproject.toml`, `google_workspace_mcp/__version__`, the tag in `.mcp.json` and every `@vX.Y.Z` in the README in one commit; after the PR merges, tag `main`; reinstall with `uv tool`.
