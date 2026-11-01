"""Unit 54 · text server。"""

from mcp.server.mcpserver import MCPServer

server = MCPServer("text", version="0.1.0")


@server.tool(name="upper", description="转大写")
def upper(text: str) -> str:
    return text.upper()


@server.tool(name="reverse", description="反转字符串")
def reverse(text: str) -> str:
    return text[::-1]


if __name__ == "__main__":
    server.run("stdio")