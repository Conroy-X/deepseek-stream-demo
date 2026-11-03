"""Unit 16 · pgvector —— 把向量存进 Postgres，用 SQL 做检索。

Unit 15 里向量还在内存里，进程一停就没了。
这一单元把它落到数据库：让"相似"变成一条可以索引、可以过滤、可以事务的 SQL。

启动：
    uv run uvicorn main:app --reload --port 8016
    打开 http://127.0.0.1:8016/

自检（不启服务，直接验一遍关键路径）：
    uv run python main.py

压测（造 1k/1万/5万 数据，对比无索引 / HNSW / IVFFlat）：
    uv run python bench.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from embedding import get_embedder

# .env 必须显式加载：uvicorn 从别的目录启动时，工作目录里没有 .env
load_dotenv(Path(__file__).with_name(".env"))

# 本机 PG 17 走 Unix socket + peer 认证（用户 rik），和 Unit 11 用的是同一个库。
# 换成 TCP 就写 postgresql+psycopg://user:pass@127.0.0.1:5432/ai_journey
DEFAULT_URL = "postgresql+psycopg://rik@/ai_journey?host=/var/run/postgresql"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_URL).strip()

# 表名固定，方便 EXPLAIN 时对照
TABLE = "chunks"

# 表的 vector(N) 与 embedder 的输出维度必须一致，否则 /insert 会直接报类型错误
EMBED_DIM = int(os.getenv("EMBEDDING_DIM", "512"))

engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
embedder = get_embedder()

_warn_dim = getattr(embedder, "dim", EMBED_DIM)
if _warn_dim != EMBED_DIM:
    print(
        f"[warn] embedder 维度 {_warn_dim} 与表定义 vector({EMBED_DIM}) 不一致："
        "改模型后必须同步改 EMBEDDING_DIM 并重建表"
    )


class DocIn(BaseModel):
    text: str = Field(min_length=1)
    meta: dict | None = None


class SearchIn(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    use_index: bool = True  # 关掉它就能对比"全表扫"的耗时
    explain: bool = False  # 要执行计划（EXPLAIN ANALYZE 的原始输出）


def _vec_literal(vec: list[float]) -> str:
    """把 Python list 编成 pgvector 的文本字面量 '[0.1,0.2,...]'。

    为什么不能直接把 list 塞进参数？
        psycopg 会把 list 适配成 PostgreSQL 数组（ARRAY[float8]），
        而 vector 列不接受数组 —— 报 "column emb is of type vector but
        expression is of type double precision[]"。
    两种解法：
        (a) 转字符串 + CAST(:e AS vector)      ← 本文件用这个，零额外依赖
        (b) pgvector 包注册的类型适配器         ← 更优雅，但多一层隐式转换
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:
        print("数据库连不上：%s" % str(e)[:120])
        print("检查 .env 里的 DATABASE_URL，或确认 PostgreSQL 已启动")
    yield


app = FastAPI(title="Unit 16 · pgvector", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def index():
    return FileResponse(Path(__file__).with_name("static") / "index.html")


# ---------------------------------------------------------------- 1. 建表
@app.post("/init")
def init_table():
    """建扩展 + 建表，幂等。

    为什么要 IF NOT EXISTS：不加的话第二次调用直接报
    "relation already exists" / "extension already exists"，接口不能重复调用。
    """
    with engine.begin() as c:
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        c.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE} (
                    id         bigserial PRIMARY KEY,
                    text       text NOT NULL,
                    meta       jsonb,
                    emb        vector({EMBED_DIM}) NOT NULL,
                    created_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
        )
        # 去重靠这条：同一条文本只存一行（text 太长不能直接建唯一索引，用 md5）
        c.execute(
            text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {TABLE}_text_md5 "
                f"ON {TABLE} (md5(text))"
            )
        )
    return {"ok": True, "table": TABLE, "dim": EMBED_DIM}


