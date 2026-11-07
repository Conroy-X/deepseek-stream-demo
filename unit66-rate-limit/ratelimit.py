"""Unit 66 · Rate Limit：令牌桶 / 滑动窗口 / 固定窗口。

核心认知：
  1. 限流是保护上游（LLM 服务）和公平分配，不是刁难用户
  2. 三种算法各有权衡：
     - 令牌桶：允许突发（桶里攒的令牌），平滑持续速率 —— 最常用
     - 滑动窗口：精确但记录每个时间戳，内存略高
     - 固定窗口：实现最简单，但有「窗口边界漏洞」——两个窗口交界处可以瞬间放行 2×max
  3. 限流要返回 429 + Retry-After，客户端（Unit 64 的 Retry）才会正确地退避重试

本单元全部内存实现，离线可测；生产接 Redis（多实例共享计数）。
"""

from __future__ import annotations

import time
from collections import deque


class RateLimitExceeded(Exception):
    """被限流。retry_after 秒后可以再试。"""

    def __init__(self, retry_after: float) -> None:
        self.retry_after = retry_after
        super().__init__(f"限流：{retry_after:.1f}s 后再试")


class TokenBucket:
    """令牌桶：capacity 桶容量，refill_per_sec 每秒补充速率。

    允许突发：桶里攒了多少就能瞬间用多少；持续速率由 refill 决定。
    """

    def __init__(self, capacity: int, refill_per_sec: float) -> None:
        if capacity <= 0 or refill_per_sec <= 0:
            raise ValueError("capacity 和 refill_per_sec 必须 > 0")
        self.capacity = float(capacity)
        self.refill = float(refill_per_sec)
        self.tokens = self.capacity
        self.last = time.monotonic()

    def allow(self, n: int = 1) -> bool:
        now = time.monotonic()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.refill)
        self.last = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def retry_after(self) -> float:
        """还需要多久才能凑够 1 个令牌（给 429 的 Retry-After 用）。"""
        need = max(0.0, 1.0 - self.tokens)
        return need / self.refill if self.refill else 0.0


class SlidingWindow:
    """滑动窗口：过去 window_sec 秒内最多 max_requests 次。

    精确，但要记录每个请求时间戳。
    """

    def __init__(self, window_sec: float, max_requests: int) -> None:
        if window_sec <= 0 or max_requests <= 0:
            raise ValueError("window_sec 和 max_requests 必须 > 0")
        self.window = float(window_sec)
        self.max = max_requests
        self._ts: deque[float] = deque()

    def allow(self) -> bool:
        now = time.monotonic()
        while self._ts and now - self._ts[0] > self.window:
            self._ts.popleft()
        if len(self._ts) < self.max:
            self._ts.append(now)
            return True
        return False

    def retry_after(self) -> float:
        if not self._ts:
            return 0.0
        return max(0.0, self.window - (time.monotonic() - self._ts[0]))


class FixedWindow:
    """固定窗口：把时间切成 window_sec 的格子，每格最多 max_requests 次。

    实现最简单，但有边界漏洞：窗口交界瞬间可以连发 2×max 而不被限。
    """

    def __init__(self, window_sec: float, max_requests: int) -> None:
        if window_sec <= 0 or max_requests <= 0:
            raise ValueError("window_sec 和 max_requests 必须 > 0")
        self.window = float(window_sec)
        self.max = max_requests
        self._bucket = -1
        self._count = 0

    def allow(self) -> bool:
        now = time.monotonic()
        bucket = int(now / self.window)
        if bucket != self._bucket:
            self._bucket = bucket
            self._count = 0
        if self._count < self.max:
            self._count += 1
            return True
        return False

    def retry_after(self) -> float:
        now = time.monotonic()
        next_bucket = (self._bucket + 1) * self.window
        return max(0.0, next_bucket - now)


class PerClientLimiter:
    """按 client_id 各配一个限流器；未知 client 也算一类（但生产要按真实身份）。"""

    def __init__(self, factory, default_allow: bool = True) -> None:
        self._factory = factory
        self._buckets: dict[str, object] = {}
        self.default_allow = default_allow

    def allow(self, client_id: str) -> bool:
        if client_id not in self._buckets:
            self._buckets[client_id] = self._factory()
        limiter = self._buckets[client_id]
        return limiter.allow()

    def retry_after(self, client_id: str) -> float:
        limiter = self._buckets.get(client_id)
        return limiter.retry_after() if limiter and hasattr(limiter, "retry_after") else 0.0

    def client_count(self) -> int:
        return len(self._buckets)


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

    print("Unit 66 · Rate Limit 自检\n")

    # ---- 令牌桶：容量与突发 ----
    tb = TokenBucket(capacity=3, refill_per_sec=1.0)
    check("空桶允许 3 次突发", tb.allow() and tb.allow() and tb.allow())
    check("第 4 次被拒", not tb.allow())
    check("被拒后有 retry_after", tb.retry_after() > 0)

    # ---- 令牌桶：随时间补充 ----
    tb2 = TokenBucket(capacity=1, refill_per_sec=10.0)
    check("用掉唯一令牌", tb2.allow())
    check("立刻再要没有", not tb2.allow())
    time.sleep(0.11)  # 10/秒 → 0.11 秒补 1.1 个
    check("0.11 秒后补充回来", tb2.allow())

    # ---- 滑动窗口 ----
    sw = SlidingWindow(window_sec=1.0, max_requests=2)
    check("滑动窗口放 2 次", sw.allow() and sw.allow())
    check("第 3 次被拒", not sw.allow())
    time.sleep(1.05)
    check("1 秒后窗口滑走，重新放行", sw.allow())

    # ---- 固定窗口边界漏洞 ----
    # max=2，在窗口交界处：前一窗末尾 2 次 + 后一窗开头 2 次，
    # 实际 1 秒内放了 4 次 —— 这是固定窗口的缺陷
    fw = FixedWindow(window_sec=10.0, max_requests=2)
    fw._bucket = 0
    fw._count = 0
    # 模拟窗口末尾
    fw.allow(); fw.allow()
    # 直接切到下一窗口
    fw._bucket = 1
    fw._count = 0
    check("固定窗口边界漏洞：跨窗口瞬间又放 2 次（缺陷演示）", fw.allow() and fw.allow())

    # ---- PerClientLimiter 隔离 ----
    pl = PerClientLimiter(lambda: TokenBucket(1, 1.0))
    check("client A 用掉配额", pl.allow("A"))
    check("client A 再要没有", not pl.allow("A"))
    check("client B 不受 A 影响", pl.allow("B"))
    check("隔离后有两个 client", pl.client_count() == 2)

    # ---- 参数校验 ----
    try:
        TokenBucket(0, 1)
        check("capacity=0 抛错", False)
    except ValueError:
        check("capacity=0 抛错", True)
    try:
        SlidingWindow(1, 0)
        check("max=0 抛错", False)
    except ValueError:
        check("max=0 抛错", True)

    # ---- RateLimitExceeded ----
    e = RateLimitExceeded(3.0)
    check("RateLimitExceeded 带 retry_after", e.retry_after == 3.0)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
