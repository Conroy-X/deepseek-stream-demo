"""Unit 70 · Evaluation：RAG 与 Agent 的离线评测。

核心认知：
  1. 评测三要素：**金标准（gold）、离线可复现、指标可解释**。
     没有金标准的评测是「我看着觉得不错」，不叫评测。
  2. RAG 要评两个方向：
     - 检索质量：Recall@K（Unit 19 已做）
     - 答案质量：正确性 + 拒答双边（该拒的拒了、不该拒的没拒）
  3. Agent 要评：任务成功率、工具调用正确性、步数效率。
  4. LLM-as-judge 用模型打分，必须给 rubric（评分标准），否则分数不可复现。

本单元把评测全部做成确定性的规则 + 模拟 judge，离线可测。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 答案质量
# ---------------------------------------------------------------------------
def score_exact(pred: str, gold: str) -> float:
    """完全匹配 1 分。最严格，适合答案很短且固定。"""
    return 1.0 if pred.strip() == gold.strip() else 0.0


def score_keywords(pred: str, gold_keywords: list[str]) -> float:
    """按关键词覆盖率打分。适合「要点必须齐全」的场景。"""
    if not gold_keywords:
        return 0.0
    hit = sum(1 for kw in gold_keywords if kw in pred)
    return hit / len(gold_keywords)


def judge_answer(pred: str, gold: str, rubric: dict) -> dict:
    """模拟 LLM-as-judge：确定性规则给分，返回 score + reason。

    真实 judge 是模型调用，但评分逻辑要先能被规则验证——
    这是「评测的评测」（meta-eval）的前提。
    """
    reasons = []
    score = 0.0
    # 正确性：gold 里的关键词出现比例
    kw_score = score_keywords(pred, rubric.get("keywords", [gold]))
    score += kw_score * rubric.get("w_correct", 0.7)
    if kw_score < 1.0:
        reasons.append(f"缺要点（{kw_score:.0%} 覆盖）")
    # 幻觉：预测里出现 rubric 标明的禁用词
    forbidden = rubric.get("forbidden", [])
    hallucinated = [w for w in forbidden if w in pred]
    if hallucinated:
        score = 0.0
        reasons.append(f"出现禁用内容 {hallucinated}")
    # 引用：有 [n] 加分
    if rubric.get("require_citation") and "[" in pred and "]" in pred:
        score += 0.1
    else:
        score -= 0.0
    return {"score": round(min(1.0, max(0.0, score)), 2), "reason": "; ".join(reasons) or "ok"}


# ---------------------------------------------------------------------------
# 拒答双边：混淆矩阵
# ---------------------------------------------------------------------------
@dataclass
class RefusalStats:
    tp: int = 0  # 该拒的拒了
    fp: int = 0  # 不该拒的拒了（误杀）
    tn: int = 0  # 不该拒的答了
    fn: int = 0  # 该拒的答了（漏网）

    def as_dict(self) -> dict:
        return {
            "tp": self.tp, "fp": self.fp, "tn": self.tn, "fn": self.fn,
            "false_accept_rate": round(self.fn / (self.tp + self.fn), 4) if (self.tp + self.fn) else 0.0,
            "false_refuse_rate": round(self.fp / (self.fp + self.tn), 4) if (self.fp + self.tn) else 0.0,
        }


def evaluate_refusal(predictions: list[tuple[bool, bool]]) -> RefusalStats:
    """输入 [(gold_should_refuse, pred_refused), ...]。"""
    s = RefusalStats()
    for gold_refuse, pred_refused in predictions:
        if gold_refuse and pred_refused:
            s.tp += 1
        elif gold_refuse and not pred_refused:
            s.fn += 1
        elif not gold_refuse and pred_refused:
            s.fp += 1
        else:
            s.tn += 1
    return s


# ---------------------------------------------------------------------------
# Agent 评测
# ---------------------------------------------------------------------------
@dataclass
class AgentRun:
    task_id: str
    success: bool
    tool_calls: list[str]          # 实际调的工具序列
    expected_tools: list[str]      # 金标准工具序列（顺序无关，集合匹配）
    steps: int


@dataclass
class AgentEval:
    runs: list[AgentRun] = field(default_factory=list)

    def add(self, run: AgentRun) -> None:
        self.runs.append(run)

    def summary(self) -> dict:
        if not self.runs:
            return {"runs": 0}
        n = len(self.runs)
        success = sum(1 for r in self.runs if r.success) / n
        # 工具正确：成功且工具集合与期望一致
        tool_ok = sum(
            1 for r in self.runs
            if r.success and set(r.tool_calls) == set(r.expected_tools)
        ) / n
        # 步数效率：成功任务里，步数超过期望工具数就算冗余
        redundant = sum(
            1 for r in self.runs if r.success and r.steps > len(r.expected_tools)
        ) / max(1, sum(1 for r in self.runs if r.success))
        return {
            "runs": n,
            "success_rate": round(success, 4),
            "tool_accuracy": round(tool_ok, 4),
            "redundant_step_rate": round(redundant, 4),
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

    print("Unit 70 · Evaluation 自检\n")

    # ---- 答案质量 ----
    check("exact 全对", score_exact("3-5 个工作日", "3-5 个工作日") == 1.0)
    check("exact 全错", score_exact("马上到", "3-5 个工作日") == 0.0)
    check("关键词覆盖率", abs(score_keywords("退款 3-5 个工作日", ["退款", "3-5"]) - 1.0) < 1e-9)
    check("缺一个关键词扣分", score_keywords("3-5 个工作日", ["退款", "3-5"]) == 0.5)

    # ---- 模拟 judge ----
    rubric = {"keywords": ["3-5", "原路退回"], "forbidden": ["无法保证", "大概"], "require_citation": True}
    j1 = judge_answer("退款原路退回，通常 3-5 个工作日到账 [1]", "gold", rubric)
    check("judge 给满分", j1["score"] >= 0.7, str(j1))
    j2 = judge_answer("大概无法保证到账时间", "gold", rubric)
    check("judge 对幻觉清零", j2["score"] == 0.0, str(j2))
    j3 = judge_answer("退款 3-5 个工作日", "gold", rubric)
    check("judge 缺要点扣分", j3["score"] < 0.7, str(j3))

    # ---- 拒答双边 ----
    preds = [
        (True, True),   # 该拒，拒了 → TP
        (True, False),  # 该拒，答了 → FN（最危险）
        (False, True),  # 不该拒，拒了 → FP（误杀）
        (False, False), # 不该拒，答了 → TN
        (True, True),
        (False, False),
    ]
    s = evaluate_refusal(preds)
    check("TP=2", s.tp == 2, str(s.as_dict()))
    check("FN=1", s.fn == 1)
    check("FP=1", s.fp == 1)
    check("TN=2", s.tn == 2)
    d = s.as_dict()
    check("false_accept=1/3", abs(d["false_accept_rate"] - 1 / 3) < 1e-3, str(d["false_accept_rate"]))
    check("false_refuse=1/3", abs(d["false_refuse_rate"] - 1 / 3) < 1e-3, str(d["false_refuse_rate"]))

    # ---- Agent 评测 ----
    ae = AgentEval()
    ae.add(AgentRun("t1", True, ["search", "fetch"], ["search", "fetch"], steps=2))
    ae.add(AgentRun("t2", True, ["search"], ["search"], steps=3))          # 步数冗余
    ae.add(AgentRun("t3", False, ["search"], ["search", "fetch"], steps=1))  # 失败
    ae.add(AgentRun("t4", True, ["search"], ["fetch"], steps=1))            # 工具错误但成功
    m = ae.summary()
    check("runs=4", m["runs"] == 4)
    check("success=3/4", abs(m["success_rate"] - 0.75) < 1e-3, str(m["success_rate"]))
    check("tool_accuracy=2/4", abs(m["tool_accuracy"] - 0.5) < 1e-3, str(m["tool_accuracy"]))
    check("redundant=1/3", abs(m["redundant_step_rate"] - 1 / 3) < 1e-3, str(m["redundant_step_rate"]))

    # ---- 空评测不崩 ----
    check("空 agent eval", AgentEval().summary() == {"runs": 0})

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
