"""Unit 87 · 面试 LLM+RAG：30 题题库 + 自测打分。

计划要求：自问自答 30 题，录音复盘。
本单元给齐 30 道高频题，每题 3+ 要点；自测按要点覆盖打分，低于阈值要重讲。
"""

from __future__ import annotations

from fastapi import FastAPI

BANK = [
    # ---- Token / 采样（1-6）----
    {"topic": "token", "q": "token 是什么？为什么中文更费 token？", "points": ["最小单位", "BPE", "中文"]},
    {"topic": "bpe", "q": "BPE 怎么训练？", "points": ["字符出发", "合并高频", "词表"]},
    {"topic": "context", "q": "上下文窗口是什么？超了怎么办？", "points": ["窗口", "截断", "摘要"]},
    {"topic": "temperature", "q": "temperature 的作用？为什么不用 greedy？", "points": ["采样", "多样性", "重复"]},
    {"topic": "topp", "q": "top-p 和 temperature 的区别？", "points": ["累积概率", "截断", "组合"]},
    {"topic": "cost", "q": "token 成本怎么算？为什么 output 更贵？", "points": ["input", "output", "并行"]},
    # ---- Embedding / 检索（7-12）----
    {"topic": "embedding", "q": "embedding 是什么？和 token 什么关系？", "points": ["向量", "语义", "映射"]},
    {"topic": "cosine", "q": "为什么用余弦相似度不用欧氏距离？", "points": ["方向", "长度", "归一"]},
    {"topic": "vector-db", "q": "向量库（pgvector）怎么选索引？", "points": ["HNSW", "IVFFlat", "权衡"]},
    {"topic": "recall", "q": "Recall@K 和 MRR@K 分别衡量什么？", "points": ["命中", "排名", "截断"]},
    {"topic": "hybrid", "q": "混合检索是什么？RRF 为什么稳？", "points": ["BM25", "向量", "排名融合"]},
    {"topic": "rerank", "q": "Reranker 干什么？双塔和交叉编码器区别？", "points": ["精排", "Top-K", "交互"]},
    # ---- RAG 流程（13-20）----
    {"topic": "rag", "q": "RAG 的完整流程？", "points": ["检索", "拼上下文", "生成", "引用"]},
    {"topic": "chunking", "q": "切块怎么切？overlap 为什么需要？", "points": ["固定", "递归", "语义", "重叠"]},
    {"topic": "threshold", "q": "RAG 为什么要在生成前做阈值判定？", "points": ["拒答", "幻觉", "可测"]},
    {"topic": "citation", "q": "引用怎么保证准确？", "points": ["编号", "对齐", "校验"]},
    {"topic": "hallucination", "q": "RAG 还会幻觉吗？怎么防？", "points": ["阈值", "引用", "输出校验"]},
    {"topic": "long-context", "q": "长上下文能替代 RAG 吗？", "points": ["成本", "衰减", "检索"]},
    {"topic": "multi-hop", "q": "多跳问题怎么处理？", "points": ["分解", "多轮检索", "合并"]},
    {"topic": "query-rewrite", "q": "查询改写有什么用？", "points": ["补全", "同义", "多查询"]},
    # ---- 评测（21-26）----
    {"topic": "eval", "q": "RAG 怎么评测？误收和误拒分别是什么？", "points": ["Recall", "准确率", "双侧"]},
    {"topic": "faithfulness", "q": "忠实度（faithfulness）怎么衡量？", "points": ["逐字", "可追溯", "引用"]},
    {"topic": "gold", "q": "没有金标准怎么做评测？", "points": ["人工标注", "小样本", "抽检"]},
    {"topic": "llm-judge", "q": "LLM-as-judge 可靠吗？怎么校准？", "points": ["rubric", "meta-eval", "一致性"]},
    {"topic": "regression", "q": "改 prompt 后怎么防退化？", "points": ["评测集", "指标", "护栏"]},
    {"topic": "ablation", "q": "消融实验怎么做？", "points": ["单变量", "对比", "结论"]},
    # ---- 工程与安全（27-30）----
    {"topic": "injection", "q": "Prompt Injection 怎么防？", "points": ["数据指令分离", "检测", "输出过滤"]},
    {"topic": "leak", "q": "模型输出可能泄露什么？怎么防？", "points": ["PII", "脱敏", "输出审查"]},
    {"topic": "cache", "q": "LLM 响应怎么缓存？key 怎么设计？", "points": ["key", "参数", "命中率"]},
    {"topic": "streaming", "q": "流式输出怎么实现？SSE 还是 WebSocket？", "points": ["SSE", "增量", "首字延迟"]},
]

