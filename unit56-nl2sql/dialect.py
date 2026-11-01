"""方言适配：同一句「取前 10 行」，四种数据库四种写法。

| 方言 | 前 N 行 | 标识符引用 | 大小写不敏感匹配 |
| --- | --- | --- | --- |
| sqlite | `LIMIT 10` | `"x"` | `LIKE`（默认不区分 ASCII 大小写） |
| postgres | `LIMIT 10` | `"x"` | `ILIKE` |
| mysql | `LIMIT 10` | `` `x` `` | 取决于 collation |
| sqlserver | `SELECT TOP 10` | `[x]` | 取决于 collation |

这一层不能省：模型吐出来的十有八九是「SQLite 风味」的 SQL，
直接搬到 SQL Server 上 `LIMIT` 是**语法错误**，不是「慢一点」。
"""

from __future__ import annotations

import re

DIALECTS = ("sqlite", "postgres", "mysql", "sqlserver")

QUOTES = {
    "sqlite": ('"', '"'),
    "postgres": ('"', '"'),
    "mysql": ("`", "`"),
    "sqlserver": ("[", "]"),
}

LIMIT_RE = re.compile(r"\s+LIMIT\s+(\d+)(\s+OFFSET\s+\d+)?\s*;?\s*$", re.IGNORECASE)
TOP_RE = re.compile(r"\bSELECT\s+TOP\s+\d+\s+", re.IGNORECASE)


def quote_ident(dialect: str, name: str) -> str:
    """引用一个可能撞关键字或含大写的标识符（比如 `order`、`Order Date`）。"""
    if dialect not in QUOTES:
        raise ValueError(f"未知方言 {dialect!r}，支持 {DIALECTS}")
    left, right = QUOTES[dialect]
    escaped = name.replace(right, right + right) if dialect != "sqlserver" else name.replace("]", "]]")
    return f"{left}{escaped}{right}"


def top_n(sql: str, dialect: str, n: int = 10) -> str:
    """把任意写法的「取前 n 行」改写成目标方言。

    做法：先剥掉原 SQL 尾部的 LIMIT，再按方言补回去。
    这样 `SELECT ... LIMIT 5` + n=10 也能正确变成 10，而不是叠加两个 LIMIT。
    """
    if dialect not in DIALECTS:
        raise ValueError(f"未知方言 {dialect!r}，支持 {DIALECTS}")
    body = TOP_RE.sub("SELECT ", sql.strip())
    body = LIMIT_RE.sub("", body).strip().rstrip(";")

    if dialect == "sqlserver":
        # TOP 必须紧跟在 SELECT 后面（DISTINCT 之后）
        if re.match(r"^SELECT\s+DISTINCT\s+", body, re.IGNORECASE):
            return re.sub(r"^(SELECT\s+DISTINCT)\s+", rf"\1 TOP {n} ", body, count=1, flags=re.IGNORECASE)
        return re.sub(r"^SELECT\s+", f"SELECT TOP {n} ", body, count=1, flags=re.IGNORECASE)
    return f"{body} LIMIT {n}"


def ci_like(dialect: str, column: str, pattern_sql: str) -> str:
    """大小写不敏感匹配。postgres 有 ILIKE，别自己 `LOWER()` 包一层浪费索引。"""
    if dialect == "postgres":
        return f"{column} ILIKE {pattern_sql}"
    return f"LOWER({column}) LIKE LOWER({pattern_sql})"


def convert(sql: str, dialect: str, n: int | None = None) -> dict:
    """一步到位：前 N + 方言改写，返回可读的说明。"""
    out = top_n(sql, dialect, n) if n is not None else sql
    return {
        "dialect": dialect,
        "input": sql,
        "n": n,
        "output": out,
        "note": {
            "sqlite": "LIMIT 追加在末尾",
            "postgres": "同 sqlite，但可以用 ILIKE",
            "mysql": "同 sqlite，标识符用反引号",
            "sqlserver": "TOP 改写在 SELECT 之后，且不能有 LIMIT",
        }[dialect],
    }


def all_dialects(sql: str, n: int = 10) -> list[dict]:
    return [convert(sql, d, n) for d in DIALECTS]
