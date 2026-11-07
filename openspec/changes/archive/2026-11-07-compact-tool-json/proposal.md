# Proposal

## Why

The MCP library turns a tool's dict result into text with `pydantic_core.to_json(..., indent=2)`,
so every result of every server carries a newline and two spaces per nesting level. The text is
for a model, and indentation is pure context cost: on 2026-11-07, 15 short Calendar records took
5,505 characters indented and 4,632 compact, 16% less, and the same 15 raw Google events took
21,409 and 16,705, 22% less. The main clients run a local model with a 200k context and a
client-side result cap.

## What Changes

- Every tool of every server, and of `gworkspace-mcp`, returns its dict or list result as one
  compact JSON text block: the same `pydantic_core.to_json` serialisation, without the indent.
- Results that are not a dict or a list (strings, images, content blocks) pass through as before.
- No tool is added, removed or renamed; no schema changes. Read-only and mutating tools alike.
- Python callers of the tool functions still get the plain dict.

New dependencies: none (`pydantic_core` comes with `mcp`).

## Capabilities

### New Capabilities

- `tool-output`: how a tool's result reaches the client as text.

### Modified Capabilities

None.

## Impact

- `google_workspace_mcp/core/runtime.py`: one wrapper in `register`.
- `tests/test_runtime.py` and a cross-server test in `tests/test_integration.py`.
- README "Architecture" envelope line, CLAUDE.md contract section.
- A release, v0.10.1, and a reinstall on the user's machine.
