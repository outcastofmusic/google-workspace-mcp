# Design

## Context

Every tool registers through `register()` in `core/runtime.py`. `READONLY` and `TOOLSET` are read once at import, and `register()` returns the plain function without calling `mcp.tool()` when a gate excludes the tool.

## Decisions

### D1. A new tag (2026-11-08)

`sends_or_deletes=True` marks the 29 tools. Alternative rejected: reuse `destructive`. It misses `trash_file`, `untrash_file`, `batch_trash_files`, `mark_as_spam`, `unmark_spam`, `create_draft`, `draft_reply` and `update_draft`, and it also marks writes the agents keep.

### D2. Skip registration, read at import (2026-11-08)

The flag follows `GOOGLE_MCP_READONLY`: read once at import, and a tagged tool never reaches `tools/list`. Alternative rejected: refuse at call time in `core/policy.py`. A listed tool costs schema context, and the model would keep trying it.

## Untrusted content

Mail and file text may tell the model to send, forward, delete or share. With the flag set, the server lists no such tool, and a `tools/call` naming one fails as an unknown tool.

## Risks

- A new send or delete tool added later without the tag leaks through the gate. CLAUDE.md tells the author to set the tag.
