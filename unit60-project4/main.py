"""Unit 60 · 项目 4 交付：MCP Database Agent。

把 55-59 的能力串起来：schema 注入 → NL→SQL → 四道安全闸 → 错误重试 → 结构化日志。
单文件可跑，自检 ≥ 30 条。

    python main.py
    uvicorn main:app --port 8060
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "project4.db"

app = FastAPI(title="Unit 60 · 项目 4 交付")


# ── 数据 ────────────────────────────────────────────────────────────────────

CUSTOMERS = [
    ("c-001", "王强", "华东", "t-acme"),
    ("c-002", "李娜", "华南", "t-acme"),
    ("c-003", "张伟", "华东", "t-globex"),
    ("c-004", "陈静", "华北", "t-globex"),
]
ORDERS = [
    ("o-001", "c-001", 998, "shipped", "t-acme"),
    ("o-002", "c-002", 499, "pending", "t-acme"),
    ("o-003", "c-003", 1899, "shipped", "t-globex"),
    ("o-004", "c-004", 259, "cancelled", "t-globex"),
]


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript("""
            CREATE TABLE customers (id TEXT PRIMARY KEY, name TEXT, region TEXT, tenant_id TEXT);
            CREATE TABLE orders (id TEXT PRIMARY KEY, customer_id TEXT, amount INTEGER, status TEXT, tenant_id TEXT);
        """)
        conn.executemany("INSERT INTO customers VALUES (?,?,?,?)", CUSTOMERS)
        conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", ORDERS)
        conn.commit()
    finally:
        conn.close()


# ── 四道闸（简化版，完整版在 unit57）──────────────────────────────────────

def strip_comments(sql: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", s)


def gate_lexical(sql: str) -> tuple[bool, str]:
    stripped = strip_comments(sql).strip()
    if not stripped:
        return True, "空 SQL"
    body = stripped.rstrip(";").strip()
    if ";" in body:
        return True, "多语句"
    head = body.split(None, 1)[0].upper()
    if head != "SELECT":
        return True, f"只允许 SELECT（{head}）"
    return False, body


def gate_limit(sql: str, default: int = 100) -> str:
    if re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE):
        return sql
    return f"{sql} LIMIT {default}"


def gate_tenant(sql: str, tenant: str) -> str:
    upper = sql.upper()
    where_pos = upper.find("WHERE")
    if where_pos >= 0:
        return sql[:where_pos + 5] + f" tenant_id = '{tenant}' AND " + sql[where_pos + 5:]
    # 没 WHERE：在 GROUP BY / ORDER BY / HAVING / LIMIT 之前插
    insert_before = len(sql)
    for kw in ("GROUP BY", "ORDER BY", "HAVING", "LIMIT"):
        pos = upper.find(kw)
        if pos >= 0 and pos < insert_before:
            insert_before = pos
    if insert_before < len(sql):
        return sql[:insert_before].rstrip() + f" WHERE tenant_id = '{tenant}' " + sql[insert_before:]
    return sql.rstrip() + f" WHERE tenant_id = '{tenant}'"


# ── 错误重试（简化版，完整版在 unit58）─────────────────────────────────────

def normalize_error(raw: str) -> str:
    if "no such column" in raw.lower():
        return "列名不存在"
    if "no such table" in raw.lower():
        return "表名不存在"
    if "syntax error" in raw.lower():
        return "SQL 语法错"
    return raw[:80]


def fix_sql(sql: str, error: str) -> str:
    # 简化：只修最常见的一个（列名 customers → customer_id 这种不修）
    return sql  # 生产里这里会做模糊匹配


def run_with_recovery(sql: str, tenant: str, max_rounds: int = 3) -> dict[str, Any]:
    attempts = []
    current = sql
    for r in range(1, max_rounds + 1):
        blocked, body = gate_lexical(current)
        if blocked:
            return {"success": False, "blocked_by": "gate_lexical", "reason": body, "attempts": attempts, "rounds": r}
        with_limit = gate_limit(body)
        with_tenant = gate_tenant(with_limit, tenant)
        ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            cur = ro.execute(with_tenant)
            rows = cur.fetchmany(50)
            cols = [c[0] for c in cur.description] if cur.description else []
            attempts.append({"round": r, "sql": with_tenant, "ok": True})
            return {"success": True, "rows": [dict(zip(cols, row)) for row in rows], "final_sql": with_tenant, "attempts": attempts, "rounds": r}
        except sqlite3.OperationalError as e:
            attempts.append({"round": r, "sql": with_tenant, "ok": False, "error": str(e), "normalized": normalize_error(str(e))})
            current = fix_sql(current, normalize_error(str(e)))
        finally:
            ro.close()
    return {"success": False, "attempts": attempts, "rounds": max_rounds}


# ── NL→SQL（离线规则模型，完整版在 unit56）─────────────────────────────────

def nl_to_sql(question: str) -> str | None:
    q = question.lower()
    if "销售额" in question or "订单金额" in question:
        if "前" in question:
            return "SELECT customer_id, sum(amount) as total FROM orders GROUP BY customer_id ORDER BY total DESC"
        return "SELECT customer_id, sum(amount) as total FROM orders GROUP BY customer_id"
    if "订单" in question and "状态" in question:
        return "SELECT * FROM orders"
    if "客户" in question:
        return "SELECT * FROM customers"
    return None


# ── 结构化日志（简化版，完整版在 unit59）────────────────────────────────────

LOG_FIELDS = {"ts", "level", "event", "trace_id", "ok", "duration_ms", "rounds", "error_code"}
_logs: list[dict[str, Any]] = []


def log(**fields: Any) -> None:
    entry = {k: v for k, v in fields.items() if k in LOG_FIELDS}
    _logs.append(entry)


# ── 端到端 ──────────────────────────────────────────────────────────────────

class AskIn(BaseModel):
    question: str
    tenant: str = "t-acme"


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "unit": 60, "project": "MCP Database Agent", "chains": ["schema", "nl2sql", "gates", "recovery", "logging"]}


@app.post("/api/ask")
def api_ask(payload: AskIn) -> dict[str, Any]:
    trace_id = str(uuid.uuid4())[:8]
    start = time.monotonic()

    sql = nl_to_sql(payload.question)
    if sql is None:
        log(ts=time.time(), level="WARN", event="no_sql", trace_id=trace_id, ok=False, duration_ms=0)
        return {"answer": "无法生成 SQL", "trace_id": trace_id}

    result = run_with_recovery(sql, payload.tenant)
    duration = (time.monotonic() - start) * 1000
    log(ts=time.time(), level="INFO" if result["success"] else "ERROR",
        event="e2e", trace_id=trace_id, ok=result["success"],
        duration_ms=round(duration, 1), rounds=result.get("rounds", 0))

    if not result["success"]:
        return {"answer": f"查询失败：{result.get('reason', '未知')}", "trace_id": trace_id, "result": result}

    rows = result["rows"]
    answer = f"查询到 {len(rows)} 行。"
    if rows and "total" in rows[0]:
        answer = "销售额排名：\n" + "\n".join(f"  {r.get('customer_id', '?')}: ¥{r['total']}" for r in rows[:3])
    elif rows:
        answer = f"查询到 {len(rows)} 行：\n" + "\n".join(f"  {r}" for r in rows[:3])

    return {"answer": answer, "trace_id": trace_id, "rows": rows, "final_sql": result.get("final_sql"), "rounds": result["rounds"]}


@app.get("/api/logs")
def api_logs() -> dict[str, Any]:
    return {"logs": _logs[-10:], "total": len(_logs)}


@app.get("/api/attacks")
def api_attacks() -> dict[str, Any]:
    """4 条攻击被四道闸拦。"""
    attacks = [
        ("多语句", "SELECT * FROM orders; DROP TABLE orders"),
        ("UPDATE", "UPDATE orders SET amount=0"),
        ("PRAGMA", "PRAGMA database_list"),
        ("ATTACH", "ATTACH DATABASE '/tmp/x' AS x"),
    ]
    out = []
    for label, sql in attacks:
        r = run_with_recovery(sql, "t-acme")
        out.append({"label": label, "sql": sql, "blocked": not r["success"], "reason": r.get("reason", "")})
    return {"attacks": out}


@app.get("/api/cross-tenant")
def api_cross() -> dict[str, Any]:
    a = run_with_recovery("SELECT * FROM orders", "t-acme")
    g = run_with_recovery("SELECT * FROM orders", "t-globex")
    return {
        "acme_rows": a.get("rows", []),
        "globex_rows": g.get("rows", []),
        "acme_sees_globex": any(r.get("tenant_id") == "t-globex" for r in a.get("rows", [])),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


# ── 自检 ─────────────────────────────────────────────────────────────────────

passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    from fastapi.testclient import TestClient

    print("\n=== Unit 60 · 项目 4 交付 自检 ===\n")
    init_db()

    print("  —— 端到端正常链路 ——")
    r = run_with_recovery("SELECT * FROM orders", "t-acme")
    check("正常查询成功", r["success"], str(r))
    check("自动注入 tenant", "t-acme" in r.get("final_sql", ""), r.get("final_sql"))
    check("自动补 LIMIT", "LIMIT" in r.get("final_sql", "").upper(), r.get("final_sql"))
    check("A 租户只看到自己的 2 笔", len(r["rows"]) == 2, str(len(r["rows"])))

    r = run_with_recovery("SELECT * FROM orders", "t-globex")
    check("G 租户只看到自己的 2 笔", len(r["rows"]) == 2, str(len(r["rows"])))

    print("  —— 四道闸攻击拦截 ——")
    for label, sql in [("多语句", "SELECT 1; DROP TABLE orders"), ("UPDATE", "UPDATE orders SET amount=0"), ("PRAGMA", "PRAGMA database_list"), ("ATTACH", "ATTACH DATABASE 'x' AS y")]:
        r = run_with_recovery(sql, "t-acme")
        check(f"攻击「{label}」被拦", not r["success"], str(r))

    print("  —— 跨租户隔离 ——")
    cross = {"acme_rows": run_with_recovery("SELECT * FROM orders", "t-acme")["rows"],
             "globex_rows": run_with_recovery("SELECT * FROM orders", "t-globex")["rows"]}
    check("A 看不到 G", all(r["tenant_id"] == "t-acme" for r in cross["acme_rows"]), str(cross))
    check("G 看不到 A", all(r["tenant_id"] == "t-globex" for r in cross["globex_rows"]), str(cross))

    print("  —— 错误重试 ——")
    r = run_with_recovery("SELECT nonexistent FROM orders", "t-acme", max_rounds=3)
    check("修不好的 3 轮后停", not r["success"] and r["rounds"] == 3, str(r))
    check("错误历史有 3 条", len(r["attempts"]) == 3, str(len(r["attempts"])))

    print("  —— 结构化日志 ——")
    log(ts=time.time(), level="INFO", event="test", trace_id="t-test", ok=True, duration_ms=1.2, token="secret")
    last = _logs[-1]
    check("白名单保留 trace_id", "trace_id" in last, str(last))
    check("非白名单被剔除", "token" not in last, str(last))

    print("  —— NL→SQL ——")
    check("销售额问题生成 SQL", nl_to_sql("销售额排名") is not None, str(nl_to_sql("销售额排名")))
    check("无关问题返回 None", nl_to_sql("今天天气") is None, str(nl_to_sql("今天天气")))

    print("  —— HTTP 层 ——")
    client = TestClient(app)
    check("/health 200", client.get("/health").status_code == 200)
    h = client.get("/health").json()
    check("health 报出 5 条链", len(h["chains"]) == 5, str(h["chains"]))

    r = client.post("/api/ask", json={"question": "销售额排名", "tenant": "t-acme"})
    check("/api/ask 端到端跑通", r.status_code == 200 and "销售额" in r.json()["answer"], r.text[:120])
    check("/api/ask 带 trace_id", bool(r.json().get("trace_id")), str(r.json()))
    check("/api/ask 返回 final_sql", bool(r.json().get("final_sql")), str(r.json()))

    r = client.post("/api/ask", json={"question": "今天天气", "tenant": "t-acme"})
    check("无关问题返回无法生成", "无法" in r.json()["answer"], r.text[:120])

    attacks = client.get("/api/attacks").json()["attacks"]
    check("4 条攻击全被拦", all(a["blocked"] for a in attacks), str(attacks))

    cross = client.get("/api/cross-tenant").json()
    check("HTTP 跨租户隔离", cross["acme_sees_globex"] is False, str(cross))

    logs = client.get("/api/logs").json()
    check("/api/logs 返回日志", logs["total"] > 0, str(logs))

    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200)
    check("页面含关键文案", "项目 4" in page.text and len(page.text) > 4000, f"{len(page.text)} 字节")

    # 验证三个交付文档存在
    for doc in ("ARCHITECTURE.md", "SECURITY.md", "DEMO.md"):
        check(f"{doc} 存在", (BASE_DIR / doc).exists(), doc)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()