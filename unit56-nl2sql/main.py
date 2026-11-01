"""Unit 56 · Schema 注入与方言适配。

同一份库、同一个问题，模型看到的 schema 换个写法，生成的 SQL 就换一个准头。
本文件把这件事变成可测的数字：三种表示的字符数、四种方言的改写结果、JOIN 的正确性。

启动：
    /home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --port 8056
自检：
    /home/rik/.cache/aj/runner/bin/python main.py
"""

from __future__ import annotations

import json
import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import agent as agent_mod
import client
import db
import dialect
import schema as schema_mod

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 56 · NL2SQL 的 schema 与方言", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

Q_DEFAULT = "上月华东区销售额前 3 的客户是谁？"

# 实测字符数，写死当基线：schema.py 一改这里就会红，逼你更新 README 里的数字
FULL_CHARS = 928
COMPACT_CHARS = 465
RELEVANT_CHARS = 382
PROMPT_RELEVANT = 512
PROMPT_FULL = 1058
SYN_ALL_CHARS = 4871
SYN_TWO_CHARS = 191


class AskIn(BaseModel):
    question: str = Field(default=Q_DEFAULT, min_length=1)
    mode: str = Field(default="relevant", pattern="^(full|compact|relevant)$")


def db_schema() -> dict[str, list[dict]]:
    conn = db.rw_connect()
    try:
        return {t: db.describe(conn, t) for t in db.table_names(conn)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    info = client.handshake()
    return {
        "ok": True,
        "server": info["server"],
        "protocol": info["protocol"],
        "tools": [t["name"] for t in info["tools"]],
        "dialects": list(dialect.DIALECTS),
    }


@app.get("/api/schema", summary="单种 schema 表示")
def api_schema(variant: str = "relevant", q: str = Q_DEFAULT) -> dict:
    smap = db_schema()
    text = schema_mod.render_for_prompt(smap, q, mode=variant, ddl=db.DDL)
    stats = schema_mod.char_stats(text)
    stats.update({"variant": variant, "tables": schema_mod.pick_tables(q, sorted(smap)) if variant == "relevant" else sorted(smap)})
    return {"text": text, **stats}


@app.get("/api/schema/compare", summary="三种表示 + 40 表规模下的差距")
def api_schema_compare(q: str = Q_DEFAULT) -> dict:
    smap = db_schema()
    variants = []
    for mode in ("full", "compact", "relevant"):
        text = schema_mod.render_for_prompt(smap, q, mode=mode, ddl=db.DDL)
        variants.append({"mode": mode, **schema_mod.char_stats(text), "text": text})
    syn = schema_mod.synthetic_wide_schema()
    all_text = schema_mod.render_compact(syn)
    two_text = schema_mod.render_compact(syn, ["fact_event_log", "dim_07_entity"])
    return {
        "question": q,
        "variants": variants,
        "ratio_full_to_relevant": round(variants[0]["chars"] / variants[2]["chars"], 2),
        "synthetic": {
            "tables": len(syn),
            "all_chars": len(all_text),
            "two_chars": len(two_text),
            "ratio": round(len(all_text) / len(two_text), 1),
        },
    }


@app.get("/api/ambiguity", summary="哪些列名在多张表里重复")
def api_ambiguity() -> dict:
    smap = agent_mod.SchemaMap(db_schema())
    amb = smap.ambiguous()
    return {"ambiguous": amb, "count": len(amb), "worst": max(amb, key=lambda k: len(amb[k]))}


@app.get("/api/dialect", summary="同一句 SQL 在四种方言下的写法")
def api_dialect(sql: str = "SELECT name FROM products", n: int = 10) -> dict:
    return {
        "items": dialect.all_dialects(sql, n),
        "quotes": {d: dialect.quote_ident(d, "order") for d in dialect.DIALECTS},
        "like": {
            "postgres": dialect.ci_like("postgres", "c.name", "'%张%'"),
            "sqlite": dialect.ci_like("sqlite", "c.name", "'%张%'"),
        },
    }


@app.post("/api/ask", summary="端到端：按指定 schema 表示生成并执行 SQL")
def api_ask(req: AskIn) -> dict:
    runner, smap, calls = agent_mod.build_agent(mode=req.mode)
    out = runner.ask(req.question)
    out["schema_calls"] = calls
    out["ambiguous"] = smap.ambiguous()
    return out


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

    print("Unit 56 · schema 注入与方言适配自检\n")
    db.reset_db()
    smap_raw = db_schema()
    smap = agent_mod.SchemaMap(smap_raw)

    print("  —— 三种 schema 表示：字符数是硬指标 ——")
    full = schema_mod.render_full(db.DDL)
    compact = schema_mod.render_compact(smap_raw)
    relevant = schema_mod.render_relevant(smap_raw, Q_DEFAULT)
    check(f"全量 DDL {FULL_CHARS} 字符", len(full) == FULL_CHARS, str(len(full)))
    check(f"精简表示 {COMPACT_CHARS} 字符", len(compact) == COMPACT_CHARS, str(len(compact)))
    check(f"预筛表示 {RELEVANT_CHARS} 字符", len(relevant) == RELEVANT_CHARS, str(len(relevant)))
    check("三者严格递减 full > compact > relevant",
          len(full) > len(compact) > len(relevant),
          f"{len(full)} / {len(compact)} / {len(relevant)}")
    check("预筛不到全量的一半", len(relevant) / len(full) < 0.5, f"{len(relevant) / len(full):.2f}")
    picked = schema_mod.pick_tables(Q_DEFAULT, sorted(smap_raw))
    check("预筛只给 3 张表", len(picked) == 3, str(picked))
    check("预筛自动补上桥表 orders", "orders" in picked, str(picked))
    check("预筛把无关的 products 排除掉", "products" not in picked, str(picked))
    check("全量 DDL 有 4 条 CREATE TABLE", full.count("CREATE TABLE") == 4, str(full.count("CREATE TABLE")))
    check("精简表示带列注释", "客户所在大区" in compact, compact[:80])

    syn = schema_mod.synthetic_wide_schema()
    syn_all = schema_mod.render_compact(syn)
    syn_two = schema_mod.render_compact(syn, ["fact_event_log", "dim_07_entity"])
    check(f"40 表合成 schema 全量 {SYN_ALL_CHARS} 字符", len(syn_all) == SYN_ALL_CHARS, str(len(syn_all)))
    check(f"只给 2 张表 {SYN_TWO_CHARS} 字符", len(syn_two) == SYN_TWO_CHARS, str(len(syn_two)))
    check("规模越大差距越大（>20 倍）", len(syn_all) / len(syn_two) > 20,
          f"{len(syn_all) / len(syn_two):.1f}x")

    prompt_rel = agent_mod.prompt_for(smap, Q_DEFAULT, "relevant")
    prompt_full = agent_mod.prompt_for(smap, Q_DEFAULT, "full")
    check(f"整段提示词 relevant {PROMPT_RELEVANT} 字符", len(prompt_rel) == PROMPT_RELEVANT,
          str(len(prompt_rel)))
    check(f"整段提示词 full {PROMPT_FULL} 字符", len(prompt_full) == PROMPT_FULL, str(len(prompt_full)))
    check("提示词里也差一倍以上", len(prompt_full) / len(prompt_rel) > 2,
          f"{len(prompt_full) / len(prompt_rel):.2f}x")

    print("\n  —— 方言：同一句「前 10 行」 ——")
    base = "SELECT name FROM products"
    d = {x["dialect"]: x["output"] for x in dialect.all_dialects(base, 10)}
    check("sqlite 用 LIMIT 10", d["sqlite"].endswith("LIMIT 10"), d["sqlite"])
    check("postgres 用 LIMIT 10", d["postgres"].endswith("LIMIT 10"), d["postgres"])
    check("mysql 用 LIMIT 10", d["mysql"].endswith("LIMIT 10"), d["mysql"])
    check("sqlserver 用 TOP 10", d["sqlserver"].startswith("SELECT TOP 10 "), d["sqlserver"])
    check("sqlserver 里不能出现 LIMIT", "LIMIT" not in d["sqlserver"], d["sqlserver"])
    rewritten = dialect.top_n("SELECT * FROM orders LIMIT 5", "sqlite", 10)
    check("已有 LIMIT 5 被改写成 10 且不叠加",
          rewritten.count("LIMIT") == 1 and rewritten.endswith("LIMIT 10"), rewritten)
    check("sqlserver 的 DISTINCT 与 TOP 顺序正确",
          dialect.top_n("SELECT DISTINCT region FROM orders", "sqlserver", 10)
          == "SELECT DISTINCT TOP 10 region FROM orders",
          dialect.top_n("SELECT DISTINCT region FROM orders", "sqlserver", 10))
    check("sqlite 标识符用双引号", dialect.quote_ident("sqlite", "order") == '"order"')
    check("mysql 标识符用反引号", dialect.quote_ident("mysql", "order") == "`order`")
    check("sqlserver 标识符用方括号", dialect.quote_ident("sqlserver", "order") == "[order]")
    check("postgres 用 ILIKE", "ILIKE" in dialect.ci_like("postgres", "c.name", "'%张%'"),
          dialect.ci_like("postgres", "c.name", "'%张%'"))
    check("sqlite 退化成 LOWER(...) LIKE",
          dialect.ci_like("sqlite", "c.name", "'%张%'") == "LOWER(c.name) LIKE LOWER('%张%')",
          dialect.ci_like("sqlite", "c.name", "'%张%'"))
    try:
        dialect.top_n(base, "oracle", 10)
        check("未知方言抛 ValueError", False)
    except ValueError:
        check("未知方言抛 ValueError", True)

    print("\n  —— 列名歧义：不限定就报错 ——")
    amb = smap.ambiguous()
    check("id 在 4 张表里都有", amb.get("id") and len(amb["id"]) == 4, str(amb.get("id")))
    check("name 在 2 张表里重复", sorted(amb.get("name", [])) == ["customers", "products"],
          str(amb.get("name")))
    check("region 在 2 张表里重复", sorted(amb.get("region", [])) == ["customers", "orders"],
          str(amb.get("region")))

    bad_sql = "SELECT id FROM orders o JOIN customers c ON c.id = o.customer_id"
    bad = json.loads(client.call_tool("run_readonly_sql", {"sql": bad_sql})["text"])
    check("不限定列名的 JOIN 直接报错", bad["ok"] is False, str(bad)[:100])
    check("报错是 ambiguous column name", "ambiguous column name" in bad.get("error", ""),
          bad.get("error", ""))

    print("\n  —— 生成 JOIN：跨表要连对 ——")
    runner, _s, calls = agent_mod.build_agent(mode="relevant")
    check("schema 用 5 次 MCP 调用拼出来", calls == 5, str(calls))
    out = runner.ask(Q_DEFAULT)
    sql = out["sql"] or ""
    check("生成的 SQL 以 SELECT 开头", sql.lstrip().upper().startswith("SELECT"), sql[:30])
    check("SQL 里没有裸 SELECT id", "SELECT id" not in sql, sql[:60])
    check("JOIN 条件用 c.id = o.customer_id", "ON c.id = o.customer_id" in sql, sql)
    check("SQL 里有两个 JOIN", sql.count("JOIN") == 2, str(sql.count("JOIN")))
    check("执行不报歧义错", out["answer"] and "ambiguous" not in out["answer"], out["answer"][:80])
    check("返回 3 行", len(out["rows"] or []) == 3, str(out["rows"]))
    top = out["rows"] or []
    check("第 1 名王强 6742 元", top and top[0] == {"customer": "王强", "revenue": 6742}, str(top[:1]))
    check("第 2 名李娜 6664 元", len(top) > 1 and top[1]["revenue"] == 6664, str(top[1:2]))
    check("第 3 名张伟 5514 元", len(top) > 2 and top[2]["revenue"] == 5514, str(top[2:3]))
    check("答案里带数字", "6742" in out["answer"], out["answer"][:80])

    out_bad = runner.ask("把这些数据按季度做成 PPT 发我邮箱")
    check("无法映射的问题返回「无法回答」", "无法回答" in out_bad["answer"], out_bad["answer"][:60])
    check("并且没有生成 SQL", out_bad["sql"] is None, str(out_bad["sql"]))

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    c = TestClient(app)
    r = c.get("/health")
    check("GET /health 200", r.status_code == 200 and r.json()["server"] == "nl2sql-mcp", r.text[:100])

    r = c.get("/api/schema/compare")
    body = r.json()
    check("/api/schema/compare 返回三档", [v["mode"] for v in body["variants"]] ==
          ["full", "compact", "relevant"], str([v["mode"] for v in body["variants"]]))
    check("compare 里的字符数与常量一致", body["variants"][0]["chars"] == FULL_CHARS, str(body["variants"][0]["chars"]))
    check("compare 带 40 表规模数据", body["synthetic"]["tables"] == 41, str(body["synthetic"]))

    r = c.get("/api/dialect", params={"sql": "SELECT id FROM orders", "n": 10})
    check("/api/dialect 返回 4 种方言", len(r.json()["items"]) == 4, r.text[:100])
    check("/api/dialect 里 sqlserver 是 TOP",
          r.json()["items"][3]["output"].startswith("SELECT TOP 10 "), r.json()["items"][3]["output"])

    r = c.post("/api/ask", json={"question": Q_DEFAULT, "mode": "relevant"})
    check("POST /api/ask 端到端跑通", r.status_code == 200 and "6742" in r.json()["answer"], r.text[:120])
    check("/api/ask 回传 prompt 字符数", r.json()["prompt_chars"] == PROMPT_RELEVANT, str(r.json()["prompt_chars"]))

    r = c.post("/api/ask", json={"question": Q_DEFAULT, "mode": "full"})
    check("mode=full 时 prompt 更长", r.json()["prompt_chars"] == PROMPT_FULL, str(r.json()["prompt_chars"]))

    r = c.get("/")
    check("GET / 返回页面", r.status_code == 200 and "方言" in r.text, str(r.status_code))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
