"""三张牌：同一份 schema 的三种给法。

| 表示 | 长什么样 | 什么时候用 |
| --- | --- | --- |
| 全量 DDL | `CREATE TABLE orders (...)` 原样 | 表少、要模型写 DDL 时 |
| 精简 | `orders(id:int, customer_id:int, ...)` | 默认。信息够、字符少 |
| 按问题预筛 | 只给命中的表 + JOIN 所需的桥表 | 表多时唯一可行的做法 |

本模块只做「拼字符串」这件事，`main.py` 会实测三种的字符数。
字符数是硬指标：它直接换算成 token 与钱，也是上下文溢出的第一原因。
"""

from __future__ import annotations

import re

# 表/列的「人话注释」。真实项目里这来自数据字典或列注释，
# 精简表示把注释也带上，模型对各列语义的猜测会准很多。
TABLE_NOTES = {
    "customers": "客户档案",
    "products": "商品目录",
    "orders": "订单主表（一笔订单一行）",
    "order_items": "订单明细（一单多行，金额算 qty*unit_price）",
}

COLUMN_NOTES = {
    ("customers", "region"): "客户所在大区",
    ("orders", "region"): "下单大区（与客户一致）",
    ("orders", "status"): "已完成/已取消/已退款",
    ("orders", "order_date"): "下单日期 YYYY-MM-DD",
    ("order_items", "qty"): "数量",
    ("order_items", "unit_price"): "成交单价（分位已归整为元）",
    ("products", "price"): "目录价，不等于成交价",
}

# 问题里的词 → 需要的表。命中一个词就把这张表拉进来。
KEYWORD_TABLES = {
    "customers": ["客户", "用户", "买家", "人"],
    "products": ["产品", "商品", "SKU", "品类", "货"],
    "orders": ["订单", "单量", "下单", "成交"],
    "order_items": ["销售额", "营收", "金额", "明细", "数量", "件"],
}

# 表之间的桥：预筛时如果两张表要 JOIN，中间表必须一起给，否则模型写不出 ON。
JOIN_BRIDGE = {
    ("customers", "order_items"): ["orders"],
    ("products", "customers"): ["orders", "order_items"],
    ("order_items", "customers"): ["orders"],
    ("products", "orders"): ["order_items"],
    ("order_items", "products"): [],
    ("orders", "customers"): [],
}


def pick_tables(question: str, all_tables: list[str]) -> list[str]:
    """按关键词挑表，再把 JOIN 桥补上。结果永远包含能连起来的闭包。"""
    hits = set()
    for table, words in KEYWORD_TABLES.items():
        if table in all_tables and any(w in question for w in words):
            hits.add(table)
    if not hits:
        return list(all_tables)          # 一个都没命中就别猜，退回全量
    # 补桥：任意两张命中表若需要中间表，一起加
    for a in list(hits):
        for b in list(hits):
            if a >= b:
                continue
            for bridge in JOIN_BRIDGE.get((a, b), []):
                if bridge in all_tables:
                    hits.add(bridge)
    return [t for t in all_tables if t in hits]


def render_full(ddl: str) -> str:
    """① 全量 DDL：把建表语句整段塞进去。信息最全，也最贵。"""
    return ddl.strip()


def render_compact(schema: dict[str, list[dict]], tables: list[str] | None = None) -> str:
    """② 精简：表名(列名:类型)。可选带注释。"""
    out = []
    for table in tables or sorted(schema):
        cols = []
        for c in schema[table]:
            note = COLUMN_NOTES.get((table, c["name"]))
            cols.append(f"{c['name']}:{c['type'].lower()}" + (f"({note})" if note else ""))
        head = f"{table}  # {TABLE_NOTES[table]}" if table in TABLE_NOTES else table
        out.append(f"{head}\n  " + ", ".join(cols))
    return "\n".join(out)


def render_relevant(schema: dict[str, list[dict]], question: str) -> str:
    """③ 按问题预筛：只给命中的表 + 桥表，列也只给必要的。"""
    tables = pick_tables(question, sorted(schema))
    return render_compact(schema, tables)


def render_for_prompt(schema: dict[str, list[dict]], question: str, mode: str = "relevant", ddl: str = "") -> str:
    if mode == "full":
        return render_full(ddl)
    if mode == "compact":
        return render_compact(schema)
    return render_relevant(schema, question)


def synthetic_wide_schema(n_tables: int = 40, cols: int = 8) -> dict[str, list[dict]]:
    """合成一份「公司真实规模」的 schema，用来测规模效应。

    本项目自己的库只有 4 张表，三种表示的差距看着不吓人。
    换到 40 张表，差距就是「放得进上下文」和「放不进」的区别。
    """
    types = ["INTEGER", "TEXT", "TEXT", "INTEGER", "TEXT", "REAL", "TEXT", "INTEGER"]
    out = {}
    for i in range(n_tables):
        name = f"dim_{i:02d}_entity"
        out[name] = [
            {"name": f"col_{j}", "type": types[j % len(types)], "notnull": False, "pk": j == 0}
            for j in range(cols)
        ]
    out["fact_event_log"] = [
        {"name": "event_id", "type": "INTEGER", "notnull": False, "pk": True},
        {"name": "user_id", "type": "INTEGER", "notnull": True, "pk": False},
        {"name": "event", "type": "TEXT", "notnull": True, "pk": False},
        {"name": "ts", "type": "TEXT", "notnull": True, "pk": False},
    ]
    return out


def char_stats(text: str) -> dict:
    """字符数与粗估 token（中文 1 字≈1 token，英文 4 字符≈1 token，这里只做量级估算）。"""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(text) - cjk
    return {"chars": len(text), "lines": text.count("\n") + 1, "cjk": cjk, "est_tokens": cjk + other // 4}