# ---------------------------------------------------------------- 2. 入库
@app.post("/insert")
async def insert_doc(doc: DocIn):
    """文本 → 向量 → 入库。

    重复插入同一条文本不会炸：靠 md5(text) 唯一索引 + ON CONFLICT DO NOTHING，
    返回值里 reused=True 表示"这行原来就有"，id 指向已有行。
    """
    vec = (await embedder.embed([doc.text]))[0]
    if len(vec) != EMBED_DIM:
        raise HTTPException(
            400, f"向量维度 {len(vec)} 与表定义 {EMBED_DIM} 不一致，检查 EMBEDDING_DIM"
        )

    payload = _vec_literal(vec)
    meta_json = json.dumps(doc.meta, ensure_ascii=False) if doc.meta is not None else None

    with engine.begin() as c:
        row = c.execute(
            text(
                f"""
                INSERT INTO {TABLE} (text, meta, emb)
                VALUES (:t, CAST(:m AS jsonb), CAST(:e AS vector))
                ON CONFLICT (md5(text)) DO NOTHING
                RETURNING id
                """
            ),
            {"t": doc.text, "m": meta_json, "e": payload},
        ).first()
        if row:
            return {"ok": True, "id": row[0], "reused": False, "dim": len(vec)}
        # 冲突了 —— 查回已有行
        existed = c.execute(
            text(f"SELECT id FROM {TABLE} WHERE md5(text) = md5(:t)"), {"t": doc.text}
        ).scalar_one()
    return {"ok": True, "id": existed, "reused": True, "dim": len(vec)}


# ---------------------------------------------------------------- 3. 检索
@app.post("/search")
async def search(q: SearchIn):
    """Top-K 检索。

    为什么 ORDER BY 用 `emb <=> :qv` 而不是 `ORDER BY score DESC`：
        前者是"按索引的有序距离取前 K 个"，HNSW 能直接沿图走；
        后者要先把所有行的距离算出来、再整体排序 —— 等于自愿放弃索引。
    为什么用 1 - 距离当分数：向量已 L2 归一化，余弦距离 = 1 - 余弦相似度，
        且二者单调反向，取前 K 的顺序完全一致。
    """
    qv = _vec_literal((await embedder.embed([q.query]))[0])

    sql_txt = f"""
        SELECT id, text, meta,
               1 - (emb <=> CAST(:qv AS vector)) AS score
        FROM {TABLE}
        ORDER BY emb <=> CAST(:qv AS vector)
        LIMIT :k
    """

    t0 = time.perf_counter()
    if q.use_index:
        with engine.connect() as c:
            rows = c.execute(text(sql_txt), {"qv": qv, "k": q.top_k}).mappings().all()
            plan = (
                c.execute(
                    text("EXPLAIN (ANALYZE, BUFFERS) " + sql_txt),
                    {"qv": qv, "k": q.top_k},
                )
                .scalars()
                .all()
                if q.explain
                else None
            )
    else:
        # SET LOCAL 只在当前事务内生效 —— 所以必须和执行语句在同一个 begin() 里
        with engine.begin() as c:
            c.execute(text("SET LOCAL enable_indexscan = off"))
            c.execute(text("SET LOCAL enable_bitmapscan = off"))
            rows = c.execute(text(sql_txt), {"qv": qv, "k": q.top_k}).mappings().all()
            plan = (
                c.execute(
                    text("EXPLAIN (ANALYZE, BUFFERS) " + sql_txt),
                    {"qv": qv, "k": q.top_k},
                )
                .scalars()
                .all()
                if q.explain
                else None
            )
    took_ms = (time.perf_counter() - t0) * 1000

    results = [
        {
            "id": r["id"],
            "text": r["text"],
            "score": round(float(r["score"]), 6),
            "meta": r["meta"],
        }
        for r in rows
    ]
    out = {"results": results, "took_ms": round(took_ms, 3), "use_index": q.use_index}
    if plan:
        out["plan"] = [str(x) for x in plan]
    return out


# ---------------------------------------------------------------- 4. 统计
@app.get("/stats")
def stats():
    """表大小、行数、索引 —— 用来填 notes.md 的实测表。"""
    with engine.connect() as c:
        rows = c.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one()
        size = c.execute(
            text(f"SELECT pg_size_pretty(pg_total_relation_size('{TABLE}'))")
        ).scalar_one()
        idx = c.execute(
            text(
                """
                SELECT indexname,
                       pg_size_pretty(pg_relation_size(indexname::regclass)) AS size
                FROM pg_indexes
                WHERE tablename = :t
                ORDER BY 1
                """
            ),
            {"t": TABLE},
        ).mappings().all()
    return {
        "rows": rows,
        "table_size": size,
        "indexes": [{"name": r["indexname"], "size": r["size"]} for r in idx],
    }


