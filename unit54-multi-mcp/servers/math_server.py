"""Unit 54 · math server。"""

from mcp.server.mcpserver import MCPServer

server = MCPServer("math", version="0.1.0")


@server.tool(name="add", description="两数相加")
def add(a: float, b: float) -> float:
    return a + b


@server.tool(name="square", description="平方")
def square(x: float) -> float:
    return x * x


if __name__ == "__main__":
    server.run("stdio")