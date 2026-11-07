"""Unit 87 · 面试 LLM+RAG：题库 + 自测。

核心认知：面试准备 = 题库覆盖 × 能用自己的话讲出来。
本单元提供 LLM+RAG 高频题，自检验证「每道题都有要点、覆盖核心话题」。
"""

from __future__ import annotations

BANK = [
    {"topic": "token", "q": "token 是什么？为什么中文更费 token？", "points": ["最小单位", "BPE", "中文"]},
    {"topic": "temperature", "q": "temperature 的作用？为什么不用 greedy？", "points": ["采样", "多样性", "重复"]},
    {"topic": "rag", "q": "RAG 的完整流程？", "points": ["检索", "拼上下文", "生成", "引用"]},
    {"topic": "threshold", "q": "RAG 为什么要在生成前做阈值判定？", "points": ["拒答", "幻觉", "可测"]},
    {"topic": "hybrid", "q": "混合检索是什么？RRF 为什么稳？", "points": ["BM25", "向量", "排名融合"]},
    {"topic": "eval", "q": "RAG 怎么评测？误收和误拒分别是什么？", "points": ["Recall", "准确率", "双侧"]},
    {"topic": "context", "q": "上下文窗口不够怎么办？", "points": ["截断", "摘要", "检索"]},
    {"topic": "embedding", "q": "embedding 是什么？和 token 什么关系？", "points": ["向量", "语义", "映射"]},
]


def check_bank(bank: list[dict]) -> dict:
    topics = [b["topic"] for b in bank]
    return {
        "count": len(bank),
        "topics": topics,
        "all_have_points": all(len(b.get("points", [])) >= 3 for b in bank),
        "no_dup": len(topics) == len(set(topics)),
    }


def self_quiz(bank: list[dict], answers: dict[str, str]) -> dict:
    """模拟自测：回答里覆盖要点才计分。"""
    scores = {}
    for b in bank:
        ans = answers.get(b["topic"], "")
        hit = sum(1 for p in b["points"] if p in ans)
        scores[b["topic"]] = hit / len(b["points"])
    return scores


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

    print("Unit 87 · 面试 LLM+RAG 自检\n")
    r = check_bank(BANK)
    check("题库 8 题", r["count"] == 8, str(r["count"]))
    check("每题至少 3 个要点", r["all_have_points"])
    check("话题不重复", r["no_dup"])
    check("覆盖核心话题", all(t in r["topics"] for t in ["token", "rag", "eval", "embedding"]))

    # 自测：一份好答案应拿满分
    good = {b["topic"]: " ".join(b["points"]) for b in BANK}
    scores = self_quiz(BANK, good)
    check("满分答案全对", all(s == 1.0 for s in scores.values()), str(scores))
    # 空答案得 0
    empty = self_quiz(BANK, {})
    check("空答案得 0", all(s == 0.0 for s in empty.values()))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(run_selfcheck())
