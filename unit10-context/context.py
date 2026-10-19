"""Unit 10 · 上下文管理：trim_messages 的两种策略。

问题：
    多轮对话里每轮都把**完整历史**重发，输入 token 随轮次累加（近似平方级）。
    超过上下文窗口会直接报错；即使没超，也会变贵、变慢，还可能在中间位置
    丢失信息（lost in the middle）。

三种策略：
    1. none        不处理（基准对照，用来观察劣化）
    2. sliding     滑动窗口：保留 System + 最近 N 条 / 最近 N token
    3. summarize   摘要压缩：超出预算的旧对话先总结成一段 summary 再塞回去

本模块不依赖 LLM，可离线测试；摘要策略的「生成摘要」由调用方注入，
这样测试时可以用假摘要，生产时接真模型。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Literal

Role = Literal["system", "user", "assistant"]

# ---------------------------------------------------------------------------
# Token 估算
# ---------------------------------------------------------------------------
# 精确计数需要 tiktoken（要联网下词表，本机 GitHub 不通）。
# 这里用一个接近实际的估算公式，并在文档里说明误差来源。
#   - 中日韩字符：约 1 字 ≈ 1.5 token（DeepSeek/GPT 系分词器实测区间 1.2~1.8）
#   - 其它字符（英文/数字/符号）：约 4 字符 ≈ 1 token
CJK_RANGE = (0x4E00, 0x9FFF)


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数。误差通常 ±20%，用于做预算够用。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if CJK_RANGE[0] <= ord(ch) <= CJK_RANGE[1])
    other = len(text) - cjk
    return max(1, math.ceil(cjk / 1.5 + other / 4))


def messages_tokens(messages: list[dict]) -> int:
    """估算整个消息列表的 token 数。

    注意：真实 API 每条消息还有固定的「格式开销」（role、分隔符等，
    通常 3~4 token/条），这里按 4 计入，更接近实际计费。
    """
    return sum(estimate_tokens(m.get("content", "")) + 4 for m in messages)


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------
@dataclass
class TrimResult:
    messages: list[dict]
    strategy: str
    original_tokens: int
    final_tokens: int
    dropped_messages: int = 0
    summarized: bool = False
    summary: str = ""

    @property
    def saved_tokens(self) -> int:
        return max(0, self.original_tokens - self.final_tokens)

    @property
    def saved_ratio(self) -> float:
        if self.original_tokens == 0:
            return 0.0
        return self.saved_tokens / self.original_tokens

    def report(self) -> str:
        lines = [
            f"策略: {self.strategy}",
            f"原始: {self.original_tokens} tokens",
            f"最终: {self.final_tokens} tokens（省 {self.saved_tokens}，{self.saved_ratio:.1%}）",
            f"丢弃: {self.dropped_messages} 条",
        ]
        if self.summarized:
            lines.append(f"已生成摘要（{estimate_tokens(self.summary)} tokens）")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 策略 1：滑动窗口
# ---------------------------------------------------------------------------
def trim_sliding(
    messages: list[dict],
    *,
    max_tokens: int,
    keep_system: bool = True,
    min_keep: int = 2,
) -> TrimResult:
    """保留 System（如果第一条是）+ 从最新往前尽量多装。

    min_keep 保证至少保留最近 N 条，哪怕已经超预算 —— 宁可贵一点，
    也不能把用户刚说的话丢掉。
    """
    original = messages_tokens(messages)
    if not messages:
        return TrimResult([], "sliding", 0, 0)

    system: list[dict] = []
    rest = messages
    if keep_system and messages[0].get("role") == "system":
        system = [messages[0]]
        rest = messages[1:]

    budget = max_tokens - messages_tokens(system)
    kept: list[dict] = []
    for msg in reversed(rest):
        cost = messages_tokens([msg])
        if messages_tokens(kept) + cost <= budget or len(kept) < min_keep:
            kept.insert(0, msg)
        else:
            break

    final = system + kept
    return TrimResult(
        messages=final,
        strategy="sliding",
        original_tokens=original,
        final_tokens=messages_tokens(final),
        dropped_messages=len(rest) - len(kept),
    )


# ---------------------------------------------------------------------------
# 策略 2：摘要压缩
# ---------------------------------------------------------------------------
Summarizer = Callable[[list[dict]], str]


def trim_summarize(
    messages: list[dict],
    *,
    max_tokens: int,
    summarizer: Summarizer,
    keep_recent: int = 4,
    summary_prefix: str = "以下是之前对话的摘要：",
) -> TrimResult:
    """把「太旧」的部分交给 summarizer 压成一段话，再拼回最近的对话。

    结构：System + [摘要消息] + 最近 keep_recent 条

    keep_recent 条永远原样保留 —— 最近几轮是最相关的上下文，压成摘要会丢细节。
    """
    original = messages_tokens(messages)
    if not messages:
        return TrimResult([], "summarize", 0, 0)

    system: list[dict] = []
    rest = messages
    if messages[0].get("role") == "system":
        system = [messages[0]]
        rest = messages[1:]

    # 没超预算就不用摘要
    if original <= max_tokens:
        return TrimResult(list(messages), "summarize", original, original, summarized=False)

    recent = rest[-keep_recent:] if len(rest) > keep_recent else rest
    older = rest[: len(rest) - len(recent)]

    summary_text = ""
    if older:
        summary_text = summarizer(older).strip()

    final: list[dict] = list(system)
    if summary_text:
        final.append({"role": "system", "content": f"{summary_prefix}\n{summary_text}"})
    final.extend(recent)

    return TrimResult(
        messages=final,
        strategy="summarize",
        original_tokens=original,
        final_tokens=messages_tokens(final),
        dropped_messages=len(older),
        summarized=bool(summary_text),
        summary=summary_text,
    )


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
def trim_messages(
    messages: list[dict],
    *,
    max_tokens: int,
    strategy: Literal["none", "sliding", "summarize"] = "sliding",
    summarizer: Summarizer | None = None,
    keep_recent: int = 4,
) -> TrimResult:
    if strategy == "none":
        t = messages_tokens(messages)
        return TrimResult(list(messages), "none", t, t)
    if strategy == "sliding":
        return trim_sliding(messages, max_tokens=max_tokens)
    if strategy == "summarize":
        if summarizer is None:
            raise ValueError("summarize 策略需要传入 summarizer 函数")
        return trim_summarize(
            messages, max_tokens=max_tokens, summarizer=summarizer, keep_recent=keep_recent
        )
    raise ValueError(f"未知策略: {strategy}")


# ---------------------------------------------------------------------------
# 预算表：把上下文窗口的钱花在哪儿说清楚
# ---------------------------------------------------------------------------
@dataclass
class Budget:
    """上下文预算表 —— 面试常问「你的 token 怎么分配」。"""

    total: int = 64_000
    reserved_output: int = 4_096
    system: int = 300
    history: int = 0
    retrieved: int = 0  # RAG 检索片段（Unit 15+ 用）
    user_input: int = 0

    @property
    def available_for_history(self) -> int:
        return max(
            0,
            self.total
            - self.reserved_output
            - self.system
            - self.user_input,
        )

    def table(self) -> str:
        rows = [
            ("上下文窗口总量", self.total),
            ("预留输出", self.reserved_output),
            ("System Prompt", self.system),
            ("用户本轮输入", self.user_input),
            ("检索片段(RAG)", self.retrieved),
            ("可用于历史", self.available_for_history),
        ]
        w = max(len(r[0]) for r in rows)
        out = ["上下文预算表", "-" * (w + 14)]
        for name, val in rows:
            pct = val / self.total if self.total else 0
            out.append(f"{name:<{w}}  {val:>7,}  {pct:>5.1%}")
        return "\n".join(out)
