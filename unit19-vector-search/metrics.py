"""Unit 19 · 检索指标：Recall@K、MRR@K 与无答案题误接受率。

本单元的每道题只有一条 gold 证据，因此这里的 Recall@K 实际是单证据
Hit Rate@K：Top-K 中有没有正确证据。多条 gold 的 Recall 还应除以 gold 总数，
两种口径不能混用。

MRR@K 也必须截断到 K：
    rank <= K → 本题 RR = 1 / rank
    rank > K 或未命中 → 本题 RR = 0
否则把 Top-10 的完整排序反复用于不同 K，会得到一个恒定值，那不是 MRR@K。
"""

from __future__ import annotations


def recall_at_k(ranked_ids: list[str], gold_ids: list[str], k: int) -> float:
    """单题单证据 Recall@K：Top-K 命中任一 gold 为 1，否则为 0。"""
    if k <= 0:
        raise ValueError(f"k 必须 > 0，收到 {k}")
    if not gold_ids:
        raise ValueError("gold_ids 不能为空；无答案题应单独评误接受率")
    gold = set(gold_ids)
    return 1.0 if any(cid in gold for cid in ranked_ids[:k]) else 0.0


def first_relevant_rank(ranked_ids: list[str], gold_ids: list[str]) -> int | None:
    """返回首个 gold 在候选列表中的 1 基排名；未命中返回 None。"""
    gold = set(gold_ids)
    for i, cid in enumerate(ranked_ids, 1):
        if cid in gold:
            return i
    return None


def reciprocal_rank(ranked_ids: list[str], gold_ids: list[str]) -> float:
    """完整候选列表上的 RR；比较不同 K 时应使用 reciprocal_rank_at_k。"""
    rank = first_relevant_rank(ranked_ids, gold_ids)
    return 0.0 if rank is None else 1.0 / rank


def reciprocal_rank_at_k(ranked_ids: list[str], gold_ids: list[str], k: int) -> float:
    """单题 RR@K：正确证据排在 K 之后时按未命中处理。"""
    if k <= 0:
        raise ValueError(f"k 必须 > 0，收到 {k}")
    return reciprocal_rank(ranked_ids[:k], gold_ids)


def evaluate(rows: list[dict], k: int) -> dict:
    """评测可回答题，所有返回指标都使用同一个 K。

    rows: [{"q": str, "ranked": [id...], "gold": [id...]}]
    ranked 可以比 K 长；Recall、MRR 和命中题数都只看前 K 条。
    details.rank 保留其在完整 ranked 中的位置，便于分析“差一点命中”的题。
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
                "rr": reciprocal_rank_at_k(r["ranked"], r["gold"], k),
                "top1": r["ranked"][0] if r["ranked"] else None,
            }
        )
    n = len(details)
    return {
        "k": k,
        "questions": n,
        "recall_at_k": round(sum(d["recall_at_k"] for d in details) / n, 4),
        "mrr_at_k": round(sum(d["rr"] for d in details) / n, 4),
        "hits_at_k": sum(d["recall_at_k"] == 1.0 for d in details),
        "details": details,
    }


def evaluate_unanswerable(rows: list[dict]) -> dict:
    """评测无答案题：检索仍返回结果，记为一次检索层误接受。

    这里的“误接受”只表示系统拿到了候选证据，不等于最终 LLM 一定误答；
    但它说明无法再用“结果为空”作为可靠的拒答信号。
    rows: [{"q": str, "ranked": [id...]}]
    """
    if not rows:
        raise ValueError("无答案评测集为空")
    accepted = sum(bool(r["ranked"]) for r in rows)
    n = len(rows)
    return {
        "questions": n,
        "accepted": accepted,
        "rejected": n - accepted,
        "false_acceptance_rate": round(accepted / n, 4),
    }
