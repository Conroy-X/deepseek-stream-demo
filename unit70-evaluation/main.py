"""Unit 70 · Evaluation：30 条评测集 + 检索命中率 + 忠实度 + 答案相关性。

计划要求：建 30 条评测集，跑检索命中率 + 忠实度 + 答案相关性。
本单元把这些指标全部实现成可离线复现的函数。

四个指标：
  1. 检索命中率 Recall@K —— 检索有没有找到该找的资料
  2. 忠实度 faithfulness —— 答案里的内容能不能在检索结果里找到（防幻觉）
  3. 答案相关性 relevance —— 答案有没有真的回答问题
  4. 拒答双侧 —— 该拒的拒了（TP）、不该拒的别拒（TN）

启动：uv run uvicorn main:app --port 8070
自检：uv run python main.py
"""

from __future__ import annotations

import re

from fastapi import FastAPI

# ---------------------------------------------------------------------------
# 知识库（10 段）
# ---------------------------------------------------------------------------
KB = {
    1: "退款金额原路退回，通常 3-5 个工作日到账。",
    2: "未发货订单可随时申请全额退款，1 个工作日内处理。",
    3: "虚拟商品一经激活不支持退款，未激活 7 天内可申请。",
    4: "年假入职满一年享 5 天，满三年 10 天，满五年 15 天。",
    5: "年假需提前 3 个工作日在系统提交申请，由直属主管审批。",
    6: "病假需提供二级以上医院证明，全年累计不超过 30 天。",
    7: "差旅报销需在出差结束后 10 个工作日内提交，附发票原件与行程单。",
    8: "住宿标准：一线城市每晚不超过 600 元，其他城市不超过 450 元。",
    9: "密码要求 12 位以上，每 90 天更换一次，连续 5 次失败锁定 30 分钟。",
    10: "生产数据禁止下载到个人设备，确需导出须走审批并加水印。",
}