REQUIRED_TOPICS = ["token", "embedding", "rag", "chunking", "eval", "injection", "cache"]


def check_bank(bank: list[dict]) -> dict:
    topics = [b["topic"] for b in bank]
    return {
        "count": len(bank),
        "topics": topics,
        "all_have_points": all(len(b.get("points", [])) >= 3 for b in bank),
        "no_dup_topic": len(topics) == len(set(topics)),
        "no_dup_question": len({b["q"] for b in bank}) == len(bank),
        "covered": all(t in topics for t in REQUIRED_TOPICS),
    }


def self_quiz(bank: list[dict], answers: dict[str, str], pass_line: float = 0.6) -> dict:
    """按要点覆盖打分；返回每题得分与不及格清单。"""
    scores = {}
    for b in bank:
        ans = answers.get(b["topic"], "")
        hit = sum(1 for p in b["points"] if p in ans)
        scores[b["topic"]] = round(hit / len(b["points"]), 2)
    weak = [t for t, s in scores.items() if s < pass_line]
    avg = sum(scores.values()) / len(scores) if scores else 0.0
    return {"scores": scores, "weak": weak, "avg": round(avg, 2), "passed": len(weak) == 0}


app = FastAPI(title="Unit 87 · 面试 LLM+RAG", version="0.1.0")


@app.get("/bank", summary="30 题题库")
async def bank(topic: str | None = None) -> dict:
    items = [b for b in BANK if topic is None or b["topic"] == topic]
    return {"count": len(items), "items": items}


@app.get("/quiz/check", summary="题库自检（覆盖/去重/要点完整）")
async def quiz_check() -> dict:
    return check_bank(BANK)


@app.get("/", include_in_schema=False)
async def index() -> dict:
    return {"hint": "/bank 看题库；/bank?topic=rag 过滤；/quiz/check 题库自检"}


# ---------------------------------------------------------------------------
# 独立自检
# ---------------------------------------------------------------------------
def run_selfcheck() -> int:
    from fastapi.testclient import TestClient

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 87 · 面试 LLM+RAG（30 题）自检\n")
    r = check_bank(BANK)

    check("题库 30 题", r["count"] == 30, str(r["count"]))
    check("每题至少 3 个要点", r["all_have_points"])
    check("话题不重复", r["no_dup_topic"])
    check("题目不重复", r["no_dup_question"])
    check("覆盖核心话题", r["covered"])
    check("包含评测类题目", any(t == "eval" for t in r["topics"]))
    check("包含安全类题目", any(t == "injection" for t in r["topics"]))

    # 自测：满分答案
    good = {b["topic"]: " ".join(b["points"]) for b in BANK}
    qr = self_quiz(BANK, good)
    check("满分答案全部通过", qr["passed"] is True)
    check("满分平均 1.0", qr["avg"] == 1.0, str(qr["avg"]))

    # 自测：空答案
    qr2 = self_quiz(BANK, {})
    check("空答案全部不及格", len(qr2["weak"]) == 30)

    # 自测：部分答案能指出弱者
    partial = {b["topic"]: b["points"][0] for b in BANK}  # 每题只答 1 个要点
    qr3 = self_quiz(BANK, partial)
    check("只答一个要点不及格", len(qr3["weak"]) > 0)
    check("部分答案平均低于 0.6", qr3["avg"] < 0.6, str(qr3["avg"]))

    # HTTP
    c = TestClient(app)
    r1 = c.get("/bank")
    check("HTTP /bank 200", r1.status_code == 200)
    check("HTTP 返回 30 题", r1.json()["count"] == 30)
    r2 = c.get("/bank", params={"topic": "rag"})
    check("HTTP 按话题过滤", r2.json()["count"] >= 1 and all(i["topic"] == "rag" for i in r2.json()["items"]))
    r3 = c.get("/quiz/check")
    check("HTTP /quiz/check 通过", r3.json()["count"] == 30)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())