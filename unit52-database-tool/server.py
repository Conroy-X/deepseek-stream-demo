"""Unit 52 · Database Tool：暴露表结构和只读 SQL。

教学核心：**「只读」不是一个动作，是连接的属性。**

很多人会写 `if not sql.upper().startswith("SELECT"): 报错` 然后说这是只读 SQL。
本单元会证明这种词法检查不够：`SELECT 1; DROP TABLE x`、`SELECT 1 -- ; DROP TABLE`
都能绕过。真正的只读必须做到位：`file:...?mode=ro` 让 sqlite3 在驱动层就拒写。

工具：
  list_tables       — 列出所有表 + 列摘要
  describe_table    — 单表详细 + 样例数据
  run_readonly_sql  — 只读 SQL 执行

启动：
    /home/rik/.cache/aj/runner/bin/python server.py
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from mcp.server.mcpserver import MCPServer

DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "articles.db"

server = MCPServer(
    "unit52-db",
    version="0.1.0",
    instructions="只读 SQL server。只允许单条 SELECT，写操作连驱动都拒。",
)


# ─────────────────────────────────────────────────────────────────────────
# DB 初始化：纯写连接建库 + 造数据（独立函数，server 运行时不再用）
# ─────────────────────────────────────────────────────────────────────────

AUTHORS = [
    ("a-001", "王强", "上海", "tech"),
    ("a-002", "李娜", "杭州", "design"),
    ("a-003", "张伟", "北京", "tech"),
    ("a-004", "陈静", "广州", "design"),
    ("a-005", "刘洋", "成都", "tech"),
]

ARTICLES = [
    ("p-101", "如何选 MCP 传输", "tech", "a-001", "2026-10-12", 842),
    ("p-102", "RAG 召回排序策略", "tech", "a-001", "2026-10-18", 1521),
    ("p-103", "设计稿到 Figma 的协作流", "design", "a-002", "2026-10-22", 488),
    ("p-104", "Postgre vs MySQL 触发器", "tech", "a-003", "2026-10-25", 993),
    ("p-105", "排版栅格的取舍", "design", "a-002", "2026-10-28", 612),
    ("p-106", "Python 3.13 与 free-threaded", "tech", "a-005", "2026-10-30", 1100),
]


def init_db() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    # 写连接：来建库 + 造数据，建完就关
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.executescript(
            """
            DROP TABLE IF EXISTS articles;
            DROP TABLE IF EXISTS authors;
            CREATE TABLE authors (
                id      TEXT PRIMARY KEY,
                name    TEXT NOT NULL,
                city    TEXT NOT NULL,
                dept    TEXT NOT NULL
            );
            CREATE TABLE articles (
                id          TEXT PRIMARY KEY,
                title        TEXT NOT NULL,
                category     TEXT NOT NULL,
                author_id    TEXT NOT NULL REFERENCES authors(id),
                publish_date TEXT NOT NULL,
                word_count   INTEGER NOT NULL
            );
            CREATE INDEX idx_articles_author ON articles(author_id);
            CREATE INDEX idx_articles_date ON articles(publish_date);
            """
        )
        conn.executemany("INSERT INTO authors VALUES (?,?,?,?)", AUTHORS)
        authors_count = conn.total_changes
        conn.executemany("INSERT INTO articles VALUES (?,?,?,?,?,?)", ARTICLES)
        articles_count = conn.total_changes - authors_count
        conn.commit()
    finally:
        conn.close()

    # 验证：之后用只读连接（能不能写？）
    ro = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        ro.execute("SELECT count(*) FROM authors").fetchone()
        try:
            ro.execute("DELETE FROM authors WHERE id='a-001'")
            # 不该走到这里
            raise SystemExit("只读连接竟然能 DELETE，代码写错了")
        except sqlite3.OperationalError as e:
            if "readonly" not in str(e).lower():
        #                        ^^^ 真的是只读连接拒绝，不是别的错
        #                        readonly 来自 sqlite3 的「attempt to write a readonly database」
                        raise
    finally:
        ro.close()


# ─────────────────────────────────────────────────────────────────────────
# 工具 1：列清单
# ─────────────────────────────────────────────────────────────────────────

@server.tool(
    name="list_tables",
    description="列出所有表名、列摘要、行数。无参数。用于回答「数据库里有什么」。",
)
def list_tables() -> str:
    ro = _ro()
    try:
        names = [r[0] for r in ro.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()]
        out = []
        for n in names:
            cols = _cols(ro, n)
            cnt = ro.execute(f"SELECT count(*) FROM {n}").fetchone()[0]
            out.append(f"{n} ({cnt} 行): {', '.join(cols)}")
        return "\n".join(out) or "无表"
    finally:
        ro.close()


# ─────────────────────────────────────────────────────────────────────────
# 工具 2：单表详情
# ─────────────────────────────────────────────────────────────────────────

@server.tool(
    name="describe_table",
    description="单表详细结构（列名/类型/可空/外键）+ 3 行样例。参数 table 必填。",
)
def describe_table(table: str) -> str:
    ro = _ro()
    try:
        # 表名也过一遍白名单：只能匹配 sqlite_master 里出现过的表
        names = {r[0] for r in ro.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if table not in names:
            return f"未知表：{table}，已知：{sorted(names)}"
        cols = _full_cols(ro, table)
        sample = ro.execute(f"SELECT * FROM {table} LIMIT 3").fetchall()
        lines = ["[表结构]"] + [f"  {c[0]:<14} {c[1]:<14} {'null' if c[2] else 'notnull'} {'fk→'+c[3] if c[3] else ''}" for c in cols]
        lines.append("[样例]")
        for row in sample:
            lines.append("  " + " | ".join(str(x) for x in row))
        return "\n".join(lines)
    finally:
        ro.close()


# ─────────────────────────────────────────────────────────────────────────
# 工具 3：执行 SQL（真只读连接 + 语句白名单）
# ─────────────────────────────────────────────────────────────────────────

# 攻击配方：词法检查拦不住的几条
# 1. 多语句: SELECT 1; DROP TABLE x    -- startswith("SELECT") 通过
# 2. 注释:   SELECT 1 -- ; DROP TABLE -- startswith 检查通过
# 3. PRAGMA:  PRAGMA database_list     -- 只读连接也允许
# 4. ATTACH:  ATTACH 'x.sqlite' AS x  -- 字面 DROP 也可
# 5. load_extension: 不可拦 —— 拦不到时建构 SQLite3 不能上传
# 6. 字面 add 但不给 dagbase: drop dable X; -- does, accept drop

@server.tool(
    name="run_readonly_sql",
    description="执行单条只读 SELECT。多语句、注释、PRAGMA、ATTACH 等会被拦。强制 LIMIT。",
)
def run_readonly_sql(sql: str) -> str:
    if not sql or not sql.strip():
        return "错误：SQL 为空"
    # 1. 剥注释 —— 防止 `SELECT 1 -- ; DROP TABLE` 偷渡
    stripped = _strip_comments(sql).strip()
    if not stripped:
        return "错误：去掉注释后 SQL 为空"
    # 2. 多语句检测 —— sqlite3.execute() 自己也会拒，但报错文本相对短
    #    这里主动拦下，给中文错误
    body = stripped.rstrip(";").strip()
    if ";" in body:
        return "错误：检测到多语句，本工具只允许单条 SELECT"
    # 3. 关键词白名单 —— 只接受 SELECT
    head = body.split(None, 1)[0].upper()
    if head != "SELECT":
        return f"错误：只允许 SELECT，检测到 {head}"
    # 4. 强制 LIMIT
    final = _enforce_limit(body, default=100, max_limit=1000)
    # 5. 真只读连接执行 —— 词法没拦到的写操作到这里炸
    ro = _ro()
    try:
        cur = ro.execute(final)
        rows = cur.fetchmany(50)
        if not rows:
            return "查询返回 0 行"
        cols = [c[0] for c in cur.description] if cur.description else []
        lines = ["\t".join(cols)] if cols else []
        for r in rows:
            lines.append("\t".join(str(x) for x in r))
        return "\n".join(lines)
    except sqlite3.OperationalError as e:
        return f"驱动层拒绝：{e}"


# ─────────────────────────────────────────────────────────────────────────
# 内部测试：ro vs rw
# ─────────────────────────────────────────────────────────────────────────

def _try_write_on_ro() -> tuple[bool, str]:
    """在只读连接上尝试写 —— 驱动层拒绝，证明「只读」是连接属性，不是动作属性。"""
    ro = _ro()
    try:
        ro.execute("INSERT INTO authors VALUES ('a-x','test','test','test')")
        return False, "INSERT 在只读连接上成功 —— 这不该发生"
    except sqlite3.OperationalError as e:
        return True, str(e)
    finally:
        ro.close()


def _try_write_on_rw() -> tuple[bool, str]:
    """在写连接上做同样写操作 —— 真的成功。证明词法检查不够。"""
    rw = sqlite3.connect(str(DB_PATH))
    try:
        cur = rw.execute("SELECT count(*) FROM articles")
        before = cur.fetchone()[0]
        rw.execute("INSERT INTO articles VALUES ('p-x','tmp','tech','a-001','2026-01-01',0)")
        cur = rw.execute("SELECT count(*) FROM articles")
        during = cur.fetchone()[0]
        rw.rollback()
        return True, f"写连接写成功，表行数 {before} → {during} → {before}（回滚后一致）"
    finally:
        rw.close()


# ─────────────────────────────────────────────────────────────────────────
# 内部工具
# ─────────────────────────────────────────────────────────────────────────

def _ro() -> sqlite3.Connection:
    """只读连接。这是本单元的核心：写操作在驱动层就会被拒。"""
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


def _cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _full_cols(conn: sqlite3.Connection, table: str) -> list[tuple[str, str, int, str]]:
    cols = []
    for c in conn.execute(f"PRAGMA table_info({table})").fetchall():
        # cid, name, type, notnull, dflt, pk
        cols.append((c[1], (c[2] or "TEXT").upper(), c[3], ""))
    # 外键信息
    for fk in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall():
        # id, seq, table, from, to, on_update, on_delete, match
        for c in cols:
            if c[0] == fk[3]:
                cols[cols.index(c)] = (c[0], c[1], c[2], fk[4] or fk[2])
    return cols


def _strip_comments(sql: str) -> str:
    """剥掉 `-- ...` 和 `/* ... */` 注释。

    关键不是「让 SQL 看着更干净」，是让 `SELECT 1 -- ; DROP TABLE` 后面那段不进关键词判断。
    如果只 startswith("SELECT") 检查，注释里的 DROP 会被忽略，但执行时不会执行 ——
    然而 SQLite 多语句拆分看的是分号，下面这条会真执行：
        SELECT 1; -- comment\nDROP TABLE x
    所以剥注释 + 拒多语句两条都要做。
    """
    import re
    s = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)   # 块注释
    s = re.sub(r"--[^\n]*", " ", s)                          # 行注释
    return s


def _enforce_limit(sql: str, default: int, max_limit: int) -> str:
    """没 LIMIT 补一个；有 LIMIT 但超过 max 就改写。"""
    import re
    m = re.search(r"\bLIMIT\s+(\d+)", sql, re.IGNORECASE)
    if not m:
        return f"{sql} LIMIT {default}"
    n = int(m.group(1))
    if n > max_limit:
        return re.sub(r"\bLIMIT\s+\d+", f"LIMIT {max_limit}", sql, count=1, flags=re.IGNORECASE)
    return sql


if __name__ == "__main__":
    init_db()
    server.run("stdio")