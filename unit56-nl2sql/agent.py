"""Unit 56 的 Agent：schema 怎么给，决定 SQL 写得对不对。

三件事：
1. `SchemaMap` —— 从 MCP 拉到的真实列清单；
2. `prompt_for(question, mode)` —— 三种 schema 表示（full / compact / relevant）；
3. `OfflineModel` —— 规则模型，能生成带 JOIN 的 SQL，也会对**列名歧义**做限定。

歧义是本单元的重点：`id` 在四张表里都有，`name` 在 customers 和 products 里都有，
`region` 在 customers 和 orders 里都有。生成 SQL 时如果不加表别名，
SQLite 会直接报 `ambiguous column name` —— 这条错误第 58 章还会再用到。
"""

from __future__ import annotations

import json
import os
import re

import client
import schema as schema_mod

TOOL_NAME = "run_readonly_sql"


def _prev_month(today: str) -> str:
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
    def __init__(self, tables: dict[str, list[dict]]):
        self.tables = tables

    def has_table(self, table: str) -> bool:
        return table in self.tables

    def has(self, table: str, column: str) -> bool:
        return any(c["name"] == column for c in self.tables.get(table, []))

    def columns(self, table: str) -> list[str]:
        return [c["name"] for c in self.tables.get(table, [])]

    def all_columns(self) -> dict[str, list[str]]:
        """列名 → 出现在哪些表。用来量化「歧义到底有多严重」。"""
        out: dict[str, list[str]] = {}
        for table, cols in self.tables.items():
            for c in cols:
                out.setdefault(c["name"], []).append(table)
        return out

    def ambiguous(self) -> dict[str, list[str]]:
        return {k: v for k, v in self.all_columns().items() if len(v) > 1}


def load_schema() -> tuple[SchemaMap, int]:
    first = client.call_many([("list_tables", {})])
    tables = json.loads(first["results"][0]["text"])["tables"]
    second = client.call_many([("describe_table", {"table": t}) for t in tables])
    mapping = {}
    for item in second["results"]:
        payload = json.loads(item["text"])
        mapping[payload["table"]] = payload["columns"]
    return SchemaMap(mapping), 1 + len(tables)


SYSTEM_TEMPLATE = """你是 SQL 生成器。今天 {today}，「上月」指 {last_month}。
只输出一条 SQLite 方言的 SELECT，不要解释。

数据库结构：
{schema}

注意：多表 JOIN 时一律用表别名限定列名，id / name / region 在多张表里都有。"""


def prompt_for(smap: SchemaMap, question: str, mode: str = "relevant") -> str:
    body = schema_mod.render_for_prompt(smap.tables, question, mode=mode, ddl=_full_ddl())
    return SYSTEM_TEMPLATE.format(today="2026-11-01", last_month=_prev_month("2026-11-01"), schema=body)


_DDL_CACHE: str | None = None


def _full_ddl() -> str:
    """全量 DDL 走 resource 拿 —— 服务端有权决定「完整的表结构」以什么形式外发。"""
    global _DDL_CACHE
    if _DDL_CACHE is None:
        _DDL_CACHE = client.read_resource("sql://schema/full")
    return _DDL_CACHE


