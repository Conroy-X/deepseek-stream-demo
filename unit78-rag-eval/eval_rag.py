"""Unit 78 · RAG 评测收口：端到端跑一遍「检索 → 生成 → 评测」。

核心认知：
  1. 阶段六的收口单元：把 Unit 19（Recall@K）、Unit 70（评测框架）、
     Unit 76/77（混合检索+重排）串成一条端到端评测流水线。
  2. 评测要回答三个问题：
     - 检索找对了吗？（Recall@K）
     - 答案答对了吗？（关键词覆盖）
     - 该拒的拒了吗？（误收/误拒）
  3. 流水线是改 prompt / 换检索 / 换模型的「防退化护栏」：每次改动跑一遍，
     三个指标任何一个掉下去都要停下来查。

本单元用小语料 + 规则检索 + 规则生成，把端到端评测离线跑通。
"""

from __future__ import annotations

import re

# 小知识库：id → 文本
KB = {
    1: "退款金额原路退回，通常 3-5 个工作日到账。",
    2: "年假需提前 3 个工作日在系统提交申请。",
    3: "密码要求 12 位以上，每 90 天更换一次。",
}

# 评测集：问题 → 金标准（相关文档 id + 答案关键词 + 是否该拒）
EVAL_SET = [
    {"q": "退款多久到账", "gold_doc": 1, "gold_kw": ["退款", "3-5"], "should_refuse": False},
    {"q": "年假怎么申请", "gold_doc": 2, "gold_kw": ["提前", "系统"], "should_refuse": False},
    {"q": "密码多久换", "gold_doc": 3, "gold_kw": ["90", "天"], "should_refuse": False},
    {"q": "今天天气怎么样", "gold_doc": None, "gold_kw": [], "should_refuse": True},
]


def retrieve(query: str, k: int = 1) -> list[int]:
    """规则检索：词重叠打分，返回 top-k 文档 id（可能为空）。"""
    q_terms = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", query))
    scored = []
    for doc_id, text in KB.items():
        d_terms = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text))
        overlap = len(q_terms & d_terms) / max(1, len(q_terms))
        if overlap >= 0.3:  # 阈值挡住「天气」与「天」这类单字误匹配
            scored.append((doc_id, overlap))
    scored.sort(key=lambda kv: -kv[1])
    return [i for i, _ in scored[:k]]


def generate(query: str, doc_ids: list[int], threshold_score: float = 0.0) -> tuple[str, bool]:
    """规则生成：命中就摘抄文档，没命中就拒答。"""
    if not doc_ids:
        return "资料里没有相关内容。", True
    return KB[doc_ids[0]], False


def recall_at_k(predictions: list[list[int]], golds: list[int | None], k: int = 1) -> float:
    """Recall@K：gold 文档出现在 top-k 预测里的比例。"""
    hits = 0
    total = 0
    for pred, gold in zip(predictions, golds):
        if gold is None:
            continue
        total += 1
        if gold in pred[:k]:
            hits += 1
    return hits / total if total else 0.0


def answer_accuracy(answers: list[str], gold_kws: list[list[str]]) -> float:
    """答案关键词覆盖率的平均。"""
    n = 0
    acc = 0.0
    for ans, kws in zip(answers, gold_kws):
        if not kws:
            continue
        n += 1
        acc += sum(1 for kw in kws if kw in ans) / len(kws)
    return acc / n if n else 0.0


def evaluate_pipeline(eval_set: list[dict], k: int = 1) -> dict:
    preds = []
    golds = []
    answers = []
    refused = []
    for item in eval_set:
        doc_ids = retrieve(item["q"], k)
        answer, did_refuse = generate(item["q"], doc_ids)
        preds.append(doc_ids)
        golds.append(item["gold_doc"])
        answers.append(answer)
        refused.append((item["should_refuse"], did_refuse))
    # 误收/误拒
    false_accept = sum(1 for (g, p) in refused if g and not p)
    false_refuse = sum(1 for (g, p) in refused if not g and p)
    return {
        "recall_at_k": round(recall_at_k(preds, golds, k), 4),
        "answer_accuracy": round(answer_accuracy(answers, [i["gold_kw"] for i in eval_set]), 4),
        "false_accept": false_accept,
        "false_refuse": false_refuse,
    }


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

    print("Unit 78 · RAG 评测收口 自检\n")

    # ---- 检索 ----
    r = retrieve("退款多久到账")
    check("检索命中退款文档", 1 in r, str(r))
    check("检索空查询为空", retrieve("xyzxyz") == [])

    # ---- 生成 ----
    ans, refused = generate("x", [1])
    check("命中时生成摘抄", "退款" in ans and refused is False)
    ans2, refused2 = generate("x", [])
    check("未命中时拒答", "没有相关" in ans2 and refused2 is True)

    # ---- 端到端评测 ----
    report = evaluate_pipeline(EVAL_SET)
    check("Recall@1 = 1.0（三条都命中）", report["recall_at_k"] == 1.0, str(report))
    check("答案准确率 = 1.0", report["answer_accuracy"] == 1.0, str(report))
    check("误收 = 0", report["false_accept"] == 0, str(report))
    check("误拒 = 0", report["false_refuse"] == 0, str(report))

    # ---- 故意制造退化：检索 k=0 时指标崩 ----
    report_bad = evaluate_pipeline(EVAL_SET, k=0)
    check("检索退化后 Recall 降", report_bad["recall_at_k"] < 1.0, str(report_bad))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
