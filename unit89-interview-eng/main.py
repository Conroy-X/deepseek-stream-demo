"""Unit 89 · 面试 AI 工程化：30 题题库 + 自测打分。

计划要求：重点考成本、延迟、评测、安全。
"""

from __future__ import annotations

from fastapi import FastAPI

BANK = [
    # ---- 可靠性（1-7）----
    {"topic": "retry", "q": "什么错误该重试？指数退避加抖动为什么？", "points": ["可重试", "429", "抖动"]},
    {"topic": "timeout", "q": "超时怎么分层？504 和 500 的区别？", "points": ["分层", "504", "上游"]},
    {"topic": "circuit-breaker", "q": "熔断器解决什么问题？", "points": ["雪崩", "快速失败", "恢复"]},
    {"topic": "ratelimit", "q": "令牌桶和漏桶的区别？", "points": ["突发", "固定速率", "限流"]},
    {"topic": "idempotency", "q": "幂等怎么实现？", "points": ["幂等键", "去重表", "重试安全"]},
    {"topic": "degradation", "q": "服务降级怎么做？", "points": ["缓存兜底", "小模型", "明确报错"]},
    {"topic": "fallback", "q": "主模型挂了怎么切备用？", "points": ["多供应商", "健康检查", "切换策略"]},
    # ---- 可观测（8-12）----
    {"topic": "log", "q": "结构化日志至少要有什么？request_id 干什么？", "points": ["JSON", "request_id", "贯穿"]},
    {"topic": "metrics", "q": "LLM 应用要监控哪些指标？", "points": ["延迟", "token", "错误率"]},
    {"topic": "trace", "q": "链路追踪怎么串起 Agent 的多次调用？", "points": ["span", "父子", "上下文"]},
    {"topic": "cost-panel", "q": "成本面板怎么设计？", "points": ["用量", "单价", "分维度"]},
    {"topic": "alert", "q": "什么情况该告警？", "points": ["错误率", "延迟", "成本突增"]},
    # ---- 性能与成本（13-18）----
    {"topic": "cache", "q": "LLM 响应怎么缓存？key 怎么设计？", "points": ["key", "参数", "命中率"]},
    {"topic": "batching", "q": "批量请求有什么收益和风险？", "points": ["吞吐", "延迟", "超时"]},
    {"topic": "streaming-perf", "q": "流式输出改善的是哪个指标？", "points": ["首字延迟", "体感", "总时长"]},
    {"topic": "quantization", "q": "量化省什么？代价是什么？", "points": ["显存", "精度", "成本"]},
    {"topic": "kv-cache", "q": "KV Cache 是什么？省了什么？", "points": ["缓存 K/V", "显存", "速度"]},
    {"topic": "gpu", "q": "自建推理和调 API 怎么选？", "points": ["成本", "可控", "运维"]},
    # ---- 评测（19-23）----
    {"topic": "eval", "q": "怎么防止改 prompt 后模型退化？", "points": ["评测集", "指标", "护栏"]},
    {"topic": "eval-online", "q": "线上怎么评测？", "points": ["采样", "人工抽检", "反馈"]},
    {"topic": "ab-test", "q": "A/B 测试怎么做？", "points": ["分流", "指标", "显著性"]},
    {"topic": "metrics-choice", "q": "准确率和满意度的区别？", "points": ["客观", "主观", "组合"]},
    {"topic": "badcase", "q": "badcase 怎么归因？", "points": ["分类", "分层", "复现"]},
    # ---- 安全（24-28）----
    {"topic": "injection", "q": "Prompt Injection 怎么防？", "points": ["数据指令分离", "检测", "输出过滤"]},
    {"topic": "pii", "q": "PII 怎么发现和脱敏？", "points": ["正则", "模型", "输出审查"]},
    {"topic": "secrets", "q": "密钥怎么管理？", "points": ["环境变量", "轮换", "最小权限"]},
    {"topic": "access", "q": "多租户数据怎么隔离？", "points": ["行级", "租户字段", "权限"]},
    {"topic": "audit", "q": "合规审计要留什么？", "points": ["调用记录", "输入输出", "可追溯"]},
    # ---- 交付（29-30）----
    {"topic": "docker", "q": "Dockerfile 层缓存顺序？密钥为什么不能 COPY 进镜像？", "points": ["依赖先", "源码后", "镜像层"]},
    {"topic": "deploy", "q": "LLM 服务怎么部署和扩缩容？", "points": ["无状态", "水平扩展", "健康检查"]},
]

REQUIRED_TOPICS = ["retry", "timeout", "log", "cache", "eval", "injection", "docker", "metrics"]


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
    scores = {}
    for b in bank:
        ans = answers.get(b["topic"], "")
        hit = sum(1 for p in b["points"] if p in ans)
        scores[b["topic"]] = round(hit / len(b["points"]), 2)
    weak = [t for t, s in scores.items() if s < pass_line]
    avg = sum(scores.values()) / len(scores) if scores else 0.0
    return {"scores": scores, "weak": weak, "avg": round(avg, 2), "passed": len(weak) == 0}


app = FastAPI(title="Unit 89 · 面试 AI 工程化", version="0.1.0")


@app.get("/bank", summary="30 题题库")
async def bank(topic: str | None = None) -> dict:
    items = [b for b in BANK if topic is None or b["topic"] == topic]
    return {"count": len(items), "items": items}


@app.get("/quiz/check", summary="题库自检")
async def quiz_check() -> dict:
    return check_bank(BANK)


@app.get("/", include_in_schema=False)
async def index() -> dict:
    return {"hint": "/bank 看题库；/bank?topic=eval 过滤；/quiz/check 题库自检"}


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

    print("Unit 89 · 面试 AI 工程化（30 题）自检\n")
    r = check_bank(BANK)
    check("题库 30 题", r["count"] == 30, str(r["count"]))
    check("每题至少 3 个要点", r["all_have_points"])
    check("话题不重复", r["no_dup_topic"])
    check("题目不重复", r["no_dup_question"])
    check("覆盖核心话题", r["covered"])
    check("安全类题目 >= 5", sum(1 for t in r["topics"] if t in
          ("injection", "pii", "secrets", "access", "audit")) == 5)

    good = {b["topic"]: " ".join(b["points"]) for b in BANK}
    check("满分全部通过", self_quiz(BANK, good)["passed"] is True)
    check("空答案全不及格", len(self_quiz(BANK, {})["weak"]) == 30)

    c = TestClient(app)
    check("HTTP /bank 200", c.get("/bank").status_code == 200)
    check("HTTP 30 题", c.get("/bank").json()["count"] == 30)
    r2 = c.get("/bank", params={"topic": "eval"})
    check("HTTP 按话题过滤", all(i["topic"] == "eval" for i in r2.json()["items"]))
    check("HTTP /quiz/check", c.get("/quiz/check").json()["count"] == 30)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())