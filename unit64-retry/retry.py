"""Unit 64 · Retry：只重试「可重试」的错误，指数退避 + 抖动。

核心认知：
  1. 不是所有错误都该重试 —— 429/5xx/网络超时可以，4xx 是参数错，重试一万次也没用
  2. 指数退避 + 随机抖动 —— 大量客户端同时重试时，抖动把请求错开，避免惊群（thundering herd）
  3. 双重上限 —— max_attempts 管次数，max_time 管总时长，哪个先到都停
  4. 幂等是重试的前提 —— 非幂等操作（扣款、下单）重试前要先做幂等键

本单元用「假 API」模拟三种失败模式，把重试行为全部离线测一遍。
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

T = TypeVar("T")


class RetryableError(Exception):
    """可重试：429 / 5xx / 网络超时。"""


class PermanentError(Exception):
    """不可重试：4xx 参数错、业务拒绝。"""


@dataclass
class RetryStats:
    attempts: int = 0
    delays: list[float] = field(default_factory=list)
    total_elapsed_ms: float = 0.0
    last_error: Exception | None = None


def backoff_delay(attempt: int, base: float = 0.2, factor: float = 2.0, max_delay: float = 10.0, jitter: float = 0.1) -> float:
    """第 attempt 次重试前的等待秒数：base * factor^(attempt-1)，封顶 max_delay，再加 ±jitter 抖动。

    attempt 从 1 开始（第一次重试 attempt=1）。
    """
    raw = min(base * (factor ** (attempt - 1)), max_delay)
    jit = raw * jitter * random.uniform(-1.0, 1.0)
    return max(0.0, raw + jit)


def retry(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    max_time: float = 30.0,
    base: float = 0.2,
    factor: float = 2.0,
    max_delay: float = 10.0,
    jitter: float = 0.1,
    retryable: tuple[type[Exception], ...] = (RetryableError,),
    on_retry: Callable[[int, Exception, float], None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> T:
    """执行 fn，失败且属于可重试错误时指数退避重试。

    - 不可重试错误直接抛，不浪费重试次数
    - 双上限：重试次数或总时长先到即停
    - on_retry 回调（记日志、发告警）
    """
    stats = RetryStats()
    t0 = time.perf_counter()

    while True:
        try:
            stats.attempts += 1
            return fn()
        except Exception as exc:  # noqa: BLE001
            stats.last_error = exc
            stats.total_elapsed_ms = (time.perf_counter() - t0) * 1000

            can_retry = isinstance(exc, retryable)
            attempts_left = stats.attempts < max_attempts
            time_left = stats.total_elapsed_ms / 1000 < max_time

            if not can_retry or not attempts_left or not time_left:
                raise

            delay = backoff_delay(stats.attempts, base, factor, max_delay, jitter)
            stats.delays.append(delay)
            if on_retry:
                on_retry(stats.attempts, exc, delay)
            sleep_fn(delay)


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

    print("Unit 64 · Retry 自检\n")

    # ---- 退避公式 ----
    d1 = backoff_delay(1, base=1.0, factor=2.0, max_delay=100, jitter=0.0)
    d2 = backoff_delay(2, base=1.0, factor=2.0, max_delay=100, jitter=0.0)
    d3 = backoff_delay(3, base=1.0, factor=2.0, max_delay=100, jitter=0.0)
    check("第1次退避 = base", abs(d1 - 1.0) < 1e-9, str(d1))
    check("第2次退避 = base*factor", abs(d2 - 2.0) < 1e-9, str(d2))
    check("第3次退避 = base*factor^2", abs(d3 - 4.0) < 1e-9, str(d3))
    check("封顶 max_delay", backoff_delay(10, base=1.0, factor=2.0, max_delay=8.0, jitter=0.0) <= 8.0)
    # 抖动让两次同参退避不同（概率上；固定 seed 保证确定）
    random.seed(42)
    j1 = backoff_delay(1, base=1.0, jitter=0.5)
    j2 = backoff_delay(1, base=1.0, jitter=0.5)
    check("抖动使延迟变化", abs(j1 - j2) > 1e-9, f"{j1} vs {j2}")

    # ---- 成功立即返回 ----
    calls = []

    def ok() -> str:
        calls.append(1)
        return "ok"

    r = retry(ok, max_attempts=3, sleep_fn=lambda s: None)
    check("一次成功只调一次", r == "ok" and len(calls) == 1)

    # ---- 可重试错误：重试后成功 ----
    state = {"n": 0}

    def flaky() -> str:
        state["n"] += 1
        if state["n"] < 3:
            raise RetryableError("暂时不可用")
        return "终于成功"

    r = retry(flaky, max_attempts=5, base=0.0, jitter=0.0, sleep_fn=lambda s: None)
    check("重试两次后成功", r == "终于成功" and state["n"] == 3, f"n={state['n']}")

    # ---- 不可重试错误：直接抛，不重试 ----
    state2 = {"n": 0}

    def bad() -> str:
        state2["n"] += 1
        raise PermanentError("参数错")

    try:
        retry(bad, max_attempts=5, sleep_fn=lambda s: None)
        check("不可重试错误直接抛", False)
    except PermanentError:
        check("不可重试错误直接抛", state2["n"] == 1, f"n={state2['n']}")

    # ---- 用尽次数后抛最后一个错误 ----
    state3 = {"n": 0}

    def always() -> str:
        state3["n"] += 1
        raise RetryableError("永远失败")

    try:
        retry(always, max_attempts=3, sleep_fn=lambda s: None)
        check("用尽次数后抛错", False)
    except RetryableError:
        check("用尽次数后抛错", state3["n"] == 3, f"n={state3['n']}")

    # ---- max_time 上限 ----
    state4 = {"n": 0}

    def slow_fail() -> str:
        state4["n"] += 1
        raise RetryableError("慢且失败")

    t0 = time.perf_counter()
    try:
        retry(slow_fail, max_attempts=100, max_time=0.5, base=0.3, jitter=0.0, sleep_fn=time.sleep)
        check("max_time 生效", False)
    except RetryableError:
        elapsed = time.perf_counter() - t0
        check("max_time 生效（总时长受控）", state4["n"] < 100 and elapsed < 2.0, f"n={state4['n']} t={elapsed:.1f}s")

    # ---- on_retry 回调被调用 ----
    events = []
    state5 = {"n": 0}

    def flaky2() -> str:
        state5["n"] += 1
        if state5["n"] < 3:
            raise RetryableError("x")
        return "ok"

    retry(flaky2, max_attempts=5, base=0.0, jitter=0.0, sleep_fn=lambda s: None,
          on_retry=lambda a, e, d: events.append((a, d)))
    check("on_retry 触发两次", len(events) == 2, str(events))

    # ---- stats 完整 ----
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
