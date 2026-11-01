"""Unit 25 · Retriever 模块 —— 把"检索"从路由里抽出来，变成可测试、可替换的一层。

为什么必须抽出来：
    一旦 top_k、阈值、过滤条件散落在各个路由里，你就再也没法回答
    "改这个参数到底变好还是变差"。Retriever 是唯一入口，参数和日志都在这里。

这层要守住四件事：
    1. 租户隔离 —— 过滤条件由服务端拼，绝不接受客户端传来的原始 SQL/字段
    2. 元数据过滤 —— source / page 范围，先过滤再排序（或先排序再过滤，结果不同，下面会讲）
    3. 阈值 —— 低于阈值的一律不返回，宁可不答也别答错
    4. 可观测 —— 每次查询都落一条日志，能复盘"为什么这题没召回"

存储用 SQLite：几千条 chunk 的暴力扫描比任何索引都快，也好调试。
换 pgvector 只改 Store 的实现，Retriever 的接口不动。
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import embedding as emb

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    source      TEXT NOT NULL,
    page        INTEGER NOT NULL,
    text        TEXT NOT NULL,
    vector      TEXT NOT NULL           -- JSON 数组；真实项目用 pgvector 的 vector 类型
);
CREATE INDEX IF NOT EXISTS idx_chunks_tenant ON chunks(tenant_id, source);

CREATE TABLE IF NOT EXISTS retrieval_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    tenant_id   TEXT NOT NULL,
    query       TEXT NOT NULL,
    top_k       INTEGER NOT NULL,
    threshold   REAL NOT NULL,
    filters     TEXT NOT NULL,
    hits        INTEGER NOT NULL,
    top_score   REAL,
    took_ms     REAL NOT NULL,
    result_ids  TEXT NOT NULL
);
"""


class RetrieverError(ValueError):
    """参数或权限问题。路由层统一转 400/403。"""


@dataclass
class Filters:
    """只允许这几个字段 —— 这是安全边界，不是便利 API。"""

    source: str | None = None
    source_prefix: str | None = None
    page_min: int | None = None
    page_max: int | None = None

    def to_sql(self) -> tuple[str, list]:
        where: list[str] = []
        args: list = []
        if self.source:
            where.append("source = ?")
            args.append(self.source)
        if self.source_prefix:
            where.append("source LIKE ?")
            args.append(f"{self.source_prefix}%")
        if self.page_min is not None:
            where.append("page >= ?")
            args.append(self.page_min)
        if self.page_max is not None:
            where.append("page <= ?")
            args.append(self.page_max)
        return (" AND ".join(where) or "1=1"), args

    def to_dict(self) -> dict:
        return {
            k: v
            for k, v in {
                "source": self.source,
                "source_prefix": self.source_prefix,
                "page_min": self.page_min,
                "page_max": self.page_max,
            }.items()
            if v is not None
        }


@dataclass
class Hit:
    chunk_id: str
    text: str
    score: float
    source: str
    page: int
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "score": round(self.score, 4),
            "source": self.source,
            "page": self.page,
            "meta": self.meta,
        }


