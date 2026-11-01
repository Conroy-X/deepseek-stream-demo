"""Unit 27 · 检索层（SQLite + 本地哈希向量）—— 只为给流式输出提供引用来源。

真正的检索单元是 Unit 25，那里有租户隔离、过滤、阈值和评测集。
这里只需要"给定问题取出几条带出处和页码的片段"，所以刻意做薄：

    chunks(id, document_id, source, page, text, vector)

向量存 JSON 数组、全表暴力扫。几千条以内比任何索引都快，也方便排查
"为什么这条没召回" —— 直接 SELECT 出来看向量就行。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

import embedding as emb

SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    source      TEXT NOT NULL,
    page        INTEGER NOT NULL,
    text        TEXT NOT NULL,
    vector      TEXT NOT NULL
);
"""


class StoreError(ValueError):
    pass


@dataclass
class Hit:
    chunk_id: str
    document_id: str
    source: str
    page: int
    text: str
    score: float


class Store:
    def __init__(self, db_path: str, embedder=None):
        self.db_path = db_path
        self.embedder = embedder or emb.get_embedder()
        with self._session() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _session(self):
        """一个「提交 + 关闭」的上下文管理器。

        别用 `with sqlite3.connect(...)`：它只提交事务，**不关连接**。
        连接没关 = 写锁没释放，下一次连接就会撞上 `database is locked` ——
        本单元第一版就是这么挂的（首次 GET /health 直接 500）。
        """
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def index(self, items: list[dict]) -> int:
        rows = []
        for it in items:
            if not it.get("text", "").strip():
                continue
            vec = self.embedder._embed_one(it["text"])
            rows.append(
                (
                    it["id"],
                    it["document_id"],
                    it.get("source", "unknown"),
                    int(it.get("page", 1)),
                    it["text"],
                    json.dumps([round(x, 6) for x in vec]),
                )
            )
        with self._session() as c:
            c.executemany(
                "INSERT OR REPLACE INTO chunks (id, document_id, source, page, text, vector)"
                " VALUES (?,?,?,?,?,?)",
                rows,
            )
        return len(rows)

    def count(self) -> int:
        with self._session() as c:
            return c.execute("SELECT count(*) FROM chunks").fetchone()[0]

    def clear(self) -> None:
        with self._session() as c:
            c.execute("DELETE FROM chunks")

    def search(self, query: str, top_k: int = 4, threshold: float = 0.05) -> list[Hit]:
        if not query.strip():
            raise StoreError("query 不能为空")
        if not 1 <= top_k <= 10:
            raise StoreError("top_k 必须在 1..10")
        with self._session() as c:
            rows = c.execute(
                "SELECT id, document_id, source, page, text, vector FROM chunks"
            ).fetchall()

        qv = self.embedder._embed_one(query)
        hits = []
        for r in rows:
            score = emb.cosine_similarity(qv, json.loads(r["vector"]))
            if score < threshold:
                continue
            hits.append(
                Hit(
                    chunk_id=r["id"],
                    document_id=r["document_id"],
                    source=r["source"],
                    page=r["page"],
                    text=r["text"],
                    score=score,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:top_k]
