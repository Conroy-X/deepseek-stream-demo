"""Unit 57 · SQL 安全：四道闸。

unit52 讲了「只读连接 vs 词法检查」，本单元把这条扩成四道闸：

  1. 只读账号        sqlite3.connect("file:...?mode=ro", uri=True)
  2. 语句白名单      剥注释 + 拒多语句 + 只允许 SELECT
  3. 强制 LIMIT      无 LIMIT 自动补 100；> 1000 改写成 1000
  4. 行级权限        自动注入 `WHERE tenant_id = ?`，A 租户查不到 B 租户

并附 OWASP SQL 注入 8 条攻击样例 + 3 条合法复杂 SQL（不能误杀）。

启动：
    /home/rik/.cache/aj/runner/bin/python main.py
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
DB_PATH = DATA_DIR / "multitenant.db"


# ─────────────────────────────────────────────────────────────────────────
# 多租户数据：3 个租户、4 笔订单
# ─────────────────────────────────────────────────────────────────────────

TENANTS = [
    ("t-acme", "Acme 公司"),
    ("t-globex", "Globex 集团"),
    ("t-umbrella", "Umbrella 实验室"),
]

ORDERS = [
    ("o-001", "t-acme", 998, "shipped"),
    ("o-002", "t-acme", 499, "pending"),
    ("o-003", "t-globex", 1899, "shipped"),
    ("o-004", "t-globex", 259, "cancelled"),
    ("o-005", "t-umbrella", 519, "shipped"),
    ("o-006", "t-umbrella", 1299, "pending"),
]


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(
            """
            CREATE TABLE tenants (
                id      TEXT PRIMARY KEY,
                name    TEXT NOT NULL
            );
            CREATE TABLE orders (
                order_id    TEXT PRIMARY KEY,
                tenant_id   TEXT NOT NULL,
                amount      INTEGER NOT NULL,
                status      TEXT NOT NULL
            );
            CREATE INDEX idx_orders_tenant ON orders(tenant_id);
            CREATE INDEX idx_orders_status ON orders(status);
            """
        )
        conn.executemany("INSERT INTO tenants VALUES (?,?)", TENANTS)
        conn.executemany("INSERT INTO orders VALUES (?,?,?,?)", ORDERS)
        conn.commit()
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────
# 四道闸
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class GateResult:
    gate: str          # 哪一道闸拦下
    blocked: bool      # 是否被拦
    reason: str
    final_sql: str | None


def gate1_readonly_conn() -> tuple[bool, str]:
    """闸 1：只读连接。词法没拦到的写操作到这里炸。"""
    ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        ro.execute("INSERT INTO orders VALUES ('x','t-acme',1,'pending')")
        return False, "INSERT 在只读连接上成功 —— 这不该发生"
    except sqlite3.OperationalError as e:
        return True, str(e)
    finally:
        ro.close()


def gate2_lexical(sql: str) -> GateResult:
    """闸 2：词法白名单 + 多语句 + 注释。"""
    stripped = strip_comments(sql).strip()
    if not stripped:
        return GateResult("gate2", True, "去掉注释后 SQL 为空", None)
    body = stripped.rstrip(";").strip()
    if ";" in body:
        return GateResult("gate2", True, "检测到多语句", None)
    head = body.split(None, 1)[0].upper()
    if head != "SELECT":
        return GateResult("gate2", True, f"只允许 SELECT，检测到 {head}", None)
    return GateResult("gate2", False, "通过词法", body)


def gate3_enforce_limit(sql: str, default: int = 100, max_limit: int = 1000) -> str:
    """闸 3：强制 LIMIT。"""
    m = re.search(r"\bLIMIT\s+(\d+)", sql, re.IGNORECASE)
    if not m:
        return f"{sql} LIMIT {default}"
    n = int(m.group(1))
    if n > max_limit:
        return re.sub(r"\bLIMIT\s+\d+", f"LIMIT {max_limit}", sql, count=1, flags=re.IGNORECASE)
    return sql


def gate4_row_level(sql: str, tenant_id: str) -> tuple[str, str]:
    """闸 4：行级权限。自动注入 WHERE tenant_id = ?

    简化策略：
      - 如果 SQL 已有 WHERE，追加 AND tenant_id = ?
      - 如果没有 WHERE，加 WHERE tenant_id = ?

    真实生产会用 RLS（PostgreSQL row-level security）或视图，本单元用改写演示原理。
    """
    upper = sql.upper()
    where_pos = upper.find("WHERE")
    if where_pos >= 0:
        # 在 WHERE 后追加 AND
        new = sql[: where_pos + 5] + f" tenant_id = '{tenant_id}' AND " + sql[where_pos + 5:]
    else:
        # 没有 WHERE，在 LIMIT 前 / 末尾加 WHERE
        limit_pos = upper.find("LIMIT")
        if limit_pos >= 0:
            new = sql[:limit_pos].rstrip() + f" WHERE tenant_id = '{tenant_id}' " + sql[limit_pos:]
        else:
            new = sql.rstrip() + f" WHERE tenant_id = '{tenant_id}'"
    return new, tenant_id


def strip_comments(sql: str) -> str:
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    s = re.sub(r"--[^\n]*", " ", s)
    return s


def run_safe(sql: str, tenant_id: str) -> dict[str, Any]:
    """跑完四道闸，返回每道闸的结果 + 最终执行结果。"""
    g2 = gate2_lexical(sql)
    if g2.blocked:
        return {"blocked_by": "gate2", "reason": g2.reason, "final_sql": None, "rows": []}

    g3_sql = gate3_enforce_limit(g2.final_sql)
    g4_sql, applied_tenant = gate4_row_level(g3_sql, tenant_id)

    ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        cur = ro.execute(g4_sql)
        rows = cur.fetchmany(50)
        cols = [c[0] for c in cur.description] if cur.description else []
        return {
            "blocked_by": None,
            "gates": {"gate2": "pass", "gate3": "pass", "gate4": f"applied tenant={applied_tenant}"},
            "final_sql": g4_sql,
            "rows": [dict(zip(cols, r)) for r in rows],
        }
    except sqlite3.OperationalError as e:
        return {"blocked_by": "gate1", "reason": f"驱动层拒：{e}", "final_sql": g4_sql, "rows": []}
    finally:
        ro.close()


# ─────────────────────────────────────────────────────────────────────────
# OWASP 攻击样例 + 合法对照
# ─────────────────────────────────────────────────────────────────────────


ATTACKS: list[tuple[str, str]] = [
    ("多语句 DROP", "SELECT * FROM orders; DROP TABLE orders"),
    ("多语句 DELETE", "SELECT * FROM orders; DELETE FROM orders"),
    ("多语句 UPDATE", "SELECT * FROM orders WHERE 1=1; UPDATE orders SET amount=0"),
    ("行注释偷渡", "SELECT * FROM orders -- ; DROP TABLE orders"),
    ("块注释偷渡", "SELECT * FROM /* ; DROP TABLE orders */ orders"),
    ("PRAGMA 摸底", "PRAGMA database_list"),
    ("ATTACH 挂载", "ATTACH DATABASE '/tmp/x.db' AS x"),
    ("UNION 拉数据", "SELECT tenant_id FROM orders UNION SELECT name FROM tenants"),
]

LEGIT: list[tuple[str, str, str]] = [
    ("带 JOIN", "SELECT o.order_id, t.name FROM orders o JOIN tenants t ON o.tenant_id = t.id LIMIT 3", "t-acme"),
    ("带 WHERE", "SELECT * FROM orders WHERE status = 'shipped'", "t-acme"),
    ("带聚合", "SELECT count(*), sum(amount) FROM orders", "t-acme"),
]


def probe_all(tenant: str = "t-acme") -> dict[str, Any]:
    """跑所有攻击 + 所有合法，给页面用。"""
    out_attacks = []
    for label, sql in ATTACKS:
        r = run_safe(sql, tenant)
        out_attacks.append({"label": label, "sql": sql, "result": r})

    out_legit = []
    for label, sql, t in LEGIT:
        r = run_safe(sql, t)
        out_legit.append({"label": label, "sql": sql, "tenant": t, "result": r})

    return {"attacks": out_attacks, "legit": out_legit, "tenant": tenant}


def cross_tenant_test() -> dict[str, Any]:
    """A 租户查不到 B 租户的订单。"""
    a = run_safe("SELECT * FROM orders", "t-acme")
    g = run_safe("SELECT * FROM orders", "t-globex")
    return {
        "acme_rows": a.get("rows", []),
        "globex_rows": g.get("rows", []),
        "acme_only": [r for r in a.get("rows", []) if r.get("tenant_id") == "t-acme"],
        "globex_only": [r for r in g.get("rows", []) if r.get("tenant_id") == "t-globex"],
        "acme_sees_globex": any(r.get("tenant_id") == "t-globex" for r in a.get("rows", [])),
    }