"""Unit 54 · db server（最小库存）。"""

from mcp.server.mcpserver import MCPServer

server = MCPServer("db", version="0.1.0")

# 故意和 math 一样有 add，验证「名字冲突要加前缀」
INVENTORY = {"A-100": 12, "B-200": 3, "C-300": 27}


@server.tool(name="add", description="增加库存（演示名字冲突，会被前缀 db.add 隔离）")
def add(sku: str, qty: int) -> str:
    if sku not in INVENTORY:
        return f"未知 SKU：{sku}"
    INVENTORY[sku] += qty
    return f"{sku} 现库存 {INVENTORY[sku]}"


@server.tool(name="query", description="查库存")
def query(sku: str) -> str:
    return f"{sku} 库存 {INVENTORY.get(sku, 0)}"


if __name__ == "__main__":
    server.run("stdio")