class OfflineModel:
    """规则模型：只认这几种问法，认不出就说不知道。"""

    name = "offline-rules"

    def __init__(self, smap: SchemaMap, today: str = "2026-11-01"):
        self.smap = smap
        self.today = today

    def complete(self, messages: list[dict]) -> dict:
        if messages and messages[-1]["role"] == "tool":
            return self._summarize(messages)
        return self._plan(messages[-1]["content"])

    def _plan(self, question: str) -> dict:
        sql = self.build_sql(question)
        if sql is None:
            return {"role": "assistant", "content": self.refusal(question), "tool_calls": []}
        return {
            "role": "assistant",
            "content": sql,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": TOOL_NAME, "arguments": json.dumps({"sql": sql}, ensure_ascii=False)},
            }],
        }

    def refusal(self, question: str) -> str:
        return (
            f"无法回答：「{question}」里没有能对上已知维度/指标的词。"
            f"可用维度：区域、产品、客户、时间；可用指标：销售额、订单数、客户数。"
        )

    # ------------------------------------------------------------------
    def build_sql(self, question: str) -> str | None:
        s = self.smap
        region = next((r for r in ("华东", "华北", "华南", "西部") if r in question), None)
        last = _prev_month(self.today)
        window = f"o.order_date >= '{last}-01' AND o.order_date < '{_next_month(last)}'"
        top = re.search(r"前\s*(\d+)", question)
        n = int(top.group(1)) if top else 3

        # ① 客户维度 + 金额 → customers JOIN orders JOIN order_items
        if re.search(r"客户|买家|用户", question) and re.search(r"成交额|销售额|金额|消费", question):
            if not (s.has("customers", "name") and s.has("orders", "customer_id")):
                return None
            where = [f"o.status = '已完成'", window]
            if region:
                where.append(f"o.region = '{region}'")
            return (
                "SELECT c.name AS customer, SUM(oi.qty * oi.unit_price) AS revenue\n"
                "FROM orders o\n"
                "JOIN customers c ON c.id = o.customer_id\n"
                "JOIN order_items oi ON oi.order_id = o.id\n"
                f"WHERE {' AND '.join(where)}\n"
                "GROUP BY c.id, c.name\n"
                "ORDER BY revenue DESC\n"
                f"LIMIT {n}"
            )

        # ② 产品维度 + 金额 → products JOIN order_items JOIN orders
        if re.search(r"产品|商品|SKU", question) and re.search(r"销售额|营收|金额|卖", question):
            if not (s.has("products", "name") and s.has("order_items", "unit_price")):
                return None
            where = ["o.status = '已完成'", window]
            if region:
                where.append(f"o.region = '{region}'")
            return (
                "SELECT p.name AS product, SUM(oi.qty * oi.unit_price) AS revenue\n"
                "FROM order_items oi\n"
                "JOIN orders o ON o.id = oi.order_id\n"
                "JOIN products p ON p.id = oi.product_id\n"
                f"WHERE {' AND '.join(where)}\n"
                "GROUP BY p.id, p.name\n"
                "ORDER BY revenue DESC\n"
                f"LIMIT {n}"
            )

        # ③ 各区域
        if re.search(r"各区域|按区域|分区", question) and re.search(r"销售额|营收", question):
            return (
                "SELECT o.region AS region, SUM(oi.qty * oi.unit_price) AS revenue\n"
                "FROM order_items oi\n"
                "JOIN orders o ON o.id = oi.order_id\n"
                f"WHERE o.status = '已完成' AND {window}\n"
                "GROUP BY o.region\n"
                "ORDER BY revenue DESC"
            )

        # ④ 客户数
        if re.search(r"多少(个|位)?客户|客户(数|数量)", question):
            return "SELECT COUNT(*) AS customers FROM customers"
        return None

    def _summarize(self, messages: list[dict]) -> dict:
        payload = json.loads(messages[-1]["content"])
        if not payload.get("ok"):
            return {"role": "assistant", "content": f"SQL 执行失败：{payload.get('error')}", "tool_calls": []}
        rows = payload.get("rows") or []
        cols = payload.get("columns") or []
        if len(cols) == 2 and cols[0] in ("customer", "product"):
            label = "客户" if cols[0] == "customer" else "产品"
            body = "\n".join(f"{i}. {r[cols[0]]} —— {r['revenue']} 元" for i, r in enumerate(rows, 1))
            return {"role": "assistant", "content": f"按销售额排序的{label}：\n{body}", "tool_calls": []}
        if len(cols) == 2 and cols[0] == "region":
            body = "，".join(f"{r['region']} {r['revenue']} 元" for r in rows)
            return {"role": "assistant", "content": f"按区域：{body}。", "tool_calls": []}
        if cols == ["customers"]:
            return {"role": "assistant", "content": f"共 {rows[0]['customers']} 个客户。", "tool_calls": []}
        return {"role": "assistant", "content": f"查到 {len(rows)} 行。", "tool_calls": []}


class Agent:
    def __init__(self, model: OfflineModel, smap: SchemaMap, mode: str = "relevant", max_rounds: int = 4):
        self.model = model
        self.smap = smap
        self.mode = mode
        self.max_rounds = max_rounds

    def ask(self, question: str) -> dict:
        system = prompt_for(self.smap, question, self.mode)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]
        trace, sql, rows, rounds = [], None, None, 0
        for rounds in range(1, self.max_rounds + 1):
            msg = self.model.complete(messages)
            messages.append(msg)
            trace.append({"round": rounds, "type": "assistant", "content": msg["content"]})
            if not msg["tool_calls"]:
                break
            for call in msg["tool_calls"]:
                fn = call["function"]
                args = json.loads(fn["arguments"])
                sql = args.get("sql", sql)
                result = client.call_tool(fn["name"], args)
                payload = json.loads(result["text"])
                rows = payload.get("rows")
                trace.append({
                    "round": rounds, "type": "tool", "tool": fn["name"],
                    "ok": payload.get("ok"), "row_count": payload.get("row_count"),
                    "error": payload.get("error"),
                })
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result["text"]})
        answer = next((m["content"] for m in reversed(messages) if m["role"] == "assistant"), "")
        return {
            "answer": answer,
            "sql": sql,
            "rows": rows,
            "rounds": rounds,
            "mode": self.mode,
            "prompt_chars": len(system),
            "trace": trace,
        }


def build_agent(mode: str = "relevant", today: str = "2026-11-01") -> tuple[Agent, SchemaMap, int]:
    smap, calls = load_schema()
    return Agent(OfflineModel(smap, today=today), smap, mode=mode), smap, calls


def chat_api(messages: list[dict], model: str = "deepseek-chat") -> dict:
    """真调 DeepSeek，被环境变量挡着；自检绝不走这条路。"""
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
