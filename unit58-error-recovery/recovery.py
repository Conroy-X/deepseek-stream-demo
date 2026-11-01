"""Unit 58 · Error Recovery：SQL 报错喂回模型自动修正，最多 3 次。

核心：**SQL 报错是资产不是故障**。
三类典型错误 + 一类 MCP 工具层错误，每类都有对应的修正策略。

  1. no such column: X    → 列名拼错，换成真实列名
  2. no such table: Y     → 表名拼错，换成真实表名
  3. near "...": syntax error → 语法错，修语法
  4. MCP 工具异常被脱敏   → Error executing tool xxx，模型看不到原因

最多 3 次重试。每次的错误历史都保留（不覆盖）。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "recovery.db"

REAL_TABLES = {"authors", "articles"}
REAL_COLUMNS = {
    "authors": {"id", "name", "city", "dept"},
    "articles": {"id", "title", "category", "author_id", "publish_date", "word_count"},
}

AUTHORS = [
    ("a-001", "王强", "上海", "tech"),
    ("a-002", "李娜", "杭州", "design"),
]
ARTICLES = [
    ("p-101", "MCP 入门", "tech", "a-001", "2026-10-12", 842),
    ("p-102", "RAG 实战", "tech", "a-001", "2026-10-18", 1521),
]


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(
            """
            CREATE TABLE authors (id TEXT PRIMARY KEY, name TEXT, city TEXT, dept TEXT);
            CREATE TABLE articles (id TEXT PRIMARY KEY, title TEXT, category TEXT,
                                   author_id TEXT, publish_date TEXT, word_count INTEGER);
            """
        )
        conn.executemany("INSERT INTO authors VALUES (?,?,?,?)", AUTHORS)
        conn.executemany("INSERT INTO articles VALUES (?,?,?,?,?,?)", ARTICLES)
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────
# 错误归一化：把 SQLite 原始错误 / MCP 脱敏错误 转成可行动的中文
# ─────────────────────────────────────────────────────────────────────────


def normalize_error(raw: str) -> dict[str, Any]:
    """把原始错误文本归一化。

    SQLite 错误含实现细节（pydantic 风格的 type=...）；
    MCP 工具异常被脱敏成 `Error executing tool xxx`。
    归一化后只留「字段名 + 期望类型 + 你给了什么」。
    """
    # SQLite no such column
    m = re.search(r"no such column: (\S+)", raw, re.IGNORECASE)
    if m:
        bad = m.group(1).strip("`\"'")
        return {"kind": "no_such_column", "field": bad, "hint": f"列名 {bad} 不存在"}

    # SQLite no such table
    m = re.search(r"no such table: (\S+)", raw, re.IGNORECASE)
    if m:
        bad = m.group(1).strip("`\"'")
        return {"kind": "no_such_table", "field": bad, "hint": f"表名 {bad} 不存在"}

    # SQLite syntax error
    if "syntax error" in raw.lower() or "near" in raw.lower():
        return {"kind": "syntax_error", "field": None, "hint": "SQL 语法有错，检查关键字顺序"}

    # MCP 脱敏错误
    m = re.match(r"Error executing tool (\w+)", raw)
    if m:
        return {"kind": "tool_exception", "field": m.group(1), "hint": f"工具 {m.group(1)} 内部异常，原因被脱敏"}

    # pydantic 校验泄露
    if "addArguments" in raw or "errors.pydantic.dev" in raw:
        return {"kind": "param_validation", "field": None, "hint": "参数类型不对（含实现细节，已归一化）"}

    return {"kind": "unknown", "field": None, "hint": raw[:100]}


# ─────────────────────────────────────────────────────────────────────────
# 离线模型：根据错误类型改写 SQL
# ─────────────────────────────────────────────────────────────────────────


def fix_sql(sql: str, error: dict[str, Any]) -> str:
    """根据归一化后的错误，改写 SQL。"""
    kind = error["kind"]
    if kind == "no_such_column":
        bad = error["field"]
        # 用前 4 字符做模糊匹配（authr → author_id，titl → title）
        prefix = bad.lower()[:4]
        for table, cols in REAL_COLUMNS.items():
            for c in cols:
                if c.lower().startswith(prefix):
                    return sql.replace(bad, c)
        return sql  # 找不到就原样返回（下一轮会再报错）
    if kind == "no_such_table":
        bad = error["field"]
        prefix = bad.lower()[:4]
        for t in REAL_TABLES:
            if t.lower().startswith(prefix):
                return sql.replace(bad, t)
        return sql
    if kind == "syntax_error":
        fixed = re.sub(r",\s*,", ",", sql)
        fixed = re.sub(r"FROM\s+FROM", "FROM", fixed)
        fixed = re.sub(r"FROMFROM", "FROM", fixed)
        fixed = re.sub(r"SELECT\s+FROM\b", "SELECT * FROM", fixed)
        return fixed
    return sql


# ─────────────────────────────────────────────────────────────────────────
# Recovery Loop
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class Attempt:
    round: int
    sql: str
    error_raw: str | None
    error_normalized: dict[str, Any] | None
    fixed: bool


@dataclass
class RecoveryResult:
    success: bool
    attempts: list[Attempt]
    final_sql: str | None
    rows: list[dict[str, Any]]
    rounds: int


def run_with_recovery(initial_sql: str, max_rounds: int = 3) -> RecoveryResult:
    """跑 SQL，错了就归一化、改写、重试，最多 max_rounds 次。"""
    attempts: list[Attempt] = []
    current_sql = initial_sql
    rows: list[dict[str, Any]] = []

    for r in range(1, max_rounds + 1):
        ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            cur = ro.execute(current_sql)
            fetched = cur.fetchmany(50)
            cols = [c[0] for c in cur.description] if cur.description else []
            rows = [dict(zip(cols, row)) for row in fetched]
            attempts.append(Attempt(r, current_sql, None, None, True))
            return RecoveryResult(True, attempts, current_sql, rows, r)
        except sqlite3.OperationalError as e:
            raw = str(e)
            normalized = normalize_error(raw)
            attempts.append(Attempt(r, current_sql, raw, normalized, False))
            # 改写下一轮的 SQL。即使改不动也继续跑 —— 让 max_rounds 兜底，
            # 这样「修不好」的 case 会跑满 3 轮而不是提前退出
            new_sql = fix_sql(current_sql, normalized)
            current_sql = new_sql
        finally:
            ro.close()

    return RecoveryResult(False, attempts, current_sql, [], max_rounds)


# ─────────────────────────────────────────────────────────────────────────
# 修不好的错误（演示 3 次后停手）
# ─────────────────────────────────────────────────────────────────────────


def unfixable_case() -> RecoveryResult:
    """故意给一个永远修不好的错误。"""
    # SELECT FROM FROM 是语法错，fix_sql 会改 FROMFROM → FROM，但 SELECT FROM 还在
    # 实际上 fix_sql 会把 SELECT FROM 改成 SELECT * FROM —— 这会修好
    # 改用一个真的修不好的：SELECT nonexistent_from_completely_unknown_table
    return run_with_recovery("SELECT badcol123 FROM nonexistent_table_xyz", max_rounds=3)