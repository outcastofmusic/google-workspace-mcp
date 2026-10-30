"""Google Workspace MCP servers (Gmail, Calendar, Sheets, Docs, Drive).

Five aligned MCP servers built on a shared core. Auth, account resolution,
the response envelope, error mapping, the read-only gate, and the common tools
(list_accounts / whoami / auth_status) are identical across all five.
"""

__version__ = "0.8.1"
