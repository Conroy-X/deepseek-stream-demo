"""Unit 65 · Timeout：超时分层，LLM 与工具各自独立。

核心认知：
  1. 超时不是「失败」，是「不再等」——等不到就降级或明确报错，绝不能无限等
  2. 超时要分层：客户端请求 / LLM 调用 / 工具调用，各自独立设限。
     一个 LLM 调用 120 秒，不代表整个请求可以等 120 秒
  3. 超时是可重试错误的一种（Unit 64 的 RetryableError 要涵盖它）
  4. 设超时的依据：P95 延迟 + 余量，不是拍脑袋

本单元用 asyncio.wait_for 实现，假 LLM / 假工具用可配置延迟模拟慢调用。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


class TimeoutExceeded(Exception):
    """超时了，且说清「超的是哪一层、限制是多少」。

    注意：**不能继承 asyncio.TimeoutError** —— 否则 asyncio.wait_for 会把它
    误当成自己触发的超时，导致内层超时被外层误判。这是个隐蔽的坑。
    """

    def __init__(self, what: str, limit: float) -> None:
        self.what = what
        self.limit = limit
        super().__init__(f"{what} 超过 {limit}s 未完成")


async def run_with_timeout(coro, timeout: float, what: str):
    """给一个协程套超时。超时抛 TimeoutExceeded，带 what/limit 上下文。

    timeout <= 0 表示不设限。
    """
    if timeout <= 0:
        return await coro
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise TimeoutExceeded(what, timeout) from exc


@dataclass
class TimeoutPolicy:
    """一次请求里三层超时的预算。"""

    llm: float = 10.0
    tool: float = 3.0
    overall: float = 20.0

    def validate(self) -> None:
        # 只要求 > 0。overall 与单层超时是不同维度：
        # overall 是客户端对「整个请求」的耐心上限，
        # llm/tool 是单次等待的上限 —— 前者完全可以小于后者。
        if self.overall <= 0 or self.llm <= 0 or self.tool <= 0:
            raise ValueError("超时必须 > 0")


# ---------------------------------------------------------------------------
# 假 LLM / 假工具：可配置延迟
# ---------------------------------------------------------------------------
async def fake_llm(q: str, latency: float) -> str:
    await asyncio.sleep(latency)
    return f"回答:{q}"


async def fake_tool(name: str, latency: float) -> str:
    await asyncio.sleep(latency)
    return f"{name}:done"


# ---------------------------------------------------------------------------
# 一次完整问答：LLM 与工具分别套超时，整体再套一次
# ---------------------------------------------------------------------------
async def ask_once(q: str, *, policy: TimeoutPolicy, llm_latency: float = 0.0, tool_latency: float = 0.0) -> dict:
    policy.validate()

    async def body() -> dict:
        llm_out = await run_with_timeout(fake_llm(q, llm_latency), policy.llm, "LLM 调用")
        tool_out = await run_with_timeout(fake_tool("lookup", tool_latency), policy.tool, "工具调用")
        return {"answer": llm_out, "tool": tool_out}

    return await run_with_timeout(body(), policy.overall, "整体请求")


# ---------------------------------------------------------------------------
# 独立自检（异步）
# ---------------------------------------------------------------------------
async def _async_selfcheck() -> tuple[int, int]:
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 65 · Timeout 自检\n")

    # ---- 快调用不超时 ----
    r = await ask_once("hi", policy=TimeoutPolicy(llm=2.0, tool=2.0, overall=5.0),
                       llm_latency=0.01, tool_latency=0.01)
    check("快调用正常返回", r["answer"] == "回答:hi" and r["tool"] == "lookup:done", str(r))

    # ---- LLM 超时：抛 TimeoutExceeded 且带上下文 ----
    try:
        await ask_once("slow", policy=TimeoutPolicy(llm=0.1, tool=2.0, overall=5.0), llm_latency=1.0)
        check("LLM 超时触发", False)
    except TimeoutExceeded as e:
        check("LLM 超时触发", e.what == "LLM 调用", e.what)
        check("LLM 超时带限制", e.limit == 0.1, str(e.limit))

    # ---- 工具超时：what 是「工具调用」----
    try:
        await ask_once("x", policy=TimeoutPolicy(llm=2.0, tool=0.1, overall=5.0), tool_latency=1.0)
        check("工具超时触发", False)
    except TimeoutExceeded as e:
        check("工具超时触发", e.what == "工具调用", e.what)

    # ---- 整体超时：每层各自不超，但串行总时长超了也要停 ----
    try:
        await ask_once("x", policy=TimeoutPolicy(llm=1.0, tool=1.0, overall=0.4),
                       llm_latency=0.3, tool_latency=0.3)
        check("整体超时触发", False)
    except TimeoutExceeded as e:
        check("整体超时触发", e.what == "整体请求", e.what)

    # ---- 超时异常是独立类型，且不被外层误判 ----
    try:
        await run_with_timeout(asyncio.sleep(1.0), 0.05, "测试")
    except TimeoutExceeded as e:
        check("超时抛 TimeoutExceeded 且带 what", e.what == "测试", e.what)
    check("TimeoutExceeded 不是 asyncio.TimeoutError 子类（避免外层误判）",
          not issubclass(TimeoutExceeded, asyncio.TimeoutError))

    # 内层超时不应被外层改写成「整体超时」——这是上面那条继承 bug 的直接回归测试
    try:
        await ask_once("slow", policy=TimeoutPolicy(llm=0.1, tool=2.0, overall=5.0), llm_latency=1.0)
        check("内层超时不被外层误判", False)
    except TimeoutExceeded as e:
        check("内层超时不被外层误判（what 仍是 LLM 调用）", e.what == "LLM 调用", e.what)

    # ---- timeout <= 0 表示不设限 ----
    r = await run_with_timeout(asyncio.sleep(0.05), 0, "不限")
    check("timeout=0 不设限", r is None)

    # ---- policy 校验 ----
    try:
        TimeoutPolicy(overall=0.4, llm=1.0, tool=1.0).validate()
        check("overall 可以小于单层超时（不同维度）", True)
    except ValueError:
        check("overall 可以小于单层超时（不同维度）", False)
    try:
        TimeoutPolicy(overall=-1).validate()
        check("负超时抛错", False)
    except ValueError:
        check("负超时抛错", True)
    try:
        TimeoutPolicy(llm=0).validate()
        check("llm=0 抛错", False)
    except ValueError:
        check("llm=0 抛错", True)

    return passed, failed


def run_selfcheck() -> int:
    passed, failed = asyncio.run(_async_selfcheck())
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