# ---------------------------------------------------------------------------
# 30 条评测集
# ---------------------------------------------------------------------------
EVAL_SET = [
    # --- 退款类（1-6）---
    {"id": 1, "q": "退款多久到账", "gold_doc": 1, "gold_kw": ["退款", "3-5"],
     "answer": "退款原路退回，通常 3-5 个工作日到账 [1]", "should_refuse": False},
    {"id": 2, "q": "未发货能退款吗", "gold_doc": 2, "gold_kw": ["未发货", "全额退款"],
     "answer": "未发货订单可随时申请全额退款 [1]", "should_refuse": False},
    {"id": 3, "q": "虚拟商品能退款吗", "gold_doc": 3, "gold_kw": ["虚拟商品", "激活"],
     "answer": "虚拟商品一经激活不支持退款 [1]", "should_refuse": False},
    {"id": 4, "q": "退款怎么原路退回", "gold_doc": 1, "gold_kw": ["原路退回"],
     "answer": "退款金额原路退回 [1]", "should_refuse": False},
    {"id": 5, "q": "退款通常多久", "gold_doc": 1, "gold_kw": ["3-5", "工作日"],
     "answer": "通常 3-5 个工作日 [1]", "should_refuse": False},
    {"id": 6, "q": "未激活的虚拟商品怎么办", "gold_doc": 3, "gold_kw": ["未激活", "7 天"],
     "answer": "未激活可在 7 天内申请退款 [1]", "should_refuse": False},
    # --- 年假类（7-11）---
    {"id": 7, "q": "年假有几天", "gold_doc": 4, "gold_kw": ["5 天", "10 天"],
     "answer": "满一年 5 天，满三年 10 天 [1]", "should_refuse": False},
    {"id": 8, "q": "年假怎么申请", "gold_doc": 5, "gold_kw": ["提前", "系统"],
     "answer": "需提前 3 个工作日在系统提交申请 [1]", "should_refuse": False},
    {"id": 9, "q": "年假谁审批", "gold_doc": 5, "gold_kw": ["主管", "审批"],
     "answer": "由直属主管审批 [1]", "should_refuse": False},
    {"id": 10, "q": "满五年年假多少天", "gold_doc": 4, "gold_kw": ["15 天"],
     "answer": "满五年 15 天 [1]", "should_refuse": False},
    {"id": 11, "q": "年假提前几天申请", "gold_doc": 5, "gold_kw": ["3 个工作日"],
     "answer": "提前 3 个工作日 [1]", "should_refuse": False},
    # --- 病假 / 报销（12-17）---
    {"id": 12, "q": "病假需要什么证明", "gold_doc": 6, "gold_kw": ["医院证明", "二级"],
     "answer": "需提供二级以上医院证明 [1]", "should_refuse": False},
    {"id": 13, "q": "病假全年多少天", "gold_doc": 6, "gold_kw": ["30 天"],
     "answer": "全年累计不超过 30 天 [1]", "should_refuse": False},
    {"id": 14, "q": "差旅报销什么时候交", "gold_doc": 7, "gold_kw": ["10 个工作日"],
     "answer": "出差结束后 10 个工作日内提交 [1]", "should_refuse": False},
    {"id": 15, "q": "差旅报销需要什么材料", "gold_doc": 7, "gold_kw": ["发票", "行程单"],
     "answer": "附发票原件与行程单 [1]", "should_refuse": False},
    {"id": 16, "q": "一线城市住宿标准", "gold_doc": 8, "gold_kw": ["600 元", "一线"],
     "answer": "一线城市每晚不超过 600 元 [1]", "should_refuse": False},
    {"id": 17, "q": "其他城市住宿多少钱", "gold_doc": 8, "gold_kw": ["450 元"],
     "answer": "其他城市不超过 450 元 [1]", "should_refuse": False},
    # --- 安全（18-22）---
    {"id": 18, "q": "密码要多少位", "gold_doc": 9, "gold_kw": ["12 位"],
     "answer": "要求 12 位以上 [1]", "should_refuse": False},
    {"id": 19, "q": "密码多久换一次", "gold_doc": 9, "gold_kw": ["90 天"],
     "answer": "每 90 天更换一次 [1]", "should_refuse": False},
    {"id": 20, "q": "登录失败几次锁定", "gold_doc": 9, "gold_kw": ["5 次", "锁定"],
     "answer": "连续 5 次失败锁定 30 分钟 [1]", "should_refuse": False},
    {"id": 21, "q": "能下载生产数据吗", "gold_doc": 10, "gold_kw": ["禁止", "个人设备"],
     "answer": "生产数据禁止下载到个人设备 [1]", "should_refuse": False},
    {"id": 22, "q": "数据导出要审批吗", "gold_doc": 10, "gold_kw": ["审批", "水印"],
     "answer": "确需导出须走审批并加水印 [1]", "should_refuse": False},
    # --- 应拒答（23-30，知识库里没有）---
    {"id": 23, "q": "今天天气怎么样", "gold_doc": None, "gold_kw": [],
     "answer": "资料里没有相关内容。", "should_refuse": True},
    {"id": 24, "q": "公司股价多少", "gold_doc": None, "gold_kw": [],
     "answer": "资料里没有相关内容。", "should_refuse": True},
    {"id": 25, "q": "CEO 是谁", "gold_doc": None, "gold_kw": [],
     "answer": "资料里没有相关内容。", "should_refuse": True},
    {"id": 26, "q": "食堂几点开门", "gold_doc": None, "gold_kw": [],
     "answer": "资料里没有相关内容。", "should_refuse": True},
    {"id": 27, "q": "怎么申请车位", "gold_doc": None, "gold_kw": [],
     "answer": "资料里没有相关内容。", "should_refuse": True},
    {"id": 28, "q": "年终奖怎么算", "gold_doc": None, "gold_kw": [],
     "answer": "资料里没有相关内容。", "should_refuse": True},
    {"id": 29, "q": "员工体检什么时候", "gold_doc": None, "gold_kw": [],
     "answer": "资料里没有相关内容。", "should_refuse": True},
    {"id": 30, "q": "内部培训课程有哪些", "gold_doc": None, "gold_kw": [],
     "answer": "资料里没有相关内容。", "should_refuse": True},
]


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------
def _terms(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower()))


