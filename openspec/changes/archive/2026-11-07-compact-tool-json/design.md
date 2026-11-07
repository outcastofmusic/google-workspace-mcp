# Design

## Context

`register` (`core/runtime.py`) wraps each tool as `_off_loop(_policy_scope(_retry_scope(...)))`
and hands `mcp.tool` the wrapper, while returning the plain function to direct callers. The
library's `convert_result` passes a returned `CallToolResult` through unchanged, and for anything
else calls `_convert_to_content`, which applies `indent=2` to every non-string value. No tool has
an output schema, so no structured content is sent.

## Goals / Non-Goals

**Goals:** compact text for every dict or list result of every server; identical parsed content.

**Non-Goals:** changing what any tool returns; structured content; a switch to turn it off.

## Decisions

| Date | Decision | Alternative rejected |
|---|---|---|
| 2026-11-07 | The registered wrapper returns a `CallToolResult` with one compact `TextContent` | Returning a JSON string: the tools' `-> dict` annotations would then lie, and a future library version that validates or derives a schema from them would break. Also rejected: overriding `WorkspaceServer.call_tool` and calling the private `_tool_manager` with `convert_result=False`, which ties the package to a private API |
| 2026-11-07 | Serialise with `pydantic_core.to_json(result, fallback=str)`, the library's own call minus `indent` | `json.dumps(..., separators=(",", ":"))`: it turns a datetime or a set differently from pydantic, so a result could change content, not just layout |
| 2026-11-07 | Compact for every tool, no setting | An environment switch: no client wants the indentation, and each setting is one more thing to document and test |

## Untrusted content

Mail, file and event text inside a result is serialised exactly as before, minus layout
whitespace between JSON tokens; strings keep their own newlines as `\n` escapes, as they do now.
