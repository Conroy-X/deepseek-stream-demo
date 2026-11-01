"""Unit 57 · SQL 安全实验台（端口 8057）。"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

import security as sec

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Unit 57 · SQL 安全")


@app.on_event("startup")
def _startup() -> None:
    sec.init_db()


class RunIn(BaseModel):
    sql: str
    tenant: str = "t-acme"


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "unit": 57, "gates": ["gate1_readonly", "gate2_lexical", "gate3_limit", "gate4_row_level"]}


@app.get("/api/probe")
def api_probe() -> dict[str, Any]:
    return sec.probe_all("t-acme")


@app.get("/api/cross-tenant")
def api_cross() -> dict[str, Any]:
    return sec.cross_tenant_test()


@app.post("/api/run")
def api_run(payload: RunIn) -> dict[str, Any]:
    return sec.run_safe(payload.sql, payload.tenant)


@app.get("/api/gate1")
def api_gate1() -> dict[str, Any]:
    ok, msg = sec.gate1_readonly_conn()
    return {"blocked": ok, "message": msg}


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

    print("\n=== Unit 57 · SQL 安全 自检 ===\n")
    sec.init_db()

    print("  —— 闸 1：只读连接 ——")
    ok, msg = sec.gate1_readonly_conn()
    check("只读连接拒写", ok, msg)
    check("错误含 readonly", "readonly" in msg.lower(), msg)

    print("  —— 闸 2：词法 + 多语句 + 注释 ——")
    g = sec.gate2_lexical("SELECT * FROM orders")
    check("合法 SELECT 通过", not g.blocked, str(g))
    g = sec.gate2_lexical("UPDATE orders SET amount=0")
    check("UPDATE 被拦", g.blocked and "UPDATE" in g.reason, str(g))
    g = sec.gate2_lexical("SELECT 1; DROP TABLE orders")
    check("多语句被拦", g.blocked and "多语句" in g.reason, str(g))
    g = sec.gate2_lexical("SELECT * FROM orders -- ; DROP TABLE orders")
    check("行注释后正常 SELECT（不被拦）", not g.blocked, str(g))
    check("行注释 SQL 不含 DROP", "DROP" not in (g.final_sql or ""), str(g.final_sql))
    g = sec.gate2_lexical("/* x */ SELECT 1")
    check("块注释后正常 SELECT", not g.blocked, str(g))
    g = sec.gate2_lexical("PRAGMA database_list")
    check("PRAGMA 被拦", g.blocked and "PRAGMA" in g.reason, str(g))
    g = sec.gate2_lexical("ATTACH DATABASE 'x' AS y")
    check("ATTACH 被拦", g.blocked and "ATTACH" in g.reason, str(g))

    print("  —— 闸 3：强制 LIMIT ——")
    s = sec.gate3_enforce_limit("SELECT * FROM orders")
    check("无 LIMIT 自动补 100", "LIMIT 100" in s, s)
    s = sec.gate3_enforce_limit("SELECT * FROM orders LIMIT 99999")
    check("LIMIT >1000 改写成 1000", "LIMIT 1000" in s and "99999" not in s, s)
    s = sec.gate3_enforce_limit("SELECT * FROM orders LIMIT 5")
    check("LIMIT ≤ 阈值保持原样", "LIMIT 5" in s, s)

    print("  —— 闸 4：行级权限 ——")
    new_sql, tenant = sec.gate4_row_level("SELECT * FROM orders", "t-acme")
    check("无 WHERE 时加 WHERE tenant", "WHERE tenant_id = 't-acme'" in new_sql, new_sql)
    new_sql, _ = sec.gate4_row_level("SELECT * FROM orders WHERE status = 'shipped'", "t-globex")
    check("有 WHERE 时追加 AND", "tenant_id = 't-globex' AND" in new_sql, new_sql)
    new_sql, _ = sec.gate4_row_level("SELECT * FROM orders LIMIT 5", "t-acme")
    check("有 LIMIT 时 WHERE 在 LIMIT 前", new_sql.upper().find("WHERE") < new_sql.upper().find("LIMIT"), new_sql)

    print("  —— 跨租户隔离 ——")
    cross = sec.cross_tenant_test()
    check("A 租户只看到自己的订单", all(r["tenant_id"] == "t-acme" for r in cross["acme_rows"]), str(cross["acme_rows"]))
    check("G 租户只看到自己的订单", all(r["tenant_id"] == "t-globex" for r in cross["globex_rows"]), str(cross["globex_rows"]))
    check("A 看不到 G 的订单", cross["acme_sees_globex"] is False, str(cross["acme_sees_globex"]))
    check("A 看到 2 笔订单（t-acme 有 2 条）", len(cross["acme_rows"]) == 2, str(len(cross["acme_rows"])))
    check("G 看到 2 笔订单（t-globex 有 2 条）", len(cross["globex_rows"]) == 2, str(len(cross["globex_rows"])))

    print("  —— OWASP 8 条攻击 ——")
    probe = sec.probe_all("t-acme")
    # 6 条是真攻击（多语句/PRAGMA/ATTACH/UNION）—— 应该被拦
    real_attacks = [a for a in probe["attacks"] if "注释" not in a["label"]]
    comment_attacks = [a for a in probe["attacks"] if "注释" in a["label"]]
    blocked_count = sum(1 for a in real_attacks if a["result"]["blocked_by"])
    check(f"{len(real_attacks)} 条真攻击全被拦", blocked_count == len(real_attacks), f"实际 {blocked_count}")
    for a in real_attacks:
        check(f"攻击「{a['label']}」被拦", a["result"]["blocked_by"] is not None, str(a["result"]))

    # 2 条注释样例：剥注释后是合法 SELECT，DROP 没执行 —— 这是「防御成功」不是「没被拦」
    # 验证：orders 表还在（没被 DROP）
    conn = sqlite3.connect(str(sec.DB_PATH))
    try:
        cnt = conn.execute("SELECT count(*) FROM orders").fetchone()[0]
    finally:
        conn.close()
    check("注释样例跑完后 orders 表还在", cnt == 6, f"实际 {cnt}")
    for a in comment_attacks:
        check(f"注释「{a['label']}」剥注释后跑通", a["result"]["blocked_by"] is None and a["result"]["rows"], str(a["result"])[:120])

    print("  —— 3 条合法复杂 SQL 不误杀 ——")
    for l in probe["legit"]:
        check(f"合法「{l['label']}」不被拦", l["result"]["blocked_by"] is None, str(l["result"]))
    # 合法 SQL 应该返回行
    join_legit = next(l for l in probe["legit"] if l["label"] == "带 JOIN")
    check("合法 JOIN 返回行", len(join_legit["result"]["rows"]) > 0, str(join_legit["result"]["rows"]))
    where_legit = next(l for l in probe["legit"] if l["label"] == "带 WHERE")
    check("合法 WHERE 返回行", len(where_legit["result"]["rows"]) > 0, str(where_legit["result"]["rows"]))
    agg_legit = next(l for l in probe["legit"] if l["label"] == "带聚合")
    check("合法聚合返回行", len(agg_legit["result"]["rows"]) > 0, str(agg_legit["result"]["rows"]))

    print("  —— HTTP 层 ——")
    client = TestClient(app)
    check("/health 200", client.get("/health").status_code == 200)
    check("/api/probe 200", client.get("/api/probe").status_code == 200)
    cross_resp = client.get("/api/cross-tenant").json()
    check("/api/cross-tenant 报出隔离", cross_resp["acme_sees_globex"] is False, str(cross_resp))
    r = client.post("/api/run", json={"sql": "SELECT * FROM orders", "tenant": "t-acme"})
    check("/api/run 跑通", r.status_code == 200 and r.json()["blocked_by"] is None, r.text[:120])
    check("/api/run 自动注入 tenant", "t-acme" in r.json()["final_sql"], r.json()["final_sql"])
    r = client.post("/api/run", json={"sql": "DROP TABLE orders", "tenant": "t-acme"})
    check("/api/run 拦 DROP", r.json()["blocked_by"] == "gate2", r.text[:120])
    r = client.get("/api/gate1")
    check("/api/gate1 拒写", r.json()["blocked"] is True, r.text[:120])

    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200, str(page.status_code))
    check("页面含关键文案", "SQL 安全" in page.text and len(page.text) > 4000, f"{len(page.text)} 字节")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()