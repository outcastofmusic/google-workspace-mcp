"""Build an MCPServer preconfigured with the shared common tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

import google_auth_core as core
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import scope
from .common_tools import register_common_tools


class WorkspaceServer(MCPServer):
    """MCPServer that keeps our anticipated errors readable by the model.

    Since mcp 2.x, an exception that is not a ``ToolError`` reaches the client
    only as "Error executing tool <name>", its text logged server-side. Our
    ``GoogleCoreError`` messages (e.g. "Run: google-auth login <account>") and
    argument ``ValueError``s are meant for the model, so re-raise them as
    ``ToolError`` with their text.
    """

    # Service whose default account a tool without ``account`` uses; the
    # folder check of a client scope resolves the same account (see register).
    workspace_service: Optional[str] = None

    async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
        """Run a tool under the client scope its request's ``_meta`` carries.

        The scope is set as a context variable for this call only; the worker
        thread the tool runs in inherits it. A call that came without a request
        context (a direct, in-process call) has no scope.
        """
        request_context = getattr(context, "request_context", None) if context is not None else None
        meta = getattr(request_context, "meta", None) if request_context is not None else None
        try:
            sc = scope.from_meta(meta if isinstance(meta, Mapping) else None)
            if sc is None and scope.required():
                raise core.InvalidArgumentError(
                    f"a client scope is required (GOOGLE_MCP_REQUIRE_SCOPE); send it in "
                    f"_meta[{scope.SCOPE_KEY!r}]"
                )
        except core.GoogleCoreError as e:
            raise ToolError(f"Error executing tool {name}: {e}") from e
        token = scope.current.set(sc)
        try:
            return await self._call_tool_readable(name, arguments, context)
        finally:
            scope.current.reset(token)

    async def _call_tool_readable(self, name: str, arguments: dict[str, Any], context: Any) -> Any:
        try:
            return await super().call_tool(name, arguments, context)
        except ToolError as e:
            cause = e.__cause__
            if isinstance(cause, (core.GoogleCoreError, ValueError)):
                raise ToolError(f"Error executing tool {name}: {cause}") from cause
            raise


def build_server(
    name: str,
    instructions: Optional[str] = None,
    service_name: Optional[str] = None,
) -> MCPServer:
    """Create an MCPServer with the common account/auth tools registered.

    Service-specific tools are added by each server module after calling this.
    """
    mcp = WorkspaceServer(name, instructions=instructions)
    mcp.workspace_service = service_name
    register_common_tools(mcp, service_name=service_name)
    return mcp