# 检索阈值：重叠率低于它的不算命中（对应 Unit 20 的「阈值前置拒答」）
MIN_SCORE = 0.4


def retrieve(q: str, k: int = 2, min_score: float = MIN_SCORE) -> list[int]:
    """字符级重叠检索，返回 top-k 文档 id。

    min_score 是「分数低于它就不算命中」的阈值——没有它，短问题（如「今天天气怎么样」）
    会因为共享一个「天」字而误命中。这是 RAG 拒答的第一道闸门。
    """
    q_terms = _terms(q)
    scored = []
    for doc_id, text in KB.items():
        overlap = len(q_terms & _terms(text)) / max(1, len(q_terms))
        scored.append((doc_id, overlap))
    scored.sort(key=lambda kv: -kv[1])
    return [d for d, s in scored[:k] if s >= min_score]


def recall_at_k(preds: list[list[int]], golds: list[int | None], k: int = 2) -> float:
    """检索命中率：gold 文档是否出现在 top-k 里（只统计有 gold 的样本）。"""
    hit = total = 0
    for pred, gold in zip(preds, golds):
        if gold is None:
            continue
        total += 1
        if gold in pred[:k]:
            hit += 1
    return round(hit / total, 4) if total else 0.0


# 虚词：不计入忠实度（它们不承载信息，资料里有没有都不影响「是否脱离资料」的判断）
STOP_CHARS = set("的了是在和与及就被把让给对从向这那有什么怎样多少不无已经")


def faithfulness(answer: str, contexts: list[str]) -> float:
    """忠实度：答案里的实词有多少能在上下文里找到。

    做法：去掉引用标记 [n]、标点和虚词，看剩余实词有多少落在上下文的字符集里。
    字符级是简化版（真实用句子级 NLI），但已能抓「答案里出现资料没有的词」这个幻觉信号。
    """
    ans = re.sub(r"\[\d+\]", "", answer)
    ctx_chars = set("".join(contexts))
    meaningful = [
        c for c in ans
        if re.match(r"[\u4e00-\u9fffA-Za-z0-9]", c) and c not in STOP_CHARS
    ]
    if not meaningful:
        return 1.0
    hit = sum(1 for c in meaningful if c in ctx_chars)
    return round(hit / len(meaningful), 4)


def relevance(answer: str, question: str) -> float:
    """答案相关性：问题关键词在答案里的覆盖率。"""
    q_terms = _terms(question)
    if not q_terms:
        return 0.0
    ans_terms = _terms(answer)
    hit = len(q_terms & ans_terms)
    return round(hit / len(q_terms), 4)


def evaluate(eval_set: list[dict], k: int = 2) -> dict:
    """跑完整评测：检索 + 忠实度 + 相关性 + 拒答双侧。"""
    preds, golds = [], []
    faiths, rels = [], []
    tp = fp = tn = fn = 0

    for item in eval_set:
        docs = retrieve(item["q"], k)
        preds.append(docs)
        golds.append(item["gold_doc"])

        # 系统实际是否拒答：检索为空 → 拒答
        pred_refuse = len(docs) == 0
        if item["should_refuse"] and pred_refuse:
            tp += 1
        elif item["should_refuse"] and not pred_refuse:
            fn += 1
        elif not item["should_refuse"] and pred_refuse:
            fp += 1
        else:
            tn += 1

        if not item["should_refuse"]:
            ctx = [KB[d] for d in docs]
            faiths.append(faithfulness(item["answer"], ctx))
            rels.append(relevance(item["answer"], item["q"]))

    n_refuse = tp + fn
    n_answer = fp + tn
    return {
        "samples": len(eval_set),
        "recall_at_k": recall_at_k(preds, golds, k),
        "faithfulness": round(sum(faiths) / len(faiths), 4) if faiths else 0.0,
        "relevance": round(sum(rels) / len(rels), 4) if rels else 0.0,
        "refusal": {
            "tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "false_accept_rate": round(fn / n_refuse, 4) if n_refuse else 0.0,
            "false_refuse_rate": round(fp / n_answer, 4) if n_answer else 0.0,
        },
    }


