"""Unit 53 · Agent 当 MCP Client。

教学核心：**MCP 的 `input_schema` 可以直接当 OpenAI function-calling 的 `parameters` 用**。
两套协议的字段结构是一样的 —— 这就是为什么 Agent 代码可以「不动地」接 MCP server。

server 暴露两个工具：
  - list_inventory(sku)  返回某商品库存
  - search_orders(customer_id, status) 返回订单

agent.py 里的 OfflineModel 走规则匹配 → 决定调哪个工具 → 把 MCP 工具结果回填成
OpenAI message → 再问模型 → 给最终答案。

启动：
    /home/rik/.cache/aj/runner/bin/python server.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mcp.server.mcpserver import MCPServer

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "shop.db"

server = MCPServer(
    "unit53-agent-shop",
    version="0.1.0",
    instructions="商品库存与订单查询。两个工具，都是只读。",
)


INVENTORY = [
    {"sku": "A-100", "name": "机械键盘", "stock": 12, "price": 499},
    {"sku": "B-200", "name": "人体工学椅", "stock": 3, "price": 1899},
    {"sku": "C-300", "name": "显示器支架", "stock": 27, "price": 259},
]

ORDERS = [
    {"order_id": "o-001", "customer_id": "c-100", "status": "shipped", "total": 998},
    {"order_id": "o-002", "customer_id": "c-100", "status": "pending", "total": 1899},
    {"order_id": "o-003", "customer_id": "c-200", "status": "shipped", "total": 259},
    {"order_id": "o-004", "customer_id": "c-200", "status": "cancelled", "total": 0},
    {"order_id": "o-005", "customer_id": "c-300", "status": "shipped", "total": 519},
]


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(
            """
            CREATE TABLE inventory (
                sku        TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                stock      INTEGER NOT NULL,
                price      INTEGER NOT NULL
            );
            CREATE TABLE orders (
                order_id    TEXT PRIMARY KEY,
                customer_id TEXT NOT NULL,
                status      TEXT NOT NULL,
                total       INTEGER NOT NULL
            );
            """
        )
        conn.executemany(
            "INSERT INTO inventory VALUES (:sku,:name,:stock,:price)", INVENTORY
        )
        conn.executemany(
            "INSERT INTO orders VALUES (:order_id,:customer_id,:status,:total)", ORDERS
        )
        conn.commit()
    finally:
        conn.close()


@server.tool(
    name="list_inventory",
    description="按 SKU 查库存。参数 sku 是必填的商品编号，如 A-100。",
)
def list_inventory(sku: str) -> str:
    """SKU 不存在返回中文提示而不是抛异常 —— 让 Agent 能读懂原因。"""
    for item in INVENTORY:
        if item["sku"].lower() == sku.lower():
            return f"{item['sku']} {item['name']} 库存 {item['stock']} 件 单价 ¥{item['price']}"
    return f"没有这个 SKU：{sku}，已知：{[i['sku'] for i in INVENTORY]}"


@server.tool(
    name="search_orders",
    description="按客户编号查订单。customer_id 必填；status 可选（shipped/pending/cancelled）。",
)
def search_orders(customer_id: str, status: str = "") -> str:
    hits = [o for o in ORDERS if o["customer_id"] == customer_id]
    if status:
        status_lower = status.lower()
        hits = [o for o in hits if o["status"] == status_lower]
    if not hits:
        return f"客户 {customer_id} 没有匹配订单（status={status or '不限'}）"
    lines = [f"{o['order_id']} 状态 {o['status']} 总额 ¥{o['total']}" for o in hits]
    return f"客户 {customer_id} 共 {len(hits)} 笔订单：\n" + "\n".join(lines)


if __name__ == "__main__":
    init_db()
    server.run("stdio")