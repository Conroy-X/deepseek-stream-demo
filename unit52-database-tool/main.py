"""Unit 52 · Database Tool 实验台（端口 8052）。

教学核心：**「只读」不是一个动作，是连接的属性**。
本页面让你试 SELECT、试多语句、试注释攻击；同时给出 ro vs rw 的对照证据。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

import server as srv
from client import MCPClient

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Unit 52 · Database Tool")


class RunIn(BaseModel):
    sql: str


@app.on_event("startup")
def _startup() -> None:
    if not srv.DB_PATH.exists():
        srv.init_db()


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "unit": 52,
        "db": str(srv.DB_PATH.name),
        "tables": _tables(),
        "tools": ["list_tables", "describe_table", "run_readonly_sql"],
    }


@app.get("/api/schema")
def api_schema() -> dict[str, Any]:
    return {"tables": _tables(), "ro_vs_rw": {"ro": srv._try_write_on_ro(), "rw": srv._try_write_on_rw()}}


@app.get("/api/attacks")
def api_attacks() -> dict[str, Any]:
    """8 个攻击样例 + 1 个合法对照，按风险类型分组。"""
    samples = [
        ("合法 SELECT", "SELECT id, title FROM articles LIMIT 3", "ok"),
        ("多语句注入", "SELECT 1; DROP TABLE articles", "inject"),
        ("行注释绕过", "SELECT * FROM articles -- ; DROP TABLE articles", "inject"),
        ("块注释绕过", "/* SELECT */ SELECT 1", "inject"),
        ("PRAGMA 偷看", "PRAGMA database_list", "inject"),
        ("UPDATE", "UPDATE authors SET name='x' WHERE id='a-001'", "write"),
        ("DELETE", "DELETE FROM articles WHERE id='p-101'", "write"),
        ("INSERT", "INSERT INTO authors VALUES ('a-x','x','x','x')", "write"),
        ("ATTACH", "ATTACH DATABASE '/tmp/x.db' AS x", "write"),
    ]
    out = []
    for label, sql, kind in samples:
        result = srv.run_readonly_sql(sql)
        out.append({"label": label, "sql": sql, "kind": kind, "result": result[:200]})
    return {"samples": out, "note": "拦截来自三层：词法白名单 + 多语句拆分 + 真只读连接。"}


@app.post("/api/run")
def api_run(payload: RunIn) -> dict[str, Any]:
    result = srv.run_readonly_sql(payload.sql)
    return {"sql": payload.sql, "result": result}


@app.get("/api/mcp")
def api_mcp() -> dict[str, Any]:
    """真起一次子进程，列出工具并执行一次。"""
    async def probe() -> dict[str, Any]:
        async with MCPClient() as c:
            tools = await c.list_tools()
            r1 = await c.call_tool("list_tables", {})
            r2 = await c.call_tool("run_readonly_sql", {"sql": "SELECT id FROM authors LIMIT 2"})
            return {
                "server": c.server_name,
                "protocol": c.protocol_version,
                "tools": tools,
                "calls": [r1.to_dict(), r2.to_dict()],
            }
    return anyio.run(probe)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


def _tables() -> list[dict[str, Any]]:
    conn = srv._ro()
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()]
        out = []
        for n in names:
            cols = [(c[1], c[2]) for c in conn.execute(f"PRAGMA table_info({n})").fetchall()]
            cnt = conn.execute(f"SELECT count(*) FROM {n}").fetchone()[0]
            out.append({"name": n, "rows": cnt, "columns": cols})
        return out
    finally:
        conn.close()


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

    print("\n=== Unit 52 · Database Tool 自检 ===\n")

    # ── 初始化 ──
    if srv.DB_PATH.exists():
        os.remove(srv.DB_PATH)
    srv.init_db()

    print("  —— DB 模型 ——")
    tables = _tables()
    check("2 张表", len(tables) == 2, str([t["name"] for t in tables]))
    names = sorted(t["name"] for t in tables)
    check("表名是 articles/authors", names == ["articles", "authors"], str(names))
    check("authors 4 列", any(t["name"] == "authors" and len(t["columns"]) == 4 for t in tables), str(tables))
    check("articles 6 列", any(t["name"] == "articles" and len(t["columns"]) == 6 for t in tables), str(tables))
    check("authors 5 行", next(t["rows"] for t in tables if t["name"] == "authors") == 5, str(tables))
    check("articles 6 行", next(t["rows"] for t in tables if t["name"] == "articles") == 6, str(tables))

    print("  —— 工具清单 ——")
    async def probe_tools() -> dict[str, Any]:
        async with MCPClient() as c:
            tools = await c.list_tools()
            r1 = await c.call_tool("list_tables", {})
            r2 = await c.call_tool("describe_table", {"table": "authors"})
            r3 = await c.call_tool("run_readonly_sql", {"sql": "SELECT id FROM articles LIMIT 2"})
            return {"tools": tools, "r1": r1.to_dict(), "r2": r2.to_dict(), "r3": r3.to_dict()}
    facts = anyio.run(probe_tools)
    tool_names = sorted(t["name"] for t in facts["tools"])
    check("3 个工具", tool_names == ["describe_table", "list_tables", "run_readonly_sql"], str(tool_names))
    check("每个工具都有描述", all(t["description"] for t in facts["tools"]), str(facts["tools"]))
    rt = next(t for t in facts["tools"] if t["name"] == "run_readonly_sql")
    check("run_readonly_sql 有 sql 参数", "sql" in rt["input_schema"].get("properties", {}), str(rt["input_schema"]))
    check("sql 是必填", "sql" in rt["input_schema"].get("required", []), str(rt["input_schema"].get("required")))
    check("list_tables 返回 2 张表", "articles" in facts["r1"]["text"] and "authors" in facts["r1"]["text"], facts["r1"]["text"])
    check("describe_table 列出列", "id" in facts["r2"]["text"] and "name" in facts["r2"]["text"], facts["r2"]["text"])
    check("正常查询拿到行", "p-101" in facts["r3"]["text"], facts["r3"]["text"])

    print("  —— 正常查询 ——")
    r = srv.run_readonly_sql("SELECT count(*) FROM articles")
    check("count(*) = 6", "6" in r, r)
    r = srv.run_readonly_sql("SELECT a.title, b.name FROM articles a JOIN authors b ON a.author_id = b.id LIMIT 2")
    check("JOIN 返回行", "如何选 MCP 传输" in r, r[:120])
    r = srv.run_readonly_sql("SELECT * FROM articles WHERE word_count > 800")
    check("WHERE 过滤", "1521" in r, r[:200])
    r = srv.run_readonly_sql("SELECT * FROM articles WHERE id = 'p-nonexistent'")
    check("空查询返回 0 行", "查询返回 0 行" == r, r)

    print("  —— 攻击拦截：多语句 ——")
    r = srv.run_readonly_sql("SELECT 1; DROP TABLE articles")
    check("多语句被拦", "多语句" in r, r)
    r = srv.run_readonly_sql("SELECT 1; DELETE FROM authors")
    check("多语句 DELETE 也拦", "多语句" in r, r)
    r = srv.run_readonly_sql("SELECT count(*) FROM articles ; UPDATE authors SET name='x'")
    check("带空格的多语句也拦", "多语句" in r, r)

    print("  —— 攻击拦截：注释 ——")
    r = srv.run_readonly_sql("SELECT * FROM articles -- ; DROP TABLE articles")
    check("行注释不执行 DROP，正常 SELECT 跑完", "如何选 MCP" in r and "DROP" not in r, r[:150])
    r = srv.run_readonly_sql("/* just a comment */ SELECT 1")
    check("块注释不影响查询", "1" in r, r)

    print("  —— 攻击拦截：词法白名单 ——")
    for kw in ["UPDATE", "DELETE", "INSERT", "PRAGMA", "ATTACH", "DROP", "ALTER", "CREATE"]:
        r = srv.run_readonly_sql(f"{kw} something")
        check(f"{kw} 被白名单拦", "只允许 SELECT" in r and kw in r, r[:100])

    print("  —— 强制 LIMIT（直接测内部函数）——")
    auto = srv._enforce_limit("SELECT id FROM authors", default=100, max_limit=1000)
    check("无 LIMIT 自动补 100", "LIMIT 100" in auto, auto)
    capped = srv._enforce_limit("SELECT id FROM authors LIMIT 99999", default=100, max_limit=1000)
    check("LIMIT >1000 改写成 1000", "LIMIT 1000" in capped and "99999" not in capped, capped)
    kept = srv._enforce_limit("SELECT id FROM authors LIMIT 5", default=100, max_limit=1000)
    check("LIMIT ≤ 阈值保持原样", "LIMIT 5" in kept and "100" not in kept, kept)

    print("  —— 教学核心：ro vs rw ——")
    ro_ok, ro_msg = srv._try_write_on_ro()
    check("只读连接拒写 INSERT", ro_ok, ro_msg)
    check("只读连接错误含 'readonly'", "readonly" in ro_msg.lower(), ro_msg)
    rw_ok, rw_msg = srv._try_write_on_rw()
    check("写连接允许写 INSERT", rw_ok, rw_msg)
    check("写连接回滚后行数一致", "6 → 7 → 6" in rw_msg or "一致" in rw_msg, rw_msg)

    print("  —— HTTP 层 ——")
    client = TestClient(app)
    h = client.get("/health").json()
    check("/health 200", client.get("/health").status_code == 200)
    check("health 报出 3 个工具", h["tools"] == ["list_tables", "describe_table", "run_readonly_sql"], str(h["tools"]))
    check("health 报出表", any(t["name"] == "articles" for t in h["tables"]), str(h["tables"]))

    schema = client.get("/api/schema").json()
    check("/api/schema 有 ro 与 rw 对照", "ro" in schema["ro_vs_rw"] and "rw" in schema["ro_vs_rw"], str(schema["ro_vs_rw"]))
    check("/api/schema 的 ro 拒写", schema["ro_vs_rw"]["ro"][0] is True, str(schema["ro_vs_rw"]["ro"]))
    check("/api/schema 的 rw 允许", schema["ro_vs_rw"]["rw"][0] is True, str(schema["ro_vs_rw"]["rw"]))

    attacks = client.get("/api/attacks").json()["samples"]
    check("/api/attacks 有 9 个样例", len(attacks) == 9, str(len(attacks)))
    by_label = {a["label"]: a for a in attacks}
    check("合法 SELECT 跑通", "p-101" in by_label["合法 SELECT"]["result"], by_label["合法 SELECT"]["result"][:80])
    check("多语句被拦", "多语句" in by_label["多语句注入"]["result"], by_label["多语句注入"]["result"])
    check("PRAGMA 被白名单拦", "只允许 SELECT" in by_label["PRAGMA 偷看"]["result"], by_label["PRAGMA 偷看"]["result"])
    check("UPDATE 被白名单拦", "只允许 SELECT" in by_label["UPDATE"]["result"], by_label["UPDATE"]["result"])
    check("ATTACH 被白名单拦", "ATTACH" in by_label["ATTACH"]["result"], by_label["ATTACH"]["result"])

    r = client.post("/api/run", json={"sql": "SELECT id FROM authors LIMIT 2"})
    check("/api/run 跑通", r.status_code == 200 and "a-001" in r.json()["result"], r.text[:120])
    r = client.post("/api/run", json={"sql": "DROP TABLE articles"})
    check("/api/run 拦 DROP", "只允许 SELECT" in r.json()["result"], r.text[:120])

    mcp = client.get("/api/mcp").json()
    check("/api/mcp 起子进程拿到工具清单", len(mcp["tools"]) == 3, str(len(mcp["tools"])))
    check("/api/mcp list_tables 调成功", mcp["calls"][0]["ok"] is True, str(mcp["calls"][0]))
    check("/api/mcp run_readonly_sql 调成功", mcp["calls"][1]["ok"] and "a-001" in mcp["calls"][1]["text"], str(mcp["calls"][1]))

    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200)
    check("页面含关键文案", "Database Tool" in page.text and len(page.text) > 4000, f"{len(page.text)} 字节")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()