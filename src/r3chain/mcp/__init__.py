"""R3CHAIN MCP server and interim tool compatibility layer."""

from r3chain.mcp.compat import DEFAULT_INTERIM_TOOL_NAMES, register_interim_geo_tools
from r3chain.mcp.server import (
    CORE_MCP_TOOLS,
    MCP_PROTOCOL_VERSION,
    MCP_SDK_VERSION,
    create_mcp_server,
)

__all__ = [
    "CORE_MCP_TOOLS",
    "DEFAULT_INTERIM_TOOL_NAMES",
    "MCP_PROTOCOL_VERSION",
    "MCP_SDK_VERSION",
    "create_mcp_server",
    "register_interim_geo_tools",
]