class _ConnCtx:
    """`with sqlite3.connect(...)` 只提交、不关闭连接；这个上下文管理器两个都做。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        return self.conn

    def __exit__(self, exc_type, exc, tb) -> bool:
        assert self.conn is not None
        if exc_type is None:
            self.conn.commit()
        else:
            self.conn.rollback()
        self.conn.close()
        return False


class Retriever:
    def __init__(self, db_path: str | Path, embedder=None):
        self.db_path = str(db_path)
        self.embedder = embedder or emb.get_embedder()
        with self._conn() as c:
            c.executescript(SCHEMA)

    def _conn(self):
        """注意：`with sqlite3.connect(...)` 只提交事务、**不关闭连接**。
        直接那么写，跑几千次检索就会把文件句柄耗光。所以这里显式包一层。
        """
        return _ConnCtx(self.db_path)

    # ---------------------------------------------------------------- 写入
    def index(self, tenant_id: str, items: list[dict]) -> int:
        """items: [{id, source, page, text}]，重复 id 覆盖。"""
        if not tenant_id:
            raise RetrieverError("tenant_id 必填")
        rows = []
        for it in items:
            if not it.get("text", "").strip():
                continue
            vec = self.embedder._embed_one(it["text"])
            rows.append(
                (
                    it["id"],
                    tenant_id,
                    it.get("source", "unknown"),
                    int(it.get("page", 1)),
                    it["text"],
                    json.dumps([round(x, 6) for x in vec]),
                )
            )
        with self._conn() as c:
            c.executemany(
                "INSERT OR REPLACE INTO chunks (id, tenant_id, source, page, text, vector)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def count(self, tenant_id: str | None = None) -> int:
        with self._conn() as c:
            if tenant_id:
                return c.execute(
                    "SELECT count(*) FROM chunks WHERE tenant_id = ?", (tenant_id,)
                ).fetchone()[0]
            return c.execute("SELECT count(*) FROM chunks").fetchone()[0]

    def clear(self, tenant_id: str | None = None) -> None:
        with self._conn() as c:
            if tenant_id:
                c.execute("DELETE FROM chunks WHERE tenant_id = ?", (tenant_id,))
            else:
                c.execute("DELETE FROM chunks")

    # ---------------------------------------------------------------- 检索
    def search(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 5,
        threshold: float = 0.0,
        filters: Filters | None = None,
        log: bool = True,
    ) -> list[Hit]:
        if not tenant_id:
            raise RetrieverError("tenant_id 必填：缺了它就会跨租户检索")
        if not query.strip():
            raise RetrieverError("query 不能为空")
        if not 1 <= top_k <= 50:
            raise RetrieverError("top_k 必须在 1..50")
        if not 0.0 <= threshold <= 1.0:
            raise RetrieverError("threshold 必须在 0..1")

        filters = filters or Filters()
        cond, args = filters.to_sql()
        t0 = time.perf_counter()

        # 关键：tenant_id 固定写在 WHERE 里，和调用方传来的 filters 用 AND 连接
        sql = f"SELECT id, source, page, text, vector FROM chunks WHERE tenant_id = ? AND {cond}"
        with self._conn() as c:
            rows = c.execute(sql, [tenant_id, *args]).fetchall()

        qv = self.embedder._embed_one(query)
        scored: list[Hit] = []
        for r in rows:
            v = json.loads(r["vector"])
            s = emb.cosine_similarity(qv, v)
            if s < threshold:
                continue
            scored.append(
                Hit(chunk_id=r["id"], text=r["text"], score=s, source=r["source"], page=r["page"])
            )
        scored.sort(key=lambda h: h.score, reverse=True)
        hits = scored[:top_k]

        if log:
            self._log(tenant_id, query, top_k, threshold, filters, hits, (time.perf_counter() - t0) * 1000)
        return hits

    def _log(self, tenant_id, query, top_k, threshold, filters, hits, ms) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO retrieval_logs (ts, tenant_id, query, top_k, threshold, filters,"
                " hits, top_score, took_ms, result_ids) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    time.time(),
                    tenant_id,
                    query,
                    top_k,
                    threshold,
                    json.dumps(filters.to_dict(), ensure_ascii=False),
                    len(hits),
                    hits[0].score if hits else None,
                    round(ms, 3),
                    json.dumps([h.chunk_id for h in hits], ensure_ascii=False),
                ),
            )

    def logs(self, tenant_id: str, limit: int = 20) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM retrieval_logs WHERE tenant_id = ? ORDER BY id DESC LIMIT ?",
                (tenant_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 指标：别用"感觉召回了"，用这两个数
# ---------------------------------------------------------------------------
def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """前 k 个里命中了几成相关。分母是相关总数，不是 k。"""
    if not relevant:
        return 0.0
    return len(set(retrieved[:k]) & relevant) / len(relevant)


def mrr(retrieved: list[str], relevant: set[str]) -> float:
    """第一个相关结果排第几的倒数。第 1 位 = 1.0，第 3 位 = 0.333。"""
    for i, doc_id in enumerate(retrieved, 1):
        if doc_id in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    """二值相关性下的 nDCG@k。和 MRR 的区别：它奖励"全都排前面"。"""
    import math

    dcg = sum(1.0 / math.log2(i + 1) for i, d in enumerate(retrieved[:k], 1) if d in relevant)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0
