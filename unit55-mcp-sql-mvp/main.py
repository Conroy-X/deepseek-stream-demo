"""Unit 55 · 项目 4 MVP：自然语言 → SQL → 结果 → 人话。

链路只有四环，本文件每一环都有断言：
    schema 注入 → 生成 SQL → MCP 只读执行 → 结果转自然语言

启动：
    ssh wsl-debian
    /home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --port 8055
    打开 http://127.0.0.1:8055/

自检（离线，会自动起 stdio 子进程）：
    /home/rik/.cache/aj/runner/bin/python main.py
"""

from __future__ import annotations

import json
import os
import sys
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import agent as agent_mod
import client
import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 55 · MCP 数据库 Agent MVP", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

LAST_TRACE: dict = {}

SQL_TOP3 = """SELECT p.name AS product, SUM(oi.qty * oi.unit_price) AS revenue
FROM order_items oi
JOIN orders o ON o.id = oi.order_id
JOIN products p ON p.id = oi.product_id
WHERE o.region = '华东' AND o.status = '已完成'
  AND o.order_date >= '2026-10-01' AND o.order_date < '2026-11-01'
GROUP BY p.id, p.name
ORDER BY revenue DESC
LIMIT 3"""


class AskIn(BaseModel):
    question: str = Field(min_length=1, description="自然语言问题")
    today: str = Field(default=db.AS_OF, description="账期锚点，默认 2026-11-01")


class SqlIn(BaseModel):
    sql: str = Field(min_length=1)
    max_rows: int = Field(default=50, ge=1, le=500)


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
@app.get("/health", summary="真的起一次 stdio 子进程，证明链路通")
def health() -> dict:
    info = client.handshake()
    return {
        "ok": True,
        "server": info["server"],
        "protocol": info["protocol"],
        "tools": [t["name"] for t in info["tools"]],
        "db": str(db.DB_PATH),
        "as_of": db.AS_OF,
    }


@app.get("/api/tools", summary="工具清单与 inputSchema（从真连接里读）")
def api_tools() -> dict:
    return client.handshake()


@app.get("/api/schema", summary="schema 注入用的文本（精简版）")
def api_schema() -> dict:
    schema, calls = agent_mod.load_schema()
    return {"text": schema.render(), "calls": calls, "chars": len(schema.render())}


