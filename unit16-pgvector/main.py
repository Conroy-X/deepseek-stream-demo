"""Unit 16 · pgvector —— 把向量存进 Postgres，用 SQL 做检索。

Unit 15 里向量还在内存里，进程一停就没了。
这一单元把它落到数据库：让"相似"变成一条可以索引、可以过滤、可以事务的 SQL。

启动：
    uv run uvicorn main:app --reload --port 8016
    打开 http://127.0.0.1:8016/

环境自检（先跑这个）：
    uv run python check_env.py

本文件是**骨架**：路由已定好，三个函数的实现留给你填。
每个函数体里的 TODO 都写了具体步骤和"要想一下"的问题 —— 那些问题才是本单元真正的考点。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text

from embedding import get_embedder

# 复用 Unit 11 的库，不另建数据库
DEFAULT_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ai_journey"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_URL).strip()

# 表名固定，方便 EXPLAIN 时对照
TABLE = "chunks"

# LocalHashingEmbedder 默认 512 维。改 embedder 就要改表的 vector(N)，两者必须一致。
EMBED_DIM = 512

engine = create_engine(DATABASE_URL, future=True)
embedder = get_embedder()


class DocIn(BaseModel):
    text: str = Field(min_length=1)
    meta: dict | None = None


class SearchIn(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    use_index: bool = True  # 关掉它就能对比"全表扫"的耗时


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时做一次轻量检查：连不上就直接报错，别等第一个请求才炸
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception as e:
        print("数据库连不上：%s" % str(e)[:120])
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
    return FileResponse("static/index.html")


# ---------------------------------------------------------------- 1. 建表
@app.post("/init")
def init_table():
    """建表 + 启用扩展。

    TODO 1：写出建表 SQL

        CREATE EXTENSION IF NOT EXISTS vector;

        CREATE TABLE IF NOT EXISTS chunks (
            id         bigserial PRIMARY KEY,
            text       text NOT NULL,
            meta       jsonb,
            emb        vector(512) NOT NULL,
            created_at timestamptz DEFAULT now()
        );

    要想一下的三件事：
      1. 为什么用 IF NOT EXISTS？如果不用，第二次调用会发生什么？
      2. vector(512) 这里的 512 从哪来？改 embedder 时忘了改这个会报什么错？
      3. 如果以后要换 embedding 模型（维度变了），这张表怎么办？
         （提示：ALTER TABLE ... ALTER COLUMN ... TYPE vector(1024) 会失败，为什么？）

    返回 {"ok": True, "table": TABLE}
    """
    raise NotImplementedError("TODO 1: 建表")


# ---------------------------------------------------------------- 2. 入库
@app.post("/insert")
def insert_doc(doc: DocIn):
    """文本 → 向量 → 入库。

    TODO 2：实现插入

    步骤：
      1. vec = embedder.embed([doc.text])[0]      # 拿到 list[float]
      2. 把 vec 变成 pgvector 认识的格式
      3. INSERT INTO chunks (text, meta, emb) VALUES (:t, :m, :e)

    要想清楚的关键点：
      **为什么不能直接把 Python 的 list 塞进 SQL 参数？**
      试一下就知道：psycopg 会把它当成 PostgreSQL 数组（ARRAY[real]），
      而 vector 列不接受数组类型。两种解法：
        (a) 手动转成字符串 '[0.1,0.2,...]' 再用 CAST(:e AS vector)
        (b) 用 pgvector 包提供的 register_vector，让 psycopg 自动适配

      两种都行，(b) 更干净 —— 但你要能说出 (b) 到底帮你在哪一步做了转换。

    返回 {"ok": True, "id": <新行 id>}
    """
    raise NotImplementedError("TODO 2: 入库")


# ---------------------------------------------------------------- 3. 检索
@app.post("/search")
def search(q: SearchIn):
    """Top-K 检索。

    TODO 3：实现查询

    核心 SQL：
        SELECT id, text, meta,
               1 - (emb <=> :qv) AS score          -- 余弦距离转相似度
        FROM chunks
        ORDER BY emb <=> :qv
        LIMIT :k

    要做的事：
      1. 把 q.query 算成向量
      2. 执行上面这条 SQL
      3. 返回 [{"text":..., "score":..., "meta":...}, ...]

    要想清楚的三件事：
      1. 为什么 ORDER BY 用 `emb <=> :qv` 而不是 `score DESC`？
         （提示：前者能走索引，后者要先把所有行算一遍再排序）
      2. `1 - 距离` 一定是"相似度"吗？对归一化后的向量成立吗？
      3. use_index=False 时怎么强制全表扫？
         （提示：`SET LOCAL enable_indexscan = off;` 要放在同一个事务里才生效）

    返回 {"results": [...], "took_ms": <耗时>}
    """
    raise NotImplementedError("TODO 3: 检索")


# ---------------------------------------------------------------- 4. 统计
@app.get("/stats")
def stats():
    """表大小、行数、索引情况 —— 用来填 notes.md 里的实测数据表。

    TODO 4：查这几项

        SELECT count(*) FROM chunks;
        SELECT pg_size_pretty(pg_total_relation_size('chunks'));
        SELECT indexname, pg_size_pretty(pg_relation_size(indexname::regclass))
        FROM pg_indexes WHERE tablename = 'chunks';

    返回 {"rows":..., "table_size":..., "indexes":[...]}
    """
    raise NotImplementedError("TODO 4: 统计")


# ---------------------------------------------------------------- 5. 索引
@app.post("/index")
def create_index(kind: str = "hnsw"):
    """建索引。kind 取 hnsw / ivfflat / none。

    TODO 5：按 kind 建对应索引

        HNSW:
          CREATE INDEX IF NOT EXISTS chunks_emb_hnsw
          ON chunks USING hnsw (emb vector_cosine_ops)
          WITH (m = 16, ef_construction = 64);

        IVFFlat:
          CREATE INDEX IF NOT EXISTS chunks_emb_ivf
          ON chunks USING ivfflat (emb vector_cosine_ops)
          WITH (lists = 100);

    要知道的前提（这条会卡住很多人）：
      **IVFFlat 建在空表上等于白建** —— 它要靠已有数据做聚类。
      所以顺序必须是：先塞够数据，再建 IVFFlat 索引。
      HNSW 没这个限制，空表也能建。

    返回 {"ok": True, "kind": kind}
    """
    raise NotImplementedError("TODO 5: 建索引")


if __name__ == "__main__":
    # 不启服务，直接跑一遍关键路径（骨架阶段会在这里报 NotImplementedError，正常）
    print("Unit 16 骨架自检")
    print("  数据库:", DATABASE_URL)
    print("  维度:", EMBED_DIM)
    print()
    print("下一步：")
    print("  1. uv run python check_env.py     # 确认环境")
    print("  2. 填 TODO 1-5")
    print("  3. uv run uvicorn main:app --reload --port 8016")
    print("  4. 用 /stats 和 EXPLAIN ANALYZE 填 notes.md 的实测表")
