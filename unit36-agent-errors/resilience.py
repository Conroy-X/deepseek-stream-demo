"""Unit 36 · 工具一定会坏，坏的时候循环不能坏。

四件事，四个类/函数：

    call_tool()        异常 / 超时 → 统一变成 ToolResult(ok=False)，绝不冒泡
    LoopBreaker        同一工具 + 同一参数重复 N 次 → 熔断，循环停
    IdempotencyStore   同一个 tool_call_id 重放 → 不重复执行有副作用的工具
    （正常路径）       以上三条都不生效时，行为和 Unit 34 完全一样

一条贯穿的原则：**工具层的失败必须被翻译成一条消息**，因为模型只能读消息，
不能读异常栈；而你的循环只能读 `stopped`，不能读你的心情。

超时用 `asyncio.wait_for`：它取消的是那个协程，不是整个事件循环 ——
所以同一个 batch 里其他工具照常返回。这一点是本单元唯一必须用 async 的理由。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

DEFAULT_BUDGET = 1.0


@dataclass
class ToolResult:
    name: str
    ok: bool
    kind: str  # ok / exception / timeout / unknown_tool / bad_arguments / blocked
    content: str
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "kind": self.kind,
            "content": self.content,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


def error_content(kind: str, message: str) -> str:
    """错误也是结果，形状必须统一 —— 模型要学会读它，代码也要。"""
    return json.dumps({"error": {"kind": kind, "message": message}}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 一、异常与超时
# ---------------------------------------------------------------------------
async def call_tool(name: str, args: dict, impl: dict[str, Callable[..., Awaitable[Any]]], budget: float = DEFAULT_BUDGET) -> ToolResult:
    """执行一个工具。任何失败都返回 ok=False，绝不抛出去。"""
    fn = impl.get(name)
    if fn is None:
        return ToolResult(name, False, "unknown_tool", error_content("unknown_tool", f"未知工具 {name}"))

    t0 = time.perf_counter()
    try:
        data = await asyncio.wait_for(fn(**args), timeout=budget)
    except asyncio.TimeoutError:
        # 注意：这里不会连带取消别的协程，只取消这一个
        return ToolResult(name, False, "timeout", error_content("timeout", f"{name} 超过 {budget}s 未返回，已放弃"), (time.perf_counter() - t0) * 1000)
    except TypeError as e:
        return ToolResult(name, False, "bad_arguments", error_content("bad_arguments", str(e)), (time.perf_counter() - t0) * 1000)
    except Exception as e:  # noqa: BLE001 - 工具内部什么都能抛
        return ToolResult(name, False, "exception", error_content("exception", f"{type(e).__name__}: {e}"), (time.perf_counter() - t0) * 1000)

    content = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return ToolResult(name, True, "ok", content, (time.perf_counter() - t0) * 1000)


async def call_many(calls: list[dict], impl: dict, budget: float = DEFAULT_BUDGET) -> list[ToolResult]:
    """并行执行。一个超时，其他照常返回 —— 这是 wait_for 比「全局超时」好的地方。"""
    async def one(call: dict) -> ToolResult:
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            return ToolResult(name, False, "bad_arguments", error_content("bad_arguments", "arguments 不是合法 JSON"))
        return await call_tool(name, args, impl, budget)

    return list(await asyncio.gather(*(one(c) for c in calls)))


# ---------------------------------------------------------------------------
# 二、死循环熔断
# ---------------------------------------------------------------------------
class LoopBreaker:
    """同一工具 + 同一参数，第 N 次就熔断。参数比较用排序后的 JSON，避免键序不同就算「不同调用」。"""

    def __init__(self, max_repeats: int = 3):
        if max_repeats < 2:
            raise ValueError("max_repeats 至少为 2，否则第一次调用就被熔断")
        self.max_repeats = max_repeats
        self.counts: dict[str, int] = {}

    @staticmethod
    def key(name: str, args: dict) -> str:
        return f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"

    def hit(self, name: str, args: dict) -> str | None:
        """返回熔断原因（触发时）或 None（放行）。第 max_repeats 次触发。"""
        k = self.key(name, args)
        self.counts[k] = self.counts.get(k, 0) + 1
        n = self.counts[k]
        if n >= self.max_repeats:
            return f"同一调用重复 {n} 次仍未收敛：{name}({json.dumps(args, ensure_ascii=False, sort_keys=True)})"
        return None

    def reset(self) -> None:
        self.counts.clear()


# ---------------------------------------------------------------------------
# 三、幂等：同一个 tool_call_id 重放不重复执行
# ---------------------------------------------------------------------------
class IdempotencyStore:
    """key 应该是 tool_call_id（客户端重试会带同一个 id）。
    没有 id 的工具（比如内部定时任务）就用业务键，比如 order_id。"""

    def __init__(self) -> None:
        self.cache: dict[str, Any] = {}
        self.effects: list[str] = []
        self.replays = 0

    async def once(self, key: str, fn: Callable[[], Awaitable[Any]]) -> tuple[Any, bool]:
        """返回 (结果, 是否是重放)。只有第一次真正执行 fn。"""
        if key in self.cache:
            self.replays += 1
            return self.cache[key], True
        result = await fn()
        self.cache[key] = result
        self.effects.append(key)
        return result, False

    def reset(self) -> None:
        self.cache.clear()
        self.effects.clear()
        self.replays = 0


@dataclass
class BreakerEvent:
    step: int
    reason: str
    key: str


@dataclass
class RunState:
    question: str
    messages: list[dict] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    steps: int = 0
    stopped: str | None = None
    answer: str = ""
    breaker_events: list[BreakerEvent] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "messages": self.messages,
            "trace": self.trace,
            "steps": self.steps,
            "stopped": self.stopped,
            "answer": self.answer,
            "breaker_events": [{"step": e.step, "reason": e.reason, "key": e.key} for e in self.breaker_events],
        }


async def run_agent(
    question: str,
    model,
    impl: dict,
    max_steps: int = 6,
    budget: float = DEFAULT_BUDGET,
    breaker: LoopBreaker | None = None,
    system: str = "你是助手。工具失败时如实说明，不要编造数据。",
) -> RunState:
    """带四道防线的循环。正常路径与 Unit 34 完全一致。"""
    breaker = breaker or LoopBreaker(3)
    st = RunState(question=question)
    st.messages = [{"role": "system", "content": system}, {"role": "user", "content": question}]

    while st.stopped is None:
        if st.steps >= max_steps:
            st.stopped = "max_steps"
            st.answer = "达到最大步数仍未得到最终答案。"
            break

        st.steps += 1
        msg = model.complete(st.messages)
        calls = msg.get("tool_calls") or []

        if not calls:
            st.messages.append({"role": "assistant", "content": msg.get("content")})
            st.stopped = "answer" if msg.get("content") else "empty"
            st.answer = msg.get("content") or ""
            st.trace.append({"step": st.steps, "type": "model", "decision": st.stopped, "content": msg.get("content")})
            break

        st.messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
        st.trace.append({"step": st.steps, "type": "model", "decision": "call_tools", "calls": len(calls)})

        # 熔断检查在**执行之前** —— 已经知道是死循环了，没必要再调一次有副作用的工具
        blocked = None
        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            reason = breaker.hit(name, args)
            if reason:
                blocked = BreakerEvent(st.steps, reason, breaker.key(name, args))
                break

        if blocked:
            st.breaker_events.append(blocked)
            st.trace.append({"step": st.steps, "type": "breaker", "reason": blocked.reason})
            # 注意：此时 messages 里有一条带 tool_calls 的 assistant 却没有 tool 消息。
            # 所以补一条「被拦截」的 tool 消息，保持数组合法（Unit 33 的规矩）。
            for call in calls:
                st.messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": call["function"]["name"],
                    "content": error_content("blocked", "重复调用已被熔断，未执行"),
                })
            st.stopped = "loop_detected"
            st.answer = f"检测到重复调用，已熔断：{blocked.reason}"
            break

        results = await call_many(calls, impl, budget)
        for call, res in zip(calls, results):
            st.messages.append({"role": "tool", "tool_call_id": call["id"], "name": res.name, "content": res.content})
            st.trace.append(
                {
                    "step": st.steps,
                    "type": "tool",
                    "tool": res.name,
                    "ok": res.ok,
                    "kind": res.kind,
                    "elapsed_ms": round(res.elapsed_ms, 1),
                    "content": res.content[:200],
                }
            )

    return st