app = FastAPI(title="Unit 70 · Evaluation（30 条评测集）", version="0.1.0")


@app.get("/eval", summary="跑 30 条评测集：检索命中率 + 忠实度 + 相关性 + 拒答双侧")
async def eval_endpoint(k: int = 2) -> dict:
    return evaluate(EVAL_SET, k)


@app.get("/eval/cases", summary="看全部评测样本")
async def eval_cases() -> dict:
    return {"count": len(EVAL_SET), "cases": EVAL_SET}


@app.get("/", include_in_schema=False)
async def index() -> dict:
    return {"hint": "/eval 跑评测；/eval/cases 看样本；/eval?k=0 演示检索退化"}


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

    print("Unit 70 · Evaluation（30 条评测集）自检\n")

    # ---- 评测集完整性 ----
    check("评测集 30 条", len(EVAL_SET) == 30, str(len(EVAL_SET)))
    check("样本 id 不重复", len({e["id"] for e in EVAL_SET}) == 30)
    check("问题不重复", len({e["q"] for e in EVAL_SET}) == 30)
    answered = [e for e in EVAL_SET if not e["should_refuse"]]
    refused = [e for e in EVAL_SET if e["should_refuse"]]
    check("可答样本 22 条", len(answered) == 22, str(len(answered)))
    check("拒答样本 8 条", len(refused) == 8, str(len(refused)))
    check("可答样本都有 gold_doc", all(e["gold_doc"] is not None for e in answered))
    check("拒答样本 gold_doc 为空", all(e["gold_doc"] is None for e in refused))
    check("可答样本都有 gold_kw", all(len(e["gold_kw"]) >= 1 for e in answered))

    # ---- 指标函数 ----
    check("recall 全命中 = 1.0", recall_at_k([[1], [2]], [1, 2]) == 1.0)
    check("recall 全落空 = 0.0", recall_at_k([[9], [9]], [1, 2]) == 0.0)
    check("faithness 完全来自上下文 = 1.0",
          faithfulness("退款原路退回 [1]", ["退款原路退回"]) == 1.0)
    check("faithness 编造内容 < 1.0",
          faithfulness("退款会立即到账绝不拖延 [1]", ["退款原路退回"]) < 1.0)
    check("faithness 忽略虚词", faithfulness("退款的原路退回 [1]", ["退款原路退回"]) == 1.0)
    check("相关性高", relevance("退款 3-5 个工作日到账", "退款多久到账") > 0.5)
    check("相关性低", relevance("年假 5 天", "退款多久到账") < 0.5)

    # ---- 端到端评测 ----
    report = evaluate(EVAL_SET, k=2)
    check("评测跑通 30 条", report["samples"] == 30)
    check("检索命中率 = 1.0", report["recall_at_k"] == 1.0, str(report["recall_at_k"]))
    check("忠实度 = 1.0（答案逐字来自资料）", report["faithfulness"] == 1.0, str(report["faithfulness"]))
    check("误收率 = 0", report["refusal"]["false_accept_rate"] == 0.0, str(report["refusal"]))
    check("误拒率 = 0", report["refusal"]["false_refuse_rate"] == 0.0, str(report["refusal"]))
    check("TP=8", report["refusal"]["tp"] == 8, str(report["refusal"]))
    check("TN=22", report["refusal"]["tn"] == 22, str(report["refusal"]))

    # ---- 退化演示 ----
    bad = evaluate(EVAL_SET, k=0)
    check("k=0 时检索命中率降", bad["recall_at_k"] < 1.0, str(bad["recall_at_k"]))
    check("k=0 时误拒率上升", bad["refusal"]["false_refuse_rate"] > 0, str(bad["refusal"]))

    # ---- HTTP ----
    c = TestClient(app)
    r = c.get("/eval")
    check("HTTP /eval 200", r.status_code == 200)
    b = r.json()
    check("HTTP 有四项指标",
          all(k in b for k in ("recall_at_k", "faithfulness", "relevance", "refusal")))
    r = c.get("/eval/cases")
    check("HTTP /eval/cases 30 条", r.json()["count"] == 30)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())