"""Unit 58 · Error Recovery 实验台（端口 8058）。"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

import recovery as rec

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Unit 58 · Error Recovery")


@app.on_event("startup")
def _startup() -> None:
    rec.init_db()


class RunIn(BaseModel):
    sql: str
    max_rounds: int = 3


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "unit": 58, "max_rounds": 3}


@app.post("/api/run")
def api_run(payload: RunIn) -> dict[str, Any]:
    r = rec.run_with_recovery(payload.sql, payload.max_rounds)
    return {
        "success": r.success,
        "rounds": r.rounds,
        "attempts": [a.__dict__ for a in r.attempts],
        "final_sql": r.final_sql,
        "rows": r.rows,
    }


@app.get("/api/cases")
def api_cases() -> dict[str, Any]:
    """四类典型错误的预置样例。"""
    cases = [
        {"label": "列名拼错（authr → author_id）", "sql": "SELECT authr FROM articles LIMIT 1"},
        {"label": "表名拼错（article → articles）", "sql": "SELECT * FROM article LIMIT 1"},
        {"label": "语法错（SELECT FROM FROM）", "sql": "SELECT FROM FROM articles"},
        {"label": "修不好（badcol + badtable）", "sql": "SELECT badcol123 FROM nonexistent_table_xyz"},
    ]
    out = []
    for c in cases:
        r = rec.run_with_recovery(c["sql"])
        out.append({
            "label": c["label"],
            "sql": c["sql"],
            "success": r.success,
            "rounds": r.rounds,
            "attempts": [a.__dict__ for a in r.attempts],
            "final_sql": r.final_sql,
        })
    return {"cases": out}


@app.get("/api/normalize")
def api_normalize(text: str) -> dict[str, Any]:
    return rec.normalize_error(text)


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

    print("\n=== Unit 58 · Error Recovery 自检 ===\n")
    rec.init_db()

    print("  —— 错误归一化 ——")
    n = rec.normalize_error("no such column: authr")
    check("no such column 归一化", n["kind"] == "no_such_column" and n["field"] == "authr", str(n))
    n = rec.normalize_error("no such table: article")
    check("no such table 归一化", n["kind"] == "no_such_table" and n["field"] == "article", str(n))
    n = rec.normalize_error('near "FROM": syntax error')
    check("syntax error 归一化", n["kind"] == "syntax_error", str(n))
    n = rec.normalize_error("Error executing tool run_sql")
    check("MCP 脱敏错误归一化", n["kind"] == "tool_exception" and n["field"] == "run_sql", str(n))
    n = rec.normalize_error("1 validation error for addArguments\na\n  Input should be a valid number [type=float_parsing, ...] https://errors.pydantic.dev/...")
    check("pydantic 泄露归一化", n["kind"] == "param_validation", str(n))
    n = rec.normalize_error("some random error")
    check("未知错误兜底", n["kind"] == "unknown", str(n))

    print("  —— 归一化不含实现细节 ——")
    # 归一化后的 hint 不该含 addArguments / errors.pydantic.dev
    for raw in [
        "1 validation error for addArguments ... errors.pydantic.dev/...",
        "Error executing tool xxx",
        "no such column: bad",
    ]:
        n = rec.normalize_error(raw)
        hint = n["hint"]
        check(f"归一化「{raw[:30]}」不含 addArguments", "addArguments" not in hint, hint)
        check(f"归一化「{raw[:30]}」不含 pydantic URL", "errors.pydantic.dev" not in hint, hint)

    print("  —— 列名拼错，1 轮修好 ——")
    r = rec.run_with_recovery("SELECT authr FROM articles LIMIT 1")
    check("修好了", r.success, str(r))
    check("1 轮修好", r.rounds == 2, str(r.rounds))  # 第 1 轮报错、第 2 轮修好
    check("修后的 SQL 含 author_id", "author_id" in (r.final_sql or ""), str(r.final_sql))
    check("返回行有数据", len(r.rows) > 0, str(r.rows))
    check("错误历史被保留", len(r.attempts) == 2, str(len(r.attempts)))
    check("第 1 轮记录了错误", r.attempts[0].error_normalized is not None, str(r.attempts[0]))
    check("第 2 轮无错误", r.attempts[1].error_raw is None, str(r.attempts[1]))

    print("  —— 表名拼错，1 轮修好 ——")
    r = rec.run_with_recovery("SELECT * FROM article LIMIT 1")
    check("表名修好了", r.success, str(r))
    check("修后含 articles", "articles" in (r.final_sql or ""), str(r.final_sql))
    check("返回行", len(r.rows) > 0, str(r.rows))

    print("  —— 语法错，1 轮修好 ——")
    r = rec.run_with_recovery("SELECT FROM FROM articles")
    check("语法错修好了", r.success, str(r))
    check("修后含 SELECT *", "SELECT *" in (r.final_sql or ""), str(r.final_sql))

    print("  —— 修不好的，3 次后停手 ——")
    r = rec.run_with_recovery("SELECT badcol123 FROM nonexistent_table_xyz", max_rounds=3)
    check("修不好返回失败", not r.success, str(r))
    check("跑了 3 轮", r.rounds == 3, str(r.rounds))
    check("不无限循环", r.rounds <= 3, str(r.rounds))
    check("错误历史有 3 条", len(r.attempts) == 3, str(len(r.attempts)))
    check("每轮错误没被覆盖", len(set(a.error_raw for a in r.attempts if a.error_raw)) > 0, str(r.attempts))

    print("  —— 重试时没把错误当成功 ——")
    r = rec.run_with_recovery("SELECT authr FROM articles LIMIT 1")
    for a in r.attempts:
        if a.error_raw:
            check("有错误的轮次 fixed=False", a.fixed is False, str(a))
        else:
            check("没错误的轮次 fixed=True", a.fixed is True, str(a))

    print("  —— HTTP 层 ——")
    client = TestClient(app)
    check("/health 200", client.get("/health").status_code == 200)
    cases = client.get("/api/cases").json()["cases"]
    check("/api/cases 4 个样例", len(cases) == 4, str(len(cases)))
    by_label = {c["label"]: c for c in cases}
    check("列名拼错修好", by_label["列名拼错（authr → author_id）"]["success"], str(by_label))
    check("表名拼错修好", by_label["表名拼错（article → articles）"]["success"], str(by_label))
    check("语法错修好", by_label["语法错（SELECT FROM FROM）"]["success"], str(by_label))
    check("修不好返回失败", by_label["修不好（badcol + badtable）"]["success"] is False, str(by_label))

    r = client.post("/api/run", json={"sql": "SELECT authr FROM articles LIMIT 1"})
    check("/api/run 修好", r.json()["success"], r.text[:120])
    r = client.post("/api/run", json={"sql": "SELECT badcol FROM badtable", "max_rounds": 3})
    check("/api/run 修不好返回失败", r.json()["success"] is False, r.text[:120])
    check("/api/run 跑了 3 轮", r.json()["rounds"] == 3, str(r.json()["rounds"]))

    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200, str(page.status_code))
    check("页面含关键文案", "Error Recovery" in page.text and len(page.text) > 4000, f"{len(page.text)} 字节")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()