@app.post("/api/sql", summary="把一条 SQL 交给 MCP 工具执行")
def api_sql(req: SqlIn) -> dict:
    t0 = time.perf_counter()
    result = client.call_tool("run_readonly_sql", {"sql": req.sql, "max_rows": req.max_rows})
    payload = json.loads(result["text"])
    payload["roundtrip_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return payload


@app.post("/api/ask", summary="端到端：问题 → SQL → 结果 → 回答")
def api_ask(req: AskIn) -> dict:
    global LAST_TRACE
    t0 = time.perf_counter()
    runner, schema, calls = agent_mod.build_agent(today=req.today)
    out = runner.ask(req.question)
    out["schema_chars"] = len(schema.render())
    out["schema_calls"] = calls
    out["total_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    LAST_TRACE = out
    return out


@app.get("/api/trace", summary="上一次 /api/ask 的完整轨迹")
def api_trace() -> dict:
    return LAST_TRACE or {"hint": "还没有问过问题"}


@app.get("/", include_in_schema=False)
def index():
    if os.path.exists(STATIC_INDEX):
        return FileResponse(STATIC_INDEX)
    return {"hint": "static/index.html 不存在，看 /docs"}


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 55 · MCP 数据库 Agent MVP 自检（真起 stdio 子进程）\n")
    db.reset_db()

    # 一次会话里跑完全部工具调用 —— 每次 stdio 往返约 0.7 秒，省着用
    t_batch = time.perf_counter()
    batch = client.call_many([
        ("list_tables", {}),
        ("describe_table", {"table": "products"}),
        ("describe_table", {"table": "不存在的表"}),
        ("run_readonly_sql", {"sql": "SELECT COUNT(*) AS n FROM customers"}),
        ("run_readonly_sql", {"sql": "SELECT COUNT(*) AS n FROM products"}),
        ("run_readonly_sql", {"sql": "SELECT COUNT(*) AS n FROM orders"}),
        ("run_readonly_sql", {"sql": "SELECT COUNT(*) AS n FROM order_items"}),
        ("run_readonly_sql", {"sql": SQL_TOP3}),
        ("run_readonly_sql", {"sql": (
            "SELECT SUM(oi.qty * oi.unit_price) AS revenue FROM order_items oi "
            "JOIN orders o ON o.id = oi.order_id WHERE o.region = '华东' "
            "AND o.status = '已完成' AND o.order_date >= '2026-10-01' "
            "AND o.order_date < '2026-11-01'"
        )}),
        ("run_readonly_sql", {"sql": (
            "SELECT o.region AS region, SUM(oi.qty * oi.unit_price) AS revenue "
            "FROM order_items oi JOIN orders o ON o.id = oi.order_id "
            "WHERE o.status = '已完成' AND o.order_date >= '2026-10-01' "
            "AND o.order_date < '2026-11-01' GROUP BY o.region ORDER BY revenue DESC"
        )}),
        ("run_readonly_sql", {"sql": "DROP TABLE customers"}),
        ("run_readonly_sql", {"sql": "INSERT INTO customers VALUES (99,'x','y','z','w')"}),
        ("run_readonly_sql", {"sql": "SELECT 1; DROP TABLE customers"}),
        ("run_readonly_sql", {"sql": "PRAGMA table_info(customers)"}),
        ("run_readonly_sql", {"sql": "SELECT * FROM order_items", "max_rows": 5}),
        ("run_readonly_sql", {"sql": "SELECT * FROM no_such_table"}),
    ])
    batch_ms = round((time.perf_counter() - t_batch) * 1000, 1)
    R = [json.loads(r["text"]) for r in batch["results"]]

    print("  —— 握手：一条 stdio 子进程 ——")
    check("服务器名是 shop-mcp", batch["server"] == "shop-mcp", batch["server"])
    check("协商出版本号", isinstance(batch["protocol"], str) and batch["protocol"].startswith("2025"),
          batch["protocol"])
    names = sorted(t["name"] for t in batch["tools"])
    check("暴露 3 个工具", len(batch["tools"]) == 3, str(names))
    check("工具名齐全", names == ["describe_table", "list_tables", "run_readonly_sql"], str(names))
    check("每个工具都带 inputSchema", all(t["input_schema"] for t in batch["tools"]))
    sql_tool = next(t for t in batch["tools"] if t["name"] == "run_readonly_sql")
    check("run_readonly_sql 必填 sql", sql_tool["input_schema"]["required"] == ["sql"],
          json.dumps(sql_tool["input_schema"], ensure_ascii=False))
    check("max_rows 是带默认值的整数",
          sql_tool["input_schema"]["properties"]["max_rows"]["type"] == "integer")

    print("\n  —— schema 注入：表与列 ——")
    tables = R[0]["tables"]
    check("列出 4 张表", len(tables) == 4, str(tables))
    check("表名按字母序", tables == sorted(tables), str(tables))
    check("row_counts 里 orders 有 30 行", R[0]["row_counts"]["orders"] == 30, str(R[0]["row_counts"]))
    check("describe products 返回 4 列", R[1]["count"] == 4, str(R[1]["count"]))
    check("列名顺序与 DDL 一致", [c["name"] for c in R[1]["columns"]] ==
          ["id", "name", "category", "price"], str([c["name"] for c in R[1]["columns"]]))
    check("price 类型是 INTEGER", R[1]["columns"][3]["type"] == "INTEGER", str(R[1]["columns"][3]))
    check("未知表返回 ok=False 而不是抛异常", R[2]["ok"] is False and
          batch["results"][2]["is_error"] is False, str(batch["results"][2]["is_error"]))
    check("未知表的报错列出可用表", "可用表" in R[2]["error"], R[2]["error"])

    ddl = client.read_resource("sql://schema/full")
    check("resource 给出全量 DDL", ddl.count("CREATE TABLE") == 4, str(ddl.count("CREATE TABLE")))
    check("DDL 里能查到 order_items 的列", "unit_price" in ddl)

    print("\n  —— 只读执行：数字全部硬核对 ——")
    check("customers 12 行", R[3]["rows"][0]["n"] == 12, str(R[3]["rows"]))
    check("products 15 行", R[4]["rows"][0]["n"] == 15)
    check("orders 30 行", R[5]["rows"][0]["n"] == 30)
    check("order_items 60 行", R[6]["rows"][0]["n"] == 60)
    check("返回里带 columns", R[3]["columns"] == ["n"], str(R[3]["columns"]))
    top = R[7]["rows"]
    check("前 3 名正好 3 行", len(top) == 3, str(top))
    check("第 1 名是 4K 显示器 MN-27", top[0]["product"] == "4K 显示器 MN-27", str(top[0]))
    check("第 1 名营收 7596 元", top[0]["revenue"] == 7596, str(top[0]["revenue"]))
    check("第 2 名营收 4798 元", top[1]["revenue"] == 4798, str(top[1]["revenue"]))
    check("第 3 名营收 2394 元", top[2]["revenue"] == 2394, str(top[2]["revenue"]))
    check("华东上月总营收 20477 元", R[8]["rows"][0]["revenue"] == 20477, str(R[8]["rows"]))
    check("分区结果 4 行", len(R[9]["rows"]) == 4, str(R[9]["rows"]))
    check("分区第一是华东", R[9]["rows"][0]["region"] == "华东", str(R[9]["rows"][0]))
    check("单条查询耗时被记录", isinstance(R[7].get("elapsed_ms"), (int, float)) and
          R[7]["elapsed_ms"] >= 0, str(R[7].get("elapsed_ms")))

    print("\n  —— 只读连接自己挡写操作（这一层不靠代码） ——")
    check("DROP TABLE 被拒", R[10]["ok"] is False, str(R[10]))
    check("报错是 readonly database", "readonly" in R[10]["error"], R[10].get("error", ""))
    check("INSERT 被拒", R[11]["ok"] is False and "readonly" in R[11]["error"], str(R[11]))
    check("多语句被拒（一次只能一条）", R[12]["ok"] is False and
          "one statement" in R[12]["error"], R[12].get("error", ""))
    check("PRAGMA 在只读连接上仍然放行（第 57 章要显式拦）",
          R[13]["ok"] is True, str(R[13])[:80])
    check("语法错也被转成可读文本", R[15]["ok"] is False and
          "no such table" in R[15]["error"], R[15].get("error", ""))

    print("\n  —— max_rows 截断 ——")
    check("max_rows=5 只回 5 行", R[14]["row_count"] == 5, str(R[14]["row_count"]))
    check("并标记 truncated", R[14]["truncated"] is True)
    check("截断的 5 行仍是真数据", len(R[14]["rows"]) == 5 and "qty" in R[14]["rows"][0])

    print("\n  —— Agent 端到端（离线规则模型） ——")
    runner, schema, schema_calls = agent_mod.build_agent(today="2026-11-01")
    check("schema 由 5 次 MCP 调用拼出来", schema_calls == 5, str(schema_calls))
    check("SchemaMap 认得 order_items.unit_price", schema.has("order_items", "unit_price"))
    out = runner.ask("上月华东区销售额前 3 的产品是哪些？")
    check("一轮工具 + 一轮总结 = 2 轮", out["rounds"] == 2, str(out["rounds"]))
    check("只调了 1 次工具", out["tool_calls"] == 1, str(out["tool_calls"]))
    check("生成的 SQL 是 SELECT 开头", (out["sql"] or "").lstrip().upper().startswith("SELECT"),
          (out["sql"] or "")[:40])
    check("生成的 SQL 含 JOIN 三张表", out["sql"].count("JOIN") == 2, str(out["sql"].count("JOIN")))
    check("回答里带第 1 名的 7596", "7596" in out["answer"], out["answer"])
    check("回答里带产品名", "4K 显示器 MN-27" in out["answer"], out["answer"])
    check("回答里带第 3 名", "2394" in out["answer"], out["answer"])
    check("rows 回传给调用方", len(out["rows"] or []) == 3, str(len(out["rows"] or [])))
    check("trace 记了 tool 事件", any(t["type"] == "tool" for t in out["trace"]), str(out["trace"]))

    out2 = runner.ask("按邮箱域名统计客户分布")
    check("无法映射的问题返回「无法回答」", "无法回答" in out2["answer"], out2["answer"])
    check("并且没有生成 SQL", out2["sql"] is None, str(out2["sql"]))
    check("也没有调用工具", out2["tool_calls"] == 0, str(out2["tool_calls"]))

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    c = TestClient(app)
    r = c.get("/health")
    check("GET /health 200 且认出服务名",
          r.status_code == 200 and r.json()["server"] == "shop-mcp", str(r.status_code))
    check("/health 列出 3 个工具", len(r.json()["tools"]) == 3, str(r.json()["tools"]))

    r = c.post("/api/sql", json={"sql": "SELECT COUNT(*) AS n FROM orders", "max_rows": 10})
    check("POST /api/sql 返回数据", r.status_code == 200 and r.json()["rows"][0]["n"] == 30, r.text[:120])
    check("/api/sql 附带往返耗时", "roundtrip_ms" in r.json())

    r = c.post("/api/sql", json={"sql": "DELETE FROM orders"})
    check("POST /api/sql 的写操作返回 ok=False", r.json()["ok"] is False, r.text[:120])

    r = c.post("/api/ask", json={"question": "上月华东区销售额前 3 的产品是哪些？"})
    check("POST /api/ask 端到端跑通", r.status_code == 200 and "7596" in r.json()["answer"],
          r.text[:160])
    check("/api/ask 回传本次 schema 字符数", r.json()["schema_chars"] > 100)

    r = c.get("/api/trace")
    check("GET /api/trace 有上一次轨迹", r.json()["rounds"] == 2, r.text[:120])

    r = c.get("/")
    check("GET / 返回页面", r.status_code == 200 and "实验台" in r.text, str(r.status_code))

    print(f"\n  批量 {len(batch['results'])} 次工具调用耗时 {batch_ms} ms")
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
