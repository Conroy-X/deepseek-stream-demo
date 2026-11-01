"""Unit 26 · Reranker —— 召回之后，再排一次。

为什么需要重排：
    向量召回是"双塔"：query 和 doc 各自编码，永不交互。
    快，但粗 —— 它只能比"整体像不像"，比不了"这段到底回答了没有"。
    也可能被"关键词堆满的长文本"骗到（向量只看重叠，越长越容易撞上）。

重排是"交叉"的：query 和 doc 一起看，所以更准，也更慢。
所以工程上的固定套路是 **召回 Top-20 → 重排 Top-5**，而不是对全库重排。

本单元的 reranker 是 BM25，不是 CrossEncoder：
    · BM25 仍然是字面匹配，但带 IDF 和长度归一化，能治"堆关键词"
    · 真 CrossEncoder（bge-reranker）需要装模型，本仓库不引入
    · 两者接口一样：query + candidates → 分数。换实现不改上层
BM25 治不了的（同义改写、跨语言），只有真 CrossEncoder 能治 —— 这一点在评测里会看到。
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from embedding import tokenize


@dataclass
class Scored:
    chunk_id: str
    text: str
    score: float
    vector_score: float = 0.0
    vector_rank: int = 0
    rerank_rank: int = 0

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "score": round(self.score, 4),
            "vector_score": round(self.vector_score, 4),
            "vector_rank": self.vector_rank,
            "rerank_rank": self.rerank_rank,
        }


def bm25_scores(
    query: str,
    docs: list[str],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    """BM25（Okapi）。候选集内算 IDF，所以分数只在本批候选里可比。

    为什么它比"向量重叠计数"更抗堆砌：
      · IDF：在全候选里到处出现的词（如"退款"）权重低
      · 长度归一化 b：长文档会被罚分，堆关键词的代价立刻体现
    """
    if not docs:
        return []

    q_terms = tokenize(query)
    if not q_terms:
        return [0.0] * len(docs)

    doc_terms = [tokenize(d) for d in docs]
    n = len(docs)
    avg_len = sum(len(t) for t in doc_terms) / n or 1.0

    df: Counter[str] = Counter()
    for terms in doc_terms:
        for t in set(terms):
            df[t] += 1
    # 加 0.5 平滑，避免 df = n 时 idf 变成负数
    idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in df}

    scores: list[float] = []
    for terms in doc_terms:
        tf = Counter(terms)
        dl = len(terms) or 1
        s = 0.0
        for t in q_terms:
            if t not in tf:
                continue
            freq = tf[t]
            s += idf.get(t, 0.0) * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * dl / avg_len))
        scores.append(s)
    return scores


def rerank(
    query: str,
    candidates: list[Scored],
    top_k: int = 5,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[Scored]:
    """对候选重排，返回 top_k。候选原顺序视为向量排名，保留下来便于对比。"""
    if not candidates:
        return []
    if top_k < 1:
        raise ValueError("top_k 必须 >= 1")

    for i, c in enumerate(candidates, 1):
        c.vector_rank = i

    scores = bm25_scores(query, [c.text for c in candidates], k1, b)
    for c, s in zip(candidates, scores):
        c.score = s

    ordered = sorted(candidates, key=lambda c: (-c.score, c.vector_rank))
    for i, c in enumerate(ordered, 1):
        c.rerank_rank = i
    return ordered[:top_k]


# ---------------------------------------------------------------------------
# 指标（和 Unit 25 同口径，方便横向比）
# ---------------------------------------------------------------------------
def recall_at_k(ids: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    return len(set(ids[:k]) & relevant) / len(relevant)


def mrr(ids: list[str], relevant: set[str]) -> float:
    for i, d in enumerate(ids, 1):
        if d in relevant:
            return 1.0 / i
    return 0.0


def ndcg_at_k(ids: list[str], relevant: set[str], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 1) for i, d in enumerate(ids[:k], 1) if d in relevant)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant), k) + 1))
    return dcg / ideal if ideal else 0.0
