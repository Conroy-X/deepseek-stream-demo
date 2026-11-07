"""Unit 77 · Reranker：检索后重排。

核心认知：
  1. 为什么需要重排：初检（BM25/向量）要快，用的是轻量模型；
     但轻量模型排序不够准。重排用**更精细的模型**对 Top-K 再排一次。
  2. 流程：初检 Top-50 → reranker 精排 → 取 Top-10 进 prompt。
  3. 交叉编码器（cross-encoder）比双编码器准：把 query 和 doc 拼一起
     输入模型打分，而不是各自编码再算相似度——代价是慢，所以只对 Top-K 用。

本单元用规则模拟 reranker（query 与 doc 的词序重叠），演示「重排让更相关的进前 10」。
"""

from __future__ import annotations

import re

DOCS = [
    "苹果手机支持 USB-C 充电",
    "USB-C 数据线兼容苹果手机",
    "香蕉富含钾元素",
    "手机充电器功率选择指南",
    "苹果 iPhone 15 Pro 使用 USB-C 接口充电",
]


def tokenize(text: str) -> list[str]:
    """中英混合：英文按词（含数字），中文按字。"""
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())


def initial_rank(query: str, docs: list[str]) -> list[int]:
    """初检：简单词重叠打分（模拟 BM25/向量）。"""
    q = set(tokenize(query))
    scored = []
    for i, d in enumerate(docs):
        overlap = len(q & set(tokenize(d)))
        scored.append((i, overlap))
    scored.sort(key=lambda kv: -kv[1])
    return [i for i, _ in scored]


def cross_encoder_score(query: str, doc: str) -> float:
    """模拟交叉编码器：考虑词序（有序 bigram 重叠）。真实是模型打分。"""
    q_terms = tokenize(query)
    d_terms = tokenize(doc)
    # 词重叠
    q_set, d_set = set(q_terms), set(d_terms)
    overlap = len(q_set & d_set) / max(1, len(q_set))
    # 词序重叠（query 里的相邻对在 doc 里也相邻出现）
    q_bigrams = set(zip(q_terms, q_terms[1:]))
    d_bigrams = set(zip(d_terms, d_terms[1:]))
    order = len(q_bigrams & d_bigrams) / max(1, len(q_bigrams))
    return overlap * 0.5 + order * 0.5


def rerank(query: str, docs: list[str], candidate_ids: list[int], top_n: int = 3) -> list[tuple[int, str, float]]:
    """重排 pipeline：只对候选重打分，取 top_n。"""
    scored = [(i, docs[i], cross_encoder_score(query, docs[i])) for i in candidate_ids]
    scored.sort(key=lambda kv: -kv[2])
    return scored[:top_n]


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

    print("Unit 77 · Reranker 自检\n")

    query = "苹果手机 USB-C 充电"
    initial = initial_rank(query, DOCS)
    check("初检返回所有文档", len(initial) == len(DOCS))

    # 重排只处理候选（模拟初检 Top-50）
    top_candidates = initial[:4]
    results = rerank(query, DOCS, top_candidates, top_n=3)
    check("重排输出 top_n 条", len(results) == 3, str(len(results)))
    check("结果按分数降序", all(results[i][2] >= results[i + 1][2] for i in range(len(results) - 1)))

    # 交叉编码器应把「精确短语匹配」排第一
    best_id = results[0][0]
    check("重排后最相关的排第一（精确短语）", best_id == 0, f"best={best_id}")

    # 词序感知：交叉编码器对「顺序正确」的打分高于「顺序颠倒」
    s_exact = cross_encoder_score("充电 USB-C", "充电 USB-C")
    s_reversed = cross_encoder_score("充电 USB-C", "USB-C 充电")
    check("交叉编码器感知词序", s_exact > s_reversed, f"{s_exact:.3f} vs {s_reversed:.3f}")

    # top_n 截断生效
    check("top_n 截断生效", len(rerank(query, DOCS, initial, top_n=1)) == 1)

    # 空候选
    check("空候选返回空", rerank(query, DOCS, [], top_n=3) == [])

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
