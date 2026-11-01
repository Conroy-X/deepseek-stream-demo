"""Unit 16 环境自检 —— 坐下第一件事先跑这个。

它只做检查，不改你的库结构（冒烟测试用 TEMP 表，会话结束自动消失）。

    uv run python check_env.py

全绿说明可以直接开始写 main.py；哪一步红了，就修那一步再往下。
"""

from __future__ import annotations

import os
import sys

from sqlalchemy import create_engine, text

# 复用 Unit 11 的库：同一个 ai_journey，避免再建一个数据库
DEFAULT_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ai_journey"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_URL).strip()

ok = 0
bad = 0


def section(t):
    print("\n" + t)


def check(name, fn):
    global ok, bad
    try:
        detail = fn()
        ok += 1
        print("  ✓ %-38s %s" % (name, detail if detail else ""))
        return True
    except Exception as e:
        bad += 1
        print("  ✗ %-38s %s" % (name, str(e)[:120]))
        return False


def main():
    print("=" * 70)
    print("Unit 16 · pgvector 环境自检")
    print("=" * 70)

    section("[1] 数据库连接")
    engine = create_engine(DATABASE_URL, future=True)

    def conn():
        with engine.connect() as c:
            v = c.execute(text("SELECT version()")).scalar()
            db = c.execute(text("SELECT current_database()")).scalar()
            return "%s / %s" % (db, v.split(" on ")[0])

    if not check("能连上 Postgres", conn):
        print("\n连接都失败了，后面的检查没意义。")
        print("提示：Unit 11 用的是 Unix socket，检查 DATABASE_URL 是否正确。")
        return

    section("[2] pgvector 扩展")

    def has_vector():
        with engine.connect() as c:
            row = c.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            ).scalar()
        if not row:
            raise RuntimeError("扩展未启用，跑一次 CREATE EXTENSION vector")
        return "v" + row

    if not check("vector 扩展已启用", has_vector):
        print("\n如果只是没启用，在 psql 里执行：CREATE EXTENSION IF NOT EXISTS vector;")

    section("[3] 向量类型与距离算子")

    def vector_type():
        with engine.connect() as c:
            c.execute(text("CREATE TEMP TABLE t (id int, emb vector(3))"))
            c.execute(
                text("INSERT INTO t VALUES (1,'[1,0,0]'), (2,'[0,1,0]'), (3,'[0.9,0.1,0]')")
            )
            rows = c.execute(
                text("SELECT id, emb <=> '[1,0,0]' FROM t ORDER BY emb <=> '[1,0,0]'")
            ).all()
        order = [r[0] for r in rows]
        dist = {r[0]: float(r[1]) for r in rows}
        if order != [1, 3, 2]:
            raise RuntimeError("排序不对：%s" % order)
        # 手算校验：cos([0.9,0.1,0],[1,0,0]) = 0.9/√0.82 ≈ 0.99388 → 距离 0.0061
        if abs(dist[3] - 0.0061) > 0.0005:
            raise RuntimeError("余弦距离算错：%.4f（应约 0.0061）" % dist[3])
        return "排序 [1,3,2] 正确，d(3)=%.4f" % dist[3]

    check("vector(3) 建表 + <=> 排序", vector_type)

    section("[4] 索引能力（只验证语法，不建真实索引）")

    def index_syntax():
        with engine.connect() as c:
            c.execute(text("CREATE TEMP TABLE t2 (id int, emb vector(3))"))
            c.execute(
                text("CREATE INDEX ON t2 USING hnsw (emb vector_cosine_ops)")
            )
            c.execute(text("DROP TABLE t2"))
            return "HNSW 可创建"

    check("HNSW 索引语法", index_syntax)

    section("[5] 现有表结构（Unit 11 留下的）")

    def tables():
        with engine.connect() as c:
            rows = c.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' ORDER BY 1"
                )
            ).scalars().all()
        return ", ".join(rows) if rows else "（空库）"

    check("列出 public 表", tables)

    print("\n" + "=" * 70)
    print("通过 %d / 失败 %d" % (ok, bad))
    if bad == 0:
        print("环境就绪。下一步：写 main.py 的 /init、/insert、/search 三个路由。")
        print("记住完成标准：能讲清 HNSW 和 IVFFlat 的区别与取舍。")
    print("=" * 70)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
