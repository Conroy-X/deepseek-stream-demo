"""离线 Agent：规则模型 + 工具循环。

三段结构：
1. `SchemaMap` —— 从 MCP 的 list_tables / describe_table 拿到的 schema，注入系统提示词；
2. `OfflineModel` —— 纯正则/规则，产出**标准 OpenAI assistant message**：
   `{"role":"assistant","content":...,"tool_calls":[{"id":..,"type":"function","function":{"name":..,"arguments":"<JSON 字符串>"}}]}`
   注意 `arguments` 是**字符串**不是 dict，这是真实 API 的行为；
3. `Agent.ask()` —— 模型 → 工具 → 模型，直到拿到最终回答。

`chat_api()` 是真的（httpx 调 DeepSeek），但被环境变量挡在后面。
自检永远走 `OfflineModel`，保证 CI 里 0 网络、0 key、结果确定。
"""

from __future__ import annotations

import json
import os
import re

import client

TOOL_NAME = "run_readonly_sql"


def _prev_month(today: str) -> str:
    """'2026-11-01' → '2026-10'。日期从系统提示词来，不由模型瞎猜。"""
    y, m, _ = (int(x) for x in today.split("-"))
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y:04d}-{m:02d}"


def _next_month(ym: str) -> str:
    y, m = (int(x) for x in ym.split("-"))
    m += 1
    if m == 13:
        y, m = y + 1, 1
    return f"{y:04d}-{m:02d}-01"


class SchemaMap:
    """「表名 → 列清单」。它的唯一职责是：让模型看到**真实存在**的列名。"""

    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables

    def has_table(self, table: str) -> bool:
        return table in self.tables

    def has(self, table: str, column: str) -> bool:
        return any(c["name"] == column for c in self.tables.get(table, []))

    def columns(self, table: str) -> list[str]:
        return [c["name"] for c in self.tables.get(table, [])]

    def find(self, table: str, *candidates: str) -> str | None:
        """按优先级找一个存在的列，找不到返回 None（而不是编一个）。"""
        cols = self.columns(table)
        for name in candidates:
            if name in cols:
                return name
        return None

    def render(self) -> str:
        """精简表示：表名 + 列名:类型。字符数远小于全量 DDL（第 56 章会量化）。"""
        lines = []
        for table in sorted(self.tables):
            cols = ", ".join(f"{c['name']}:{c['type'].lower()}" for c in self.tables[table])
            lines.append(f"{table}({cols})")
        return "\n".join(lines)


def load_schema() -> tuple[SchemaMap, int]:
    """真的通过 MCP 拉 schema。返回值第二个是工具调用次数（用来算往返开销）。"""
    calls = [("list_tables", {})]
    first = client.call_many(calls)
    tables = json.loads(first["results"][0]["text"])["tables"]
    more = [(f, {"table": t}) for t in tables for f in ["describe_table"]]
    second = client.call_many(more)
    mapping = {}
    for item in second["results"]:
        payload = json.loads(item["text"])
        mapping[payload["table"]] = payload["columns"]
    return SchemaMap(mapping), 1 + len(more)


SYSTEM_TEMPLATE = """你是电商数据分析助手，只能通过 run_readonly_sql 这个工具查数据。
今天是 {today}。「上月」指 {last_month}。
数据库 schema（表名(列名:类型)）：
{schema}

规则：不确定列名就不要生成 SQL，直接说不知道该查哪个字段。"""


