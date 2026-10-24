"""Unit 13 · LLM 客户端封装：超时 / 重试 / 异常归一 / 限流 / 成本统计。

为什么不能直接 `client.chat.completions.create()` 就算了：
    真实线上会遇到的都不是"代码 bug"，而是这些：
      · 网络抖动 / 连接被重置        → 需要重试
      · 429 限流（平台侧）           → 需要退避后重试
      · 5xx（平台侧故障）            → 需要重试
      · 401 密钥错 / 400 参数错      → 重试多少次都没用，必须快速失败
      · 用户狂点按钮                 → 需要限流，不然你的钱和对方的配额一起烧完
      · 半夜账单暴涨                 → 需要按调用记账（tokens × 单价）

本模块把这些一次性收口。上层（FastAPI 路由）只需要 `await chat(...)`
或者 `async for delta in chat_stream(...)`，不用关心重试细节。

⚠️ 一个容易踩的坑：OpenAI SDK 自带 max_retries（默认 2）。
   我们在外面又包一层重试，如果不把 SDK 的关掉（max_retries=0），
   实际重试次数会变成「外层次数 × SDK 次数」，1 次调用可能打出 9 个请求。
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openai
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv(Path(__file__).with_name(".env"))

# 便于自检把 sleep 换掉，避免真的等几十秒
_SLEEP: Callable[[float], Awaitable[None]] = asyncio.sleep


# ---------------------------------------------------------------------------
# 异常体系：把所有底层异常归一成这几种，上层只需要 catch LLMError
# ---------------------------------------------------------------------------
class LLMError(Exception):
    """所有 LLM 调用异常的基类。"""

    def __init__(self, message: str, *, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


class LLMConfigError(LLMError):
    """配置错（没 key / 占位 key）。重试无意义。"""


class LLMBadRequestError(LLMError):
    """400：参数不合法、上下文超长。重试无意义。"""


class LLMAuthError(LLMError):
    """401/403：密钥无效或无权限。重试无意义。"""


class LLMRateLimitError(LLMError):
    """429：被限流。可重试，但要退避。"""


class LLMServerError(LLMError):
    """5xx：平台侧故障。可重试。"""


class LLMTimeoutError(LLMError):
    """超时。可重试。"""


class LLMConnectionError(LLMError):
    """连接失败/被重置。可重试。"""


class RateLimited(LLMError):
    """本地令牌桶拒绝（还没发请求就被拦下）。"""


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"
    # 超时要分三段看：连不上、发不出去、等太久不回，是三个不同的问题
    timeout: float = 60.0
    max_retries: int = 3
    base_delay: float = 0.5      # 首次退避 0.5s，之后 1s / 2s ...
    max_delay: float = 8.0
    # 成本（元 / 百万 token）。按各平台实际价格改，这里只是默认占位
    price_in_per_mtok: float = 1.0
    price_out_per_mtok: float = 2.0

    @classmethod
    def from_env(cls) -> LLMConfig:
        return cls(
            api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            timeout=float(os.getenv("LLM_TIMEOUT", "60")),
            max_retries=int(os.getenv("LLM_MAX_RETRIES", "3")),
            base_delay=float(os.getenv("LLM_BASE_DELAY", "0.5")),
            max_delay=float(os.getenv("LLM_MAX_DELAY", "8")),
            price_in_per_mtok=float(os.getenv("PRICE_IN_PER_MTOK", "1.0")),
            price_out_per_mtok=float(os.getenv("PRICE_OUT_PER_MTOK", "2.0")),
        )


def _is_placeholder(key: str) -> bool:
    return (not key) or key.startswith("sk-你的") or key in {"sk-xxx", "test", "changeme"}


# ---------------------------------------------------------------------------
# 令牌桶限流
# ---------------------------------------------------------------------------
class TokenBucket:
    """令牌桶：容量 capacity，每秒补 refill_per_sec 个令牌。

    和"固定窗口计数"的区别：
        固定窗口在窗口边界会被打穿（59 秒打满、61 秒再打满，两秒内过两倍流量）；
        令牌桶是按持续速率放行，天然平滑，还能承受短时突发（桶里有存货）。

    这里是单机内存版。多实例部署要换成 Redis 的原子实现，否则每个进程各自算，
    实际放行量 = 实例数 × 配置值。
    """

    def __init__(self, capacity: int, refill_per_sec: float):
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self._tokens = float(capacity)
        self._last = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_sec)
            self._last = now

    def try_acquire(self, n: float = 1.0) -> bool:
        self._refill()
        if self._tokens >= n:
            self._tokens -= n
            return True
        return False

    def retry_after(self, n: float = 1.0) -> float:
        """还差多少秒才能凑够 n 个令牌 —— 用来填 Retry-After 响应头。"""
        self._refill()
        missing = n - self._tokens
        if missing <= 0:
            return 0.0
        return round(missing / self.refill_per_sec, 2)


class PerUserRateLimiter:
    """每个用户一个桶。参数：每分钟允许 N 次（桶容量 N，每秒补 N/60）。"""

    def __init__(self, per_minute: int = 20):
        self.per_minute = per_minute
        self._buckets: dict[str, TokenBucket] = {}

    def bucket(self, key: str) -> TokenBucket:
        b = self._buckets.get(key)
        if b is None:
            b = TokenBucket(capacity=self.per_minute, refill_per_sec=self.per_minute / 60.0)
            self._buckets[key] = b
        return b

    def check(self, key: str) -> None:
        b = self.bucket(key)
        if not b.try_acquire():
            raise RateLimited(
                f"请求过于频繁，请 {b.retry_after():.1f} 秒后再试",
                retryable=False,
                status=429,
            )


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------
@dataclass
class CallStats:
    """一次调用的完整账本。"""

    attempts: int = 0
    retries: int = 0
    error_kinds: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "retries": self.retries,
            "error_kinds": self.error_kinds,
            "elapsed": round(self.elapsed, 3),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cost": round(self.cost, 8),
        }


class CostLedger:
    """进程级累计账本。生产上应该落库/上报，这里内存版用于演示与自检。"""

    def __init__(self) -> None:
        self.total_calls = 0
        self.total_retries = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_cost = 0.0
        self.recent: deque[dict[str, Any]] = deque(maxlen=50)

    def record(self, stats: CallStats, model: str) -> None:
        self.total_calls += 1
        self.total_retries += stats.retries
        self.total_prompt_tokens += stats.prompt_tokens
        self.total_completion_tokens += stats.completion_tokens
        self.total_cost += stats.cost
        self.recent.append({"model": model, **stats.as_dict()})

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_calls": self.total_calls,
            "total_retries": self.total_retries,
            "total_prompt_tokens": self.total_prompt_tokens,
            "total_completion_tokens": self.total_completion_tokens,
            "total_cost_cny": round(self.total_cost, 6),
            "recent": list(self.recent)[-10:],
        }


LEDGER = CostLedger()


# ---------------------------------------------------------------------------
# 异常归一
# ---------------------------------------------------------------------------
def normalize_error(exc: BaseException) -> LLMError:
    """把 SDK 抛的各种异常翻译成我们自己的类型 + 是否可重试。

    重试与否的判断标准不是"什么错"，而是「**同样的请求再发一次，有没有可能成功**」：
        · 429 / 5xx / 超时 / 连接错 → 有可能，重试
        · 400 / 401 / 403           → 不可能，快速失败
    """
    if isinstance(exc, LLMError):
        return exc

    if isinstance(exc, openai.RateLimitError):
        return LLMRateLimitError(str(exc), retryable=True, status=429)
    if isinstance(exc, openai.APITimeoutError):
        return LLMTimeoutError(str(exc) or "请求超时", retryable=True)
    if isinstance(exc, openai.APIConnectionError):
        return LLMConnectionError(str(exc) or "连接失败", retryable=True)
    if isinstance(exc, openai.AuthenticationError):
        return LLMAuthError(str(exc), retryable=False, status=401)
    if isinstance(exc, openai.PermissionDeniedError):
        return LLMAuthError(str(exc), retryable=False, status=403)
    if isinstance(exc, openai.BadRequestError):
        return LLMBadRequestError(str(exc), retryable=False, status=400)
    if isinstance(exc, openai.InternalServerError):
        return LLMServerError(str(exc), retryable=True, status=500)
    if isinstance(exc, openai.APIStatusError):
        retryable = 500 <= (exc.status_code or 0) < 600
        return LLMError(str(exc), retryable=retryable, status=exc.status_code)

    return LLMError(f"{type(exc).__name__}: {exc}", retryable=False)


def backoff_delay(attempt: int, base: float, cap: float) -> float:
    """指数退避 + 抖动（full jitter）。

    attempt 从 0 开始：base, 2*base, 4*base ... 封顶 cap。
    抖动为什么必须加：一批客户端同时被限流，如果都按固定间隔重试，
    它们会永远同步撞在一起（惊群），限流永远解不开。
    random.uniform(0, delay) 把重试时刻打散。
    """
    raw = min(cap, base * (2**attempt))
    return random.uniform(0, raw)


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------
class LLMClient:
    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig.from_env()
        self._client: AsyncOpenAI | None = None
        self._client_key: str | None = None

    def client(self) -> AsyncOpenAI:
        cfg = self.config
        if _is_placeholder(cfg.api_key):
            raise LLMConfigError(
                "缺少有效的 DEEPSEEK_API_KEY，请在 .env 里配置（cp .env.example .env）",
                retryable=False,
            )
        if self._client is None or self._client_key != cfg.api_key:
            self._client = AsyncOpenAI(
                api_key=cfg.api_key,
                base_url=cfg.base_url,
                timeout=cfg.timeout,
                # 关掉 SDK 自带重试，否则和我们这层叠乘
                max_retries=0,
            )
            self._client_key = cfg.api_key
        return self._client

    # -- 非流式 -------------------------------------------------------------
    async def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> tuple[str, CallStats]:
        stats = CallStats()
        started = time.perf_counter()
        cfg = self.config

        last_error: LLMError | None = None
        for attempt in range(cfg.max_retries + 1):
            stats.attempts = attempt + 1
            try:
                resp = await self.client().chat.completions.create(
                    model=cfg.model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                stats.elapsed = time.perf_counter() - started
                if resp.usage:
                    stats.prompt_tokens = resp.usage.prompt_tokens
                    stats.completion_tokens = resp.usage.completion_tokens
                stats.cost = self._cost(stats.prompt_tokens, stats.completion_tokens)
                LEDGER.record(stats, cfg.model)
                return (resp.choices[0].message.content or ""), stats

            except BaseException as exc:  # noqa: BLE001
                err = normalize_error(exc)
                last_error = err
                stats.error_kinds.append(type(err).__name__)
                if not err.retryable or attempt >= cfg.max_retries:
                    stats.elapsed = time.perf_counter() - started
                    LEDGER.record(stats, cfg.model)
                    raise err from exc
                stats.retries += 1
                await _SLEEP(backoff_delay(attempt, cfg.base_delay, cfg.max_delay))

        # 理论上到不了这里
        stats.elapsed = time.perf_counter() - started
        LEDGER.record(stats, cfg.model)
        raise last_error or LLMError("未知错误")

    # -- 流式 ---------------------------------------------------------------
    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> AsyncIterator[dict[str, Any]]:
        """逐块产出事件字典。

        流式重试的特殊规则：**第一个 chunk 到达之后就不能再重试了**。
        因为此时前端已经显示了一半内容，你再重发一遍，用户会看到重复的文字。
        所以这里的策略是"只在拿到第一个 token 前重试"。
        """
        cfg = self.config
        started = time.perf_counter()
        stats = CallStats()

        for attempt in range(cfg.max_retries + 1):
            stats.attempts = attempt + 1
            got_first = False
            try:
                stream = await self.client().chat.completions.create(
                    model=cfg.model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                    stream_options={"include_usage": True},
                )
                async for chunk in stream:
                    if chunk.usage:
                        stats.prompt_tokens = chunk.usage.prompt_tokens
                        stats.completion_tokens = chunk.usage.completion_tokens
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content
                    if not delta:
                        continue
                    if not got_first:
                        got_first = True
                        yield {"type": "first_token", "ttft": round(time.perf_counter() - started, 3)}
                    yield {"type": "delta", "content": delta}

                stats.elapsed = time.perf_counter() - started
                stats.cost = self._cost(stats.prompt_tokens, stats.completion_tokens)
                LEDGER.record(stats, cfg.model)
                yield {
                    "type": "done",
                    "elapsed": round(stats.elapsed, 3),
                    **stats.as_dict(),
                }
                return

            except BaseException as exc:  # noqa: BLE001
                err = normalize_error(exc)
                stats.error_kinds.append(type(err).__name__)
                if got_first or not err.retryable or attempt >= cfg.max_retries:
                    stats.elapsed = time.perf_counter() - started
                    LEDGER.record(stats, cfg.model)
                    yield {"type": "error", "kind": type(err).__name__, "message": str(err)}
                    return
                stats.retries += 1
                yield {
                    "type": "retry",
                    "attempt": attempt + 1,
                    "kind": type(err).__name__,
                    "delay": round(backoff_delay(attempt, cfg.base_delay, cfg.max_delay), 2),
                }
                await _SLEEP(backoff_delay(attempt, cfg.base_delay, cfg.max_delay))

    def _cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        cfg = self.config
        return (
            prompt_tokens / 1_000_000 * cfg.price_in_per_mtok
            + completion_tokens / 1_000_000 * cfg.price_out_per_mtok
        )


# 进程内单例（FastAPI 里直接 import 用）
_default_client: LLMClient | None = None


def get_client() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client
