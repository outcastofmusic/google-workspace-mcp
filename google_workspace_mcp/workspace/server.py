"""All six Workspace servers in one process -> gworkspace-mcp.

Each server is its own Python process, and almost all of a process's memory is
the shared stack (mcp SDK, googleapiclient, google-auth-core), not the tools:
the five servers before Slides cost ~450MB, one process with every tool ~130MB. Clients start a
full set per window, so this is the entry point to configure when you want
Gmail, Calendar, Sheets, Docs, Drive and Slides together.

Tools are taken from the six server modules as they are, so behavior, the
read-only gate and the toolset filter stay identical. Names that two servers
share (list_changes, add_comment, batch_update, ...) get the service as prefix, counted over
every tool a server declares, so a name does not change with the toolset,
e.g. ``drive_list_changes``; unique names are kept. The common account tools
are registered once.
"""
from __future__ import annotations

from collections import Counter

from ..calendar import server as calendar
from ..core import build_server
from ..core.runtime import declared_tools
from ..docs import server as docs
from ..drive import server as drive
from ..gmail import server as gmail
from ..sheets import server as sheets
from ..slides import server as slides

_SERVICES = {"gmail": gmail, "calendar": calendar, "sheets": sheets, "docs": docs, "drive": drive,
             "slides": slides}

mcp = build_server(
    "gworkspace-mcp",
    "Google Workspace (Gmail, Calendar, Sheets, Docs, Drive, Slides) for one or more Google accounts.\n\n"
    + "\n\n".join(m.mcp.instructions for m in _SERVICES.values() if m.mcp.instructions),
)

_common = {t.name for t in mcp._tool_manager.list_tools()}
# Counted over every declared tool, so a name keeps its prefix whatever the
# toolset or the read-only gate registered.
_counts = Counter(
    name for m in _SERVICES.values() for name in declared_tools.get(id(m.mcp), ()) if name not in _common
)

for _service, _module in _SERVICES.items():
    for _tool in _module.mcp._tool_manager.list_tools():
        if _tool.name in _common:
            continue
        mcp.add_tool(
            _tool.fn,
            name=f"{_service}_{_tool.name}" if _counts[_tool.name] > 1 else _tool.name,
            title=_tool.title,
            description=_tool.description,
            annotations=_tool.annotations,
        )


def main():
    mcp.run()
