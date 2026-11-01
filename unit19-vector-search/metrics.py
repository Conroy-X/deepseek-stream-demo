"""Unit 19 · 检索指标：Recall@K 和 MRR。

两个指标回答两个不同的问题：

    Recall@K   「K 个结果里有没有正确的」        → 能不能用（够不够喂给模型）
    MRR        「正确的排在第几位」               → 好不好用（模型会不会被噪声带偏）

只有 Recall@K 时，第 1 名命中和第 5 名命中算一样好；
补上 MRR 才能区分这两种情况，而它们对 RAG 的实际效果差别很大：
Top-1 命中时上下文里全是证据，Top-5 命中时前面塞了 4 段噪声。

口径必须写死，否则数字不可比：
  - 「相关」= 该 chunk 的 id 在 gold 列表里（不是「看起来相关」）
  - Recall@K 的 K 大于返回条数时，按实际返回条数算（不补零、不惩罚）
  - MRR 未命中记 0，不是记 1/K_max
"""

from __future__ import annotations


def recall_at_k(ranked_ids: list[str], gold_ids: list[str], k: int) -> float:
    """单题 Recall@K：Top-K 里命中任一 gold 就是 1，否则 0。

    这是 RAG 场景的标准口径（不是「命中几个」除以「gold 总数」）：
    只要有一段证据进了上下文，模型就有机会答对。
    """
    if k <= 0:
        raise ValueError(f"k 必须 > 0，收到 {k}")
    if not gold_ids:
        raise ValueError("gold_ids 不能为空，没有标准答案就没有 recall 可言")
    gold = set(gold_ids)
    return 1.0 if any(cid in gold for cid in ranked_ids[:k]) else 0.0


def first_relevant_rank(ranked_ids: list[str], gold_ids: list[str]) -> int | None:
    gold = set(gold_ids)
    for i, cid in enumerate(ranked_ids, 1):
        if cid in gold:
            return i
    return None


def reciprocal_rank(ranked_ids: list[str], gold_ids: list[str]) -> float:
    """1/rank，未命中 0。rank 从 1 开始 —— 从 0 开始会让 Top-1 命中变成 ZeroDivisionError。"""
    rank = first_relevant_rank(ranked_ids, gold_ids)
    return 0.0 if rank is None else 1.0 / rank


def evaluate(rows: list[dict], k: int) -> dict:
    """rows: [{"q": str, "ranked": [id...], "gold": [id...]}]

    一次算完 K 个值，避免「Recall 用一套判定、MRR 用另一套」——
    两套逻辑对不上的时候，数字会互相打架而你不知道信哪个。
    """
    if not rows:
        raise ValueError("评测集为空")
    details = []
    for r in rows:
        rank = first_relevant_rank(r["ranked"], r["gold"])
        details.append(
            {
                "q": r["q"],
                "rank": rank,
                "recall_at_k": recall_at_k(r["ranked"], r["gold"], k),
                "rr": reciprocal_rank(r["ranked"], r["gold"]),
                "top1": r["ranked"][0] if r["ranked"] else None,
            }
        )
    n = len(details)
    return {
        "k": k,
        "questions": n,
        "recall_at_k": round(sum(d["recall_at_k"] for d in details) / n, 4),
        "mrr": round(sum(d["rr"] for d in details) / n, 4),
        "hits": sum(1 for d in details if d["rank"]),
        "details": details,
    }
