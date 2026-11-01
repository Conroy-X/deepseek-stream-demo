"""Unit 19 · 向量存储：SQLite 存 chunk + 向量，查询时全量算余弦。

为什么是 SQLite 而不是 pgvector：
    本单元要验证的是「检索行为」——排序、阈值、过滤、指标，
    这些和用什么引擎无关。SQLite 零依赖、单文件、可反复删了重建，
    比先装一个 Postgres 再调 docker 更适合把逻辑想清楚。
    真上量了换 pgvector / HNSW，改的是 store 里的两个方法，不是调用方。

存储格式：
    向量用 struct 打包成 BLOB（float32），不用 JSON。
    512 维固定 2048 字节；同样内容用 JSON 写要 2.6 KB（本地哈希向量很稀疏，一半是 0），
    换成稠密向量（每个分量都非零）是 11.1 KB —— 差 5 倍，而且字符串解析比 struct 慢一个量级。
    这个选择只在「自建存储」时成立；用向量库时序列化是库的事。

检索是 **暴力扫描（flat / brute-force）**：读出全部向量，逐个算余弦，排序取前 K。
    N 在几万以内完全够用，也永远是 ANN 的召回参照。
    它的复杂度是 O(N·d)，瓶颈在 Python 循环，不在数学 —— notes 里有实测。
"""

from __future__ import annotations

import json
import math
import os
import sqlite3
import struct
import time
from dataclasses import dataclass, field

# 允许过滤的字段白名单。**字段名不能来自用户输入**，否则就是拼接 SQL 的口子。
FILTERABLE = frozenset({"source", "page", "category", "tenant"})
MAX_TOP_K = 50

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id      TEXT PRIMARY KEY,
    source  TEXT NOT NULL,
    page    INTEGER NOT NULL DEFAULT 1,
    text    TEXT NOT NULL,
    vector  BLOB NOT NULL,
    dim     INTEGER NOT NULL,
    meta    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chunks_source ON chunks(source);
CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(page);
"""


class SearchError(ValueError):
    """参数不合法（top_k 越界、filter 字段不在白名单、维度不匹配）。"""


@dataclass
class Hit:
    id: str
    score: float
    text: str
    source: str
    page: int
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "score": round(self.score, 4),
            "text": self.text,
            "source": self.source,
            "page": self.page,
            "meta": self.meta,
        }


@dataclass
class SearchResult:
    """除了命中，还要报告「扫了多少行、花了多久」—— 调 top_k 时这两个数会一起变。"""

    hits: list[Hit]
    scanned: int
    elapsed_ms: float
    top_k: int
    min_score: float | None
    filters: dict

    def to_dict(self) -> dict:
        return {
            "hits": [h.to_dict() for h in self.hits],
            "count": len(self.hits),
            "scanned": self.scanned,
            "elapsed_ms": round(self.elapsed_ms, 3),
            "top_k": self.top_k,
            "min_score": self.min_score,
            "filters": self.filters,
        }


# ---------------------------------------------------------------------------
# 向量序列化与数学
# ---------------------------------------------------------------------------
def pack_vector(vec: list[float]) -> bytes:
    """float32 打包。512 维 = 2048 字节；同样的向量写 JSON 要 2.6 KB（稀疏）到 11 KB（稠密）。

    代价是精度：float32 只有约 7 位有效数字，余弦分数会在第 6~7 位抖动。
    对排序无影响（差 1e-7 不会改变名次），但**别用 == 比较分数**。
    """
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    if len(blob) % 4:
        raise SearchError(f"向量字节数 {len(blob)} 不是 4 的倍数，BLOB 被写坏了")
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def l2_normalize(vec: list[float]) -> list[float]:
    """入库前归一化：之后余弦就等于点积，且分数不会被向量长度带偏。"""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return list(vec)
    return [x / norm for x in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise SearchError(f"维度不一致：查询 {len(a)} 维，库里 {len(b)} 维")
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------
class VectorStore:
    """一个 chunk 表 + 一个暴力检索。

    path 传 ":memory:" 就是一次性库（自检用），传文件才有持久化。
    """

    def __init__(self, path: str = "data/vectors.db", dim: int | None = None):
        self.path = path
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self.dim = dim

    # -- 写 ---------------------------------------------------------------
    def upsert(self, items: list[dict]) -> int:
        """items: [{id, text, vector, source, page, meta?}]

        用 UPSERT 而不是 INSERT：重跑一次入库脚本不该报主键冲突，
        也不该留下两份同一个 chunk（否则检索结果里会出现重复片段）。
        """
        if not items:
            return 0
        rows = []
        for it in items:
            vec = [float(x) for x in it["vector"]]
            if self.dim is None:
                self.dim = len(vec)
            elif len(vec) != self.dim:
                raise SearchError(f"{it.get('id')} 维度 {len(vec)} 与库的 {self.dim} 不一致")
            rows.append(
                (
                    str(it["id"]),
                    str(it.get("source", "unknown")),
                    int(it.get("page", 1)),
                    str(it["text"]),
                    pack_vector(vec),
                    len(vec),
                    json.dumps(it.get("meta") or {}, ensure_ascii=False, sort_keys=True),
                )
            )
        self.conn.executemany(
            """INSERT INTO chunks (id, source, page, text, vector, dim, meta)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 source=excluded.source, page=excluded.page, text=excluded.text,
                 vector=excluded.vector, dim=excluded.dim, meta=excluded.meta""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def clear(self) -> None:
        self.conn.execute("DELETE FROM chunks")
        self.conn.commit()

    # -- 读 ---------------------------------------------------------------
    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0])

    def get(self, chunk_id: str) -> Hit | None:
        row = self.conn.execute(
            "SELECT id, source, page, text, vector, meta FROM chunks WHERE id = ?", (chunk_id,)
        ).fetchone()
        if row is None:
            return None
        return Hit(row["id"], 1.0, row["text"], row["source"], row["page"], json.loads(row["meta"]))

    def stats(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n, MAX(dim) AS dim, COALESCE(SUM(LENGTH(text)), 0) AS chars FROM chunks"
        ).fetchone()
        return {"count": int(row["n"]), "dim": row["dim"], "chars": int(row["chars"])}

    def sources(self) -> list[dict]:
        """按 source 聚合：每个文件多少块、覆盖到第几页。UI 和 /health 都用它。"""
        rows = self.conn.execute(
            """SELECT source, COUNT(*) AS n, MIN(page) AS p1, MAX(page) AS p2,
                      SUM(LENGTH(text)) AS chars
               FROM chunks GROUP BY source ORDER BY source"""
        ).fetchall()
        return [dict(r) for r in rows]

    def _where(self, filters: dict | None) -> tuple[str, list]:
        """把 filter dict 变成 WHERE 子句。

        两个安全点：
          1. 字段名必须在 FILTERABLE 里（白名单），不能拼用户给的字符串
          2. 值一律走占位符 ?，不拼进 SQL
        常见错误是「值参数化了，字段名没有」—— 一样能注入。
        """
        if not filters:
            return "", []
        clauses: list[str] = []
        params: list = []
        for key, val in filters.items():
            if key not in FILTERABLE:
                raise SearchError(f"不支持按 {key!r} 过滤，可选：{sorted(FILTERABLE)}")
            if key == "page":
                clauses.append("page = ?")
                params.append(int(val))
            elif key == "source":
                clauses.append("source = ?")
                params.append(str(val))
            else:
                # category / tenant 存在 meta JSON 里
                clauses.append(f"json_extract(meta, '$.{key}') = ?")
                params.append(str(val))
        return "WHERE " + " AND ".join(clauses), params

    def search(
        self,
        qvec: list[float],
        top_k: int = 5,
        min_score: float | None = None,
        filters: dict | None = None,
    ) -> SearchResult:
        """先过滤 → 再算分 → 再阈值 → 再排序取 K。

        顺序很重要：
          - 过滤必须在排序之前，否则「先全局 Top-5 再过滤」会把结果削到 1 条，
            然后你以为是库里没数据。
          - 阈值必须作用在分数上、排序之后不影响正确性，
            但提前剪掉能少排很多无效项。
        """
        if not qvec:
            raise SearchError("查询向量为空")
        if not isinstance(top_k, int) or isinstance(top_k, bool):
            raise SearchError(f"top_k 必须是整数，收到 {type(top_k).__name__}")
        if not 1 <= top_k <= MAX_TOP_K:
            raise SearchError(f"top_k 必须在 1..{MAX_TOP_K} 之间，收到 {top_k}")
        if min_score is not None and not -1.0 <= float(min_score) <= 1.0:
            raise SearchError(f"min_score 必须在 -1..1 之间，收到 {min_score}")

        where, params = self._where(filters)
        t0 = time.perf_counter()
        rows = self.conn.execute(
            f"SELECT id, source, page, text, vector, dim, meta FROM chunks {where}", params
        ).fetchall()

        hits: list[Hit] = []
        for row in rows:
            if row["dim"] != len(qvec):
                raise SearchError(f"库内向量 {row['dim']} 维，查询 {len(qvec)} 维")
            score = cosine(qvec, unpack_vector(row["vector"]))
            if min_score is not None and score < float(min_score):
                continue
            hits.append(
                Hit(row["id"], score, row["text"], row["source"], row["page"], json.loads(row["meta"]))
            )
        # 同分按 id 排，保证结果可复现 —— 否则自检的「降序」断言会随机挂
        hits.sort(key=lambda h: (-h.score, h.id))
        elapsed = (time.perf_counter() - t0) * 1000
        return SearchResult(hits[:top_k], len(rows), elapsed, top_k, min_score, dict(filters or {}))

    def close(self) -> None:
        self.conn.close()
