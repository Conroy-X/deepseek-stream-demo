"""Unit 55 · MCP 服务端：把 shop.db 的三个动作暴露成工具。

三个工具的分工是刻意的 —— 它们正好是「生成 SQL」所需的最小信息集：
    list_tables   → 有哪些表
    describe_table→ 每张表什么样（列名/类型）
    run_readonly_sql → 试跑并拿回结果

注意每个工具都 **自己 try/except 并把原因写进正常返回值**，而不是 raise。
`mcp 2.3.0` 会把未捕获异常脱敏成 `Error executing tool xxx`，模型拿到那句话就等于瞎了
（详见 docs/mcp-sdk-2x-notes.md 第 5 节坑一）。
"""

from __future__ import annotations

import json
import sqlite3
import time

from mcp.server.mcpserver import MCPServer

import db

server = MCPServer(
    "nl2sql-mcp",
    version="0.1.0",
    instructions="只读的电商数据服务。schema 怎么描述给模型看，是本单元的重点。",
)


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


@server.tool(name="list_tables", description="列出库里所有表名（不含 sqlite 内部表）")
def list_tables() -> str:
    try:
        conn = db.ro_connect()
        try:
            names = db.table_names(conn)
            counts = db.row_counts(conn)
        finally:
            conn.close()
        return _json({"ok": True, "tables": names, "row_counts": counts})
    except Exception as exc:  # noqa: BLE001 - 故意把任何异常转成可读文本
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


@server.tool(name="describe_table", description="看一张表的列：列名、类型、是否非空、是否主键")
def describe_table(table: str) -> str:
    try:
        conn = db.ro_connect()
        try:
            if table not in db.table_names(conn):
                return _json({
                    "ok": False,
                    "error": f"表 {table!r} 不存在，可用表：{', '.join(db.table_names(conn))}",
                })
            cols = db.describe(conn, table)
        finally:
            conn.close()
        return _json({"ok": True, "table": table, "columns": cols, "count": len(cols)})
    except Exception as exc:  # noqa: BLE001
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}"})


@server.tool(name="run_readonly_sql", description="在只读连接上执行一条 SQL，返回列名与行数据")
def run_readonly_sql(sql: str, max_rows: int = 200) -> str:
    started = time.perf_counter()
    try:
        conn = db.ro_connect()
        try:
            cur = conn.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = [dict(r) for r in cur.fetchmany(max_rows + 1)]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        # 把 SQLite 的原始错误原样交出去 —— 第 58 章要靠它做自动修正
        return _json({
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "sql": sql,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        })
    except Exception as exc:  # noqa: BLE001
        return _json({"ok": False, "error": f"{type(exc).__name__}: {exc}", "sql": sql})

    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    return _json({
        "ok": True,
        "sql": sql,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
    })


@server.resource("sql://schema/full", name="全量 DDL", mime_type="text/plain")
def schema_full() -> str:
    """整库 DDL。第 56 章会拿它和「精简版」「按问题预筛版」比字符数。"""
    return db.DDL.strip()


if __name__ == "__main__":
    server.run("stdio")
