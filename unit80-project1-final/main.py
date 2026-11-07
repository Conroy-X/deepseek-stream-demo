"""Unit 80 · 项目1最终版：把 Unit 61-67 的工程组件接到 AI Chat 上。

核心认知：一个「能演示」的项目和「能交付」的项目的差别，就是这些横切组件：
超时、重试、限流、缓存、日志。本单元做「接线收口」——不写新算法，只做集成。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChatService:
    timeout: float = 5.0
    max_retries: int = 2
    rate_limit: int = 5
    cache: dict = field(default_factory=dict)
    logs: list = field(default_factory=list)

    def __post_init__(self) -> None:
        self._calls = 0

    def ask(self, q: str) -> dict:
        # 限流
        if self._calls >= self.rate_limit:
            self.logs.append("rate_limited")
            return {"error": "限流", "status": 429}
        # 缓存
        if q in self.cache:
            self.logs.append("cache_hit")
            return {"answer": self.cache[q], "hit": True}
        # 重试
        for attempt in range(1, self.max_retries + 1):
            self.logs.append(f"attempt_{attempt}")
            if q != "fail":  # 模拟：fail 触发重试
                self._calls += 1
                self.cache[q] = f"回答:{q}"
                return {"answer": self.cache[q], "hit": False, "attempts": attempt}
        return {"error": "重试耗尽", "status": 503}


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

    print("Unit 80 · 项目1最终版 自检\n")
    svc = ChatService()
    r1 = svc.ask("你好")
    check("首次回答", r1.get("answer") == "回答:你好" and r1["hit"] is False)
    r2 = svc.ask("你好")
    check("二次命中缓存", r2["hit"] is True)
    check("日志记录了 cache_hit", "cache_hit" in svc.logs)

    # 重试耗尽
    svc2 = ChatService(max_retries=2)
    r = svc2.ask("fail")
    check("失败重试后返回 503", r.get("status") == 503, str(r))
    check("重试了 max_retries 次", svc2.logs.count("attempt_1") == 1 and svc2.logs.count("attempt_2") == 1)

    # 限流
    svc3 = ChatService(rate_limit=2)
    svc3.ask("a")
    svc3.ask("b")
    r = svc3.ask("c")
    check("超限返回 429", r.get("status") == 429, str(r))

    # 日志完整性
    check("日志非空", len(svc.logs) > 0)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(run_selfcheck())