class OfflineModel:
    """规则模型。接口长得和 OpenAI Chat Completions 一样，方便换成真模型。"""

    name = "offline-rules"

    def __init__(self, schema: SchemaMap, today: str = "2026-11-01"):
        self.schema = schema
        self.today = today

    # -- 对外唯一入口 ------------------------------------------------------
    def complete(self, messages: list[dict]) -> dict:
        if messages and messages[-1]["role"] == "tool":
            return self._summarize(messages)
        return self._plan(messages[-1]["content"])

    # -- 第一轮：问题 → tool_calls ----------------------------------------
    def _plan(self, question: str) -> dict:
        sql = self.build_sql(question)
        if sql is None:
            return {"role": "assistant", "content": self._refuse(question), "tool_calls": []}
        return {
            "role": "assistant",
            "content": f"我来查一下：{sql}",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": TOOL_NAME, "arguments": json.dumps({"sql": sql}, ensure_ascii=False)},
            }],
        }

    def _refuse(self, question: str) -> str:
        return (
            f"无法回答：问题里没有能对应到已知字段的指标或维度（问的是「{question}」）。"
            f"当前可查的表是 {', '.join(sorted(self.schema.tables))}，请换一个说法。"
        )

    # -- 规则：自然语言 → SQL ---------------------------------------------
    def build_sql(self, question: str) -> str | None:
        s = self.schema
        region = next((r for r in ("华东", "华北", "华南", "西部") if r in question), None)
        last = _prev_month(self.today)

        # ① 某区域某月销售额前 N 的产品
        if region and re.search(r"前\s*(\d+)", question) and re.search(r"产品|商品|SKU", question):
            n = int(re.search(r"前\s*(\d+)", question).group(1))
            if not (s.has("order_items", "unit_price") and s.has("products", "name")):
                return None
            return (
                "SELECT p.name AS product, SUM(oi.qty * oi.unit_price) AS revenue\n"
                "FROM order_items oi\n"
                "JOIN orders o ON o.id = oi.order_id\n"
                "JOIN products p ON p.id = oi.product_id\n"
                f"WHERE o.region = '{region}' AND o.status = '已完成'\n"
                f"  AND o.order_date >= '{last}-01' AND o.order_date < '{_next_month(last)}'\n"
                "GROUP BY p.id, p.name\n"
                "ORDER BY revenue DESC\n"
                f"LIMIT {n}"
            )

        # ② 各区域销售额（不加月份条件时默认上月）
        if re.search(r"各区域|按区域|分区", question) and re.search(r"销售额|营收|收入", question):
            return (
                "SELECT o.region AS region, SUM(oi.qty * oi.unit_price) AS revenue\n"
                "FROM order_items oi\n"
                "JOIN orders o ON o.id = oi.order_id\n"
                "WHERE o.status = '已完成'\n"
                f"  AND o.order_date >= '{last}-01' AND o.order_date < '{_next_month(last)}'\n"
                "GROUP BY o.region\n"
                "ORDER BY revenue DESC"
            )

        # ③ 客户数
        if re.search(r"多少(个|位)?客户|客户(数|数量)", question):
            if not s.has("customers", "id"):
                return None
            return "SELECT COUNT(*) AS customers FROM customers"

        # ④ 上月总销售额
        if re.search(r"总(销售额|营收|收入)", question):
            return (
                "SELECT SUM(oi.qty * oi.unit_price) AS revenue\n"
                "FROM order_items oi\n"
                "JOIN orders o ON o.id = oi.order_id\n"
                "WHERE o.status = '已完成'\n"
                f"  AND o.order_date >= '{last}-01' AND o.order_date < '{_next_month(last)}'"
            )
        return None

    # -- 第二轮：工具结果 → 自然语言 --------------------------------------
    def _summarize(self, messages: list[dict]) -> dict:
        payload = json.loads(messages[-1]["content"])
        if not payload.get("ok"):
            return {"role": "assistant", "content": f"查询失败：{payload.get('error')}", "tool_calls": []}
        rows = payload.get("rows") or []
        cols = payload.get("columns") or []
        if not rows:
            return {"role": "assistant", "content": "查询成功但结果为空。", "tool_calls": []}

        if len(cols) == 2 and cols[0] == "product":
            lines = [f"{i}. {r['product']} —— {r['revenue']} 元" for i, r in enumerate(rows, 1)]
            total = sum(r["revenue"] for r in rows)
            return {
                "role": "assistant",
                "content": f"共 {len(rows)} 个产品：\n" + "\n".join(lines) + f"\n合计 {total} 元。",
                "tool_calls": [],
            }
        if len(cols) == 2 and cols[0] == "region":
            body = "，".join(f"{r['region']} {r['revenue']} 元" for r in rows)
            return {"role": "assistant", "content": f"按区域：{body}。", "tool_calls": []}
        if cols == ["customers"]:
            return {"role": "assistant", "content": f"一共有 {rows[0]['customers']} 个客户。", "tool_calls": []}
        if cols == ["revenue"]:
            return {"role": "assistant", "content": f"合计 {rows[0]['revenue']} 元。", "tool_calls": []}
        return {"role": "assistant", "content": f"查到 {len(rows)} 行：{rows[:3]}", "tool_calls": []}


class Agent:
    """最小的工具循环：最多 max_rounds 轮，每轮最多一个工具调用。"""

    def __init__(self, model, system_prompt: str, max_rounds: int = 4):
        self.model = model
        self.system_prompt = system_prompt
        self.max_rounds = max_rounds

    def ask(self, question: str) -> dict:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": question},
        ]
        trace = []
        sql = None
        rows = None
        rounds = 0
        for rounds in range(1, self.max_rounds + 1):
            msg = self.model.complete(messages)
            messages.append(msg)
            trace.append({"round": rounds, "type": "assistant", "content": msg["content"]})
            if not msg["tool_calls"]:
                break
            for call in msg["tool_calls"]:
                fn = call["function"]
                args = json.loads(fn["arguments"])  # 字符串 → dict，真实 API 也这样
                sql = args.get("sql", sql)
                result = client.call_tool(fn["name"], args)
                payload = json.loads(result["text"])
                rows = payload.get("rows")
                trace.append({
                    "round": rounds,
                    "type": "tool",
                    "tool": fn["name"],
                    "is_error": result["is_error"],
                    "ok": payload.get("ok"),
                    "row_count": payload.get("row_count"),
                    "elapsed_ms": payload.get("elapsed_ms"),
                })
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result["text"],
                })
        answer = next((m["content"] for m in reversed(messages) if m["role"] == "assistant"), "")
        return {
            "answer": answer,
            "sql": sql,
            "rows": rows,
            "rounds": rounds,
            "tool_calls": sum(1 for t in trace if t["type"] == "tool"),
            "trace": trace,
        }


def build_agent(today: str = "2026-11-01") -> tuple[Agent, SchemaMap, int]:
    schema, calls = load_schema()
    model = OfflineModel(schema, today=today)
    prompt = SYSTEM_TEMPLATE.format(
        today=today, last_month=_prev_month(today), schema=schema.render()
    )
    return Agent(model, prompt), schema, calls


def chat_api(messages: list[dict], model: str = "deepseek-chat") -> dict:
    """真的调一次 DeepSeek。只在设了 DEEPSEEK_API_KEY 时可用 —— 自检绝不走这条路。"""
    import httpx

    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise RuntimeError("未设 DEEPSEEK_API_KEY；离线路径请用 OfflineModel")
    resp = httpx.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": messages, "temperature": 0},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]


__all__ = ["Agent", "OfflineModel", "SchemaMap", "build_agent", "chat_api", "load_schema"]
