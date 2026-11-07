"""Unit 89 · 面试 AI 工程化：题库 + 自测。"""

from __future__ import annotations

BANK = [
    {"topic": "retry", "q": "什么错误该重试？指数退避加抖动为什么？", "points": ["可重试", "429", "抖动"]},
    {"topic": "timeout", "q": "超时怎么分层？504 和 500 的区别？", "points": ["分层", "504", "上游"]},
    {"topic": "ratelimit", "q": "令牌桶和漏桶的区别？", "points": ["突发", "固定速率", "限流"]},
    {"topic": "cache", "q": "LLM 响应怎么缓存？key 怎么设计？", "points": ["key", "参数", "命中率"]},
    {"topic": "log", "q": "结构化日志至少要有什么？request_id 干什么？", "points": ["JSON", "request_id", "贯穿"]},
    {"topic": "security", "q": "Prompt Injection 怎么防？", "points": ["数据指令分离", "检测", "输出过滤"]},
    {"topic": "docker", "q": "Dockerfile 层缓存顺序？密钥为什么不能 COPY 进镜像？", "points": ["依赖先", "源码后", "镜像层"]},
    {"topic": "eval", "q": "怎么防止改 prompt 后模型退化？", "points": ["评测集", "指标", "护栏"]},
]


def check_bank(bank: list[dict]) -> dict:
    topics = [b["topic"] for b in bank]
    return {"count": len(bank), "topics": topics,
            "all_have_points": all(len(b.get("points", [])) >= 3 for b in bank),
            "no_dup": len(topics) == len(set(topics))}


def self_quiz(bank: list[dict], answers: dict[str, str]) -> dict:
    return {b["topic"]: sum(1 for p in b["points"] if p in answers.get(b["topic"], "")) / len(b["points"])
            for b in bank}


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

    print("Unit 89 · 面试 AI 工程化 自检\n")
    r = check_bank(BANK)
    check("题库 8 题", r["count"] == 8)
    check("每题至少 3 个要点", r["all_have_points"])
    check("话题不重复", r["no_dup"])
    check("覆盖工程话题", all(t in r["topics"] for t in ["retry", "docker", "security", "eval"]))
    good = {b["topic"]: " ".join(b["points"]) for b in BANK}
    check("满分答案全对", all(s == 1.0 for s in self_quiz(BANK, good).values()))
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(run_selfcheck())
