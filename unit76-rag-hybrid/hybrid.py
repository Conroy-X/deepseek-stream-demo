"""Unit 76 · RAG 优化：混合检索（BM25 字面 + 向量语义）。

核心认知：
  1. 单一检索各有盲区：
     - 纯向量：语义好，但精确关键词（型号、法条号）会漏
     - 纯 BM25：关键词好，但换种说法就找不到
  2. 混合检索 = 两条路都跑，再把结果**融合**。
  3. 两种融合方式：
     - 加权分数融合：score = α·bm25 + (1-α)·vector（要归一化）
     - RRF（倒数排名融合）：score = Σ 1/(k+rank)，不依赖绝对分数，更稳

本单元实现 BM25 简化版 + 余弦检索 + RRF 融合，离线可测。
"""

from __future__ import annotations

import math
import re
from collections import Counter

# 小语料
DOCS = [
    "苹果 iPhone 15 Pro 支持 USB-C 充电",
    "苹果手机拍照效果很好",
    "香蕉是一种富含钾的水果",
    "USB-C 数据线兼容多种设备",
    "手机充电器功率选择指南",
]


def tokenize(text: str) -> list[str]:
    """中英混合：英文按词（含数字），中文按字。"""
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())


def bm25(query: str, docs: list[str], k1: float = 1.2, b: float = 0.75) -> list[tuple[int, float]]:
    """简化 BM25：按词频和文档长度打分。"""
    q_terms = tokenize(query)
    tokenized = [tokenize(d) for d in docs]
    N = len(docs)
    avgdl = sum(len(t) for t in tokenized) / max(1, N)
    df = Counter()
    for t in tokenized:
        df.update(set(t))
    scores = []
    for i, terms in enumerate(tokenized):
        score = 0.0
        tf = Counter(terms)
        dl = len(terms)
        for term in q_terms:
            if term not in tf:
                continue
            n = df.get(term, 0)
            idf = math.log((N - n + 0.5) / (n + 0.5) + 1.0)
            score += idf * (tf[term] * (k1 + 1)) / (tf[term] + k1 * (1 - b + b * dl / avgdl))
        scores.append((i, score))
    return scores


def vector_score(query: str, docs: list[str]) -> list[tuple[int, float]]:
    """假向量检索：用词重叠模拟语义相似度（教学用；真实是 embedding 余弦）。"""
    q_terms = set(tokenize(query))
    out = []
    for i, d in enumerate(docs):
        d_terms = set(tokenize(d))
        overlap = len(q_terms & d_terms) / max(1, len(q_terms | d_terms))
        out.append((i, overlap))
    return out


def reciprocal_rank_fusion(ranked_lists: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """RRF：score(doc) = Σ 1/(k + rank)。rank 从 1 开始。"""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, doc_id in enumerate(ranked, 1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: -kv[1])


def hybrid_search(query: str, docs: list[str], k: int = 60) -> list[tuple[int, str, float]]:
    bm25_scores = sorted(bm25(query, docs), key=lambda kv: -kv[1])
    vec_scores = sorted(vector_score(query, docs), key=lambda kv: -kv[1])
    # 只取分数 > 0 的进 RRF
    bm_ranked = [i for i, s in bm25_scores if s > 0]
    vec_ranked = [i for i, s in vec_scores if s > 0]
    fused = reciprocal_rank_fusion([bm_ranked, vec_ranked], k)
    return [(doc_id, docs[doc_id], round(score, 6)) for doc_id, score in fused]


# ---------------------------------------------------------------------------
# 独立自检
# ---------------------------------------------------------------------------
def run_selfcheck() -> int:
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 76 · 混合检索 自检\n")

    # ---- BM25：精确关键词 ----
    bm = bm25("USB-C", DOCS)
    top_bm = max(bm, key=lambda kv: kv[1])
    check("BM25 命中精确关键词 USB-C", top_bm[0] in (0, 3), f"doc={top_bm[0]}")

    # ---- 向量：语义（拍照 → 相机相关）----
    vec = vector_score("拍照", DOCS)
    top_vec = max(vec, key=lambda kv: kv[1])
    check("向量检索把「拍照」关联到「拍照效果」", top_vec[0] == 1, f"doc={top_vec[0]}")

    # ---- 混合：两个维度都能捞到 ----
    hybrid = hybrid_search("USB-C 充电", DOCS)
    top_ids = [i for i, _, _ in hybrid[:3]]
    check("混合检索 USB-C 命中充电相关", any(i in (0, 4) for i in top_ids), str(top_ids))
    hybrid2 = hybrid_search("拍照", DOCS)
    top2 = [i for i, _, _ in hybrid2[:2]]
    check("混合检索拍照仍能命中语义相关", 1 in top2, str(top2))

    # ---- RRF 性质 ----
    rrf = reciprocal_rank_fusion([[0, 1, 2], [2, 0, 1]])
    check("RRF 排第一的是双列表都靠前的", rrf[0][0] in (0, 2), str(rrf[0]))
    total = sum(s for _, s in rrf)
    check("RRF 分数为正", all(s > 0 for _, s in rrf))
    check("RRF 输出按分数降序", all(rrf[i][1] >= rrf[i + 1][1] for i in range(len(rrf) - 1)))

    # ---- 空查询不崩 ----
    check("空查询返回空", hybrid_search("", DOCS) == [])

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