# ---------------------------------------------------------------- 5. 索引
@app.post("/index")
def create_index(kind: str = "hnsw"):
    """按 kind 建索引：hnsw / ivfflat / none。

    两个关键的建索引前提：
      · IVFFlat 要靠已有数据做 k-means 聚类 —— 空表建出来是**废索引**，
        之后即使灌满数据也不会自动重新聚类（必须 REINDEX）。
        所以顺序只能是：先灌数据，再建 IVFFlat。
      · HNSW 不需要先有数据，空表也能建，插新数据会增量维护图。
    """
    kind = kind.strip().lower()
    with engine.begin() as c:
        rows = c.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one()
        # 只保留一个向量索引，方便对比（同时也是为了避免 planner 在两个索引间摇摆）
        c.execute(text("DROP INDEX IF EXISTS chunks_emb_hnsw"))
        c.execute(text("DROP INDEX IF EXISTS chunks_emb_ivf"))

        if kind == "none":
            return {"ok": True, "kind": "none", "rows": rows, "dropped": True}

        if kind == "hnsw":
            c.execute(
                text(
                    f"""
                    CREATE INDEX chunks_emb_hnsw ON {TABLE}
                    USING hnsw (emb vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                    """
                )
            )
            return {
                "ok": True,
                "kind": "hnsw",
                "rows": rows,
                "note": "m=16 每层 16 条边，ef_construction=64 建图时的候选集大小",
            }

        if kind == "ivfflat":
            if rows < 100:
                return {
                    "ok": False,
                    "kind": "ivfflat",
                    "rows": rows,
                    "error": f"只有 {rows} 行，建 IVFFlat 等于白建（聚类样本不够）——先灌数据",
                }
            # 经验规则：lists ≈ rows/1000（1M 以下），太少=每个桶太大，太多=查询要扫太多桶
            lists = max(1, min(1000, rows // 1000))
            c.execute(
                text(
                    f"""
                    CREATE INDEX chunks_emb_ivf ON {TABLE}
                    USING ivfflat (emb vector_cosine_ops)
                    WITH (lists = {lists})
                    """
                )
            )
            return {
                "ok": True,
                "kind": "ivfflat",
                "rows": rows,
                "lists": lists,
                "note": "灌完数据再建；后续大量新增后要 REINDEX 重新聚类",
            }

        raise HTTPException(400, f"未知索引类型：{kind}（可选 hnsw / ivfflat / none）")


# ---------------------------------------------------------------- 自检
def _run(coro):
    return asyncio.run(coro)


def selfcheck() -> int:
    """不启服务，直接跑关键路径。15 项，全过说明这一单元跑通了。"""
    ok = bad = 0

    def check(name, fn):
        nonlocal ok, bad
        try:
            detail = fn()
            print(ok + bad + 1, name, "✓", detail)
            ok += 1
            return True
        except Exception as e:  # noqa: BLE001
            print(ok + bad + 1, name, "✗", str(e)[:160])
            bad += 1
            return False

    print("=" * 78)
    print("Unit 16 · pgvector 自检")
    print("=" * 78)

    # 1
    check("连上数据库", lambda: engine.connect().close() or DATABASE_URL.split("@")[-1])

    # 2 扩展
    def ext():
        with engine.connect() as c:
            v = c.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            ).scalar_one_or_none()
        if not v:
            raise RuntimeError("vector 扩展未启用")
        return "pgvector " + str(v)

    check("vector 扩展已启用", ext)

    # 3 /init 幂等
    def init_twice():
        r1 = init_table()
        r2 = init_table()
        if not (r1["ok"] and r2["ok"]):
            raise RuntimeError("init 返回不 ok")
        return f"表 {TABLE}，重复调用不报错"

    check("/init 建表且幂等", init_twice)

    # 4 维度确实落到了 vector(512)
    def dim():
        with engine.connect() as c:
            d = c.execute(
                text(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'chunks'::regclass AND attname = 'emb'"
                )
            ).scalar_one()
        if d != EMBED_DIM:
            raise RuntimeError(f"表里是 {d} 维，期望 {EMBED_DIM}")
        return f"vector({d})"

    check("表列维度 = EMBED_DIM", dim)

    # 5 插入
    doc = {"text": "pgvector 把向量存进 Postgres，用 <=> 算余弦距离", "meta": {"src": "selfcheck"}}
    first = _run(insert_doc(DocIn(**doc)))

    def ins():
        if not first["id"]:
            raise RuntimeError(str(first))
        return f"id={first['id']} reused={first['reused']}"

    check("/insert 入库返回 id", ins)

    # 6 重复插入不新增行
    def dup():
        before = stats()["rows"]
        r = _run(insert_doc(DocIn(**doc)))
        after = stats()["rows"]
        if not r["reused"]:
            raise RuntimeError("repeat insert 未被识别为重复")
        if after != before:
            raise RuntimeError(f"行数变了：{before} → {after}")
        return f"复用 id={r['id']}，行数仍 {after}"

    check("重复插入复用同一行", dup)

    # 7 落库向量模长 ≈ 1（归一化真的生效了）
    def norm():
        with engine.connect() as c:
            r = c.execute(
                text(f"SELECT sqrt(sum(x*x)) FROM (SELECT unnest(emb::real[]) AS x FROM {TABLE} WHERE id = :i) s"),
                {"i": first["id"]},
            ).scalar_one()
        if abs(float(r) - 1.0) > 1e-3:
            raise RuntimeError(f"模长 {r}，期望 1")
        return f"|v| = {float(r):.6f}"

    check("入库向量已 L2 归一化", norm)

    # 8 自相似度 ≈ 1
    def self_sim():
        r = _run(search(SearchIn(query=doc["text"], top_k=1)))
        s = r["results"][0]["score"]
        if abs(s - 1.0) > 1e-4:
            raise RuntimeError(f"自相似 {s}，期望 1")
        return f"score = {s}"

    check("同一文本检索自己 = 1.0", self_sim)

    # 9 无关文本分数低
    def irrelevant():
        r = _run(search(SearchIn(query="红烧肉的做法与糖色调配", top_k=3)))
        top = r["results"][0]
        if top["score"] > 0.3:
            raise RuntimeError(f"无关查询最高分 {top['score']} 偏高")
        return f"最高分 {top['score']}（字面基线，语义相近它也看不出来）"

    check("无关文本分数 < 0.3", irrelevant)

    # 10 Top-K 数量与降序
    def topk():
        r = _run(search(SearchIn(query="向量检索", top_k=3)))
        scores = [x["score"] for x in r["results"]]
        if len(scores) > 3:
            raise RuntimeError("返回超过 top_k")
        if scores != sorted(scores, reverse=True):
            raise RuntimeError(f"未按分数降序：{scores}")
        return f"{len(scores)} 条，降序 {scores}"

    check("Top-K 条数与降序", topk)

    # 11 全表扫与索引路径结果一致
    def same_result():
        a = _run(search(SearchIn(query="向量检索", top_k=3, use_index=True)))
        b = _run(search(SearchIn(query="向量检索", top_k=3, use_index=False)))
        ida = [x["id"] for x in a["results"]]
        idb = [x["id"] for x in b["results"]]
        if ida != idb:
            raise RuntimeError(f"两条路径结果不同：{ida} vs {idb}")
        return f"顺序一致 {ida}，耗时 {a['took_ms']}ms vs {b['took_ms']}ms"

    check("use_index 开关不改结果", same_result)

    # 12 stats 行数与库一致
    def stats_ok():
        s = stats()
        with engine.connect() as c:
            n = c.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one()
        if s["rows"] != n:
            raise RuntimeError(f"stats 行数 {s['rows']} ≠ 实际 {n}")
        return f"{s['rows']} 行，{s['table_size']}，索引 {len(s['indexes'])} 个"

    check("/stats 行数与表一致", stats_ok)

    # 13 HNSW 能建
    def hnsw():
        r = create_index("hnsw")
        if not r["ok"]:
            raise RuntimeError(str(r))
        return "chunks_emb_hnsw 已建"

    has_hnsw = check("建 HNSW 索引", hnsw)

    # 14 执行计划里出现 Index Scan（小表 planner 默认走 Seq Scan，所以要强制）
    def plan_uses_index():
        if not has_hnsw:
            raise RuntimeError("上一步索引没建成")
        with engine.connect() as c:
            c.execute(text("SET enable_seqscan = off"))
            rows = c.execute(
                text(
                    f"EXPLAIN SELECT id FROM {TABLE} ORDER BY emb <=> CAST(:q AS vector) LIMIT 5"
                ),
                {"q": _vec_literal([0.1] * EMBED_DIM)},
            ).scalars().all()
        joined = " ".join(rows)
        if "Index Scan" not in joined:
            raise RuntimeError("计划里没有 Index Scan：" + joined[:120])
        return "plan 命中 Index Scan using chunks_emb_hnsw"

    check("EXPLAIN 能命中索引", plan_uses_index)

    # 15 IVFFlat 的小表保护
    def ivf_guard():
        r = create_index("ivfflat")
        if r["ok"] and stats()["rows"] < 100:
            raise RuntimeError("行数不足却建成了 IVFFlat")
        return r.get("error") or f"lists={r.get('lists')}"

    check("IVFFlat 小表拒绝建（防白建）", ivf_guard)

    print("-" * 78)
    print(f"通过 {ok} / 失败 {bad}")
    if bad == 0:
        print("全过。下一步：uv run python bench.py 填 notes.md 的实测表")
    print("=" * 78)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(selfcheck())
