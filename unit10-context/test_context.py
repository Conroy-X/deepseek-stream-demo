#!/usr/bin/env python3
"""Unit 10 · context.py 的离线测试（不调用模型、不花钱）。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from context import (  # noqa: E402
    Budget,
    estimate_tokens,
    messages_tokens,
    trim_messages,
    trim_sliding,
    trim_summarize,
)

passed = failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


def msgs(n: int, size: int = 100) -> list[dict]:
    """生成 n 轮对话（user/assistant 交替），每条 size 字符。"""
    out = []
    for i in range(n):
        content = f"第{i}轮内容" + "补" * size
        out.append({"role": "user", "content": content})
        out.append({"role": "assistant", "content": content})
    return out


print("Unit 10 · 上下文管理 自测\n")

# ---------------- token 估算 ----------------
print("--- token 估算 ---")
check("空串 = 0", estimate_tokens("") == 0)
check("纯中文有值", estimate_tokens("你好世界") > 0)
check("中文比英文密", estimate_tokens("你好世界") >= estimate_tokens("abcd"))
check("长度越大 token 越多", estimate_tokens("a" * 400) > estimate_tokens("a" * 40))
check("消息列表含格式开销", messages_tokens([{"role": "user", "content": "hi"}]) >= 4)

# ---------------- 滑动窗口 ----------------
print("\n--- 滑动窗口 ---")
history = [{"role": "system", "content": "你是助手。"}] + msgs(10)
r = trim_sliding(history, max_tokens=500)
check("保留了 System", r.messages[0]["role"] == "system")
check("token 未超预算", r.final_tokens <= 500, str(r.final_tokens))
check("有丢弃", r.dropped_messages > 0, str(r.dropped_messages))
check("保留的是最近的", r.messages[-1] == history[-1])
check("省了 token", r.saved_tokens > 0)

# 预算极大时不该丢
r2 = trim_sliding(history, max_tokens=100_000)
check("预算充足时不丢弃", r2.dropped_messages == 0)

# 预算极小仍保留最近 min_keep 条
r3 = trim_sliding(history, max_tokens=10)
check("极紧预算下保留最近 2 条", len([m for m in r3.messages if m["role"] != "system"]) == 2,
      str(len(r3.messages)))

# 无 System 也能工作
nosys = msgs(5)
r4 = trim_sliding(nosys, max_tokens=300)
check("无 System 时不报错", len(r4.messages) > 0)

# 空列表
r5 = trim_sliding([], max_tokens=100)
check("空列表返回空", r5.messages == [])

# ---------------- 摘要压缩 ----------------
print("\n--- 摘要压缩 ---")
summarized_inputs: list[list[dict]] = []


def fake_summarizer(old: list[dict]) -> str:
    summarized_inputs.append(old)
    return f"用户讨论了 {len(old)} 条旧消息，主题是测试。"

history2 = [{"role": "system", "content": "你是助手。"}] + msgs(12)
r6 = trim_summarize(history2, max_tokens=800, summarizer=fake_summarizer, keep_recent=4)
check("触发了摘要", r6.summarized)
check("摘要器被调用", len(summarized_inputs) == 1)
check("摘要被放进消息里",
      any("以下是之前对话的摘要" in m["content"] for m in r6.messages))
check("保留最近 4 条原样", len([m for m in r6.messages if m["role"] in ("user", "assistant")]) == 4)
check("最近一条未被摘要", r6.messages[-1] == history2[-1])
check("token 减少", r6.final_tokens < r6.original_tokens,
      f"{r6.final_tokens} vs {r6.original_tokens}")

# 没超预算不该摘要
r7 = trim_summarize(history2, max_tokens=100_000, summarizer=fake_summarizer)
check("未超预算时不摘要", not r7.summarized)
check("未超预算时原样返回", r7.messages == history2)

# ---------------- 统一入口 ----------------
print("\n--- 统一入口 ---")
r8 = trim_messages(history, max_tokens=400, strategy="none")
check("none 策略不裁剪", len(r8.messages) == len(history))

r9 = trim_messages(history, max_tokens=400, strategy="sliding")
check("sliding 策略生效", r9.dropped_messages > 0)

r10 = trim_messages(history2, max_tokens=800, strategy="summarize", summarizer=fake_summarizer)
check("summarize 策略生效", r10.summarized)

try:
    trim_messages(history, max_tokens=100, strategy="summarize")
    check("缺 summarizer 时抛错", False)
except ValueError:
    check("缺 summarizer 时抛错", True)

# ---------------- 三种策略对比 ----------------
print("\n--- 三策略对比（同一份 20 条历史）---")
big = [{"role": "system", "content": "你是助手。"}] + msgs(10)
for strat in ("none", "sliding", "summarize"):
    rr = trim_messages(
        big, max_tokens=600, strategy=strat,
        summarizer=lambda o: "摘要：" + "x" * 50,
    )
    print(f"  {strat:<10} 最终 {rr.final_tokens:>5} tokens · 丢弃 {rr.dropped_messages:>2} 条 · "
          f"省 {rr.saved_ratio:>5.1%}")

# ---------------- 预算表 ----------------
print("\n--- 上下文预算表 ---")
b = Budget(total=64_000, reserved_output=4_096, system=300, user_input=200)
print("  " + b.table().replace("\n", "\n  "))
check("可用于历史 = 总量-输出-System-输入",
      b.available_for_history == 64_000 - 4_096 - 300 - 200)
b2 = Budget(total=1000, reserved_output=2000, system=300)
check("不会出现负数", b2.available_for_history == 0)

# ---------------- 劣化演示：为什么必须裁剪 ----------------
print("\n--- 为什么必须裁剪：输入 token 的累积 ---")
total = 0
per_turn = []
for turn in range(1, 11):
    h = [{"role": "system", "content": "你是助手。"}] + msgs(turn)
    t = messages_tokens(h)
    per_turn.append(t)
    total += t
    if turn in (1, 5, 10):
        print(f"  第 {turn:>2} 轮：本次输入 {t:>5} tokens")
print(f"  10 轮累计输入约 {total:,} tokens（每轮都带完整历史）")
print(f"  对比：若每轮只带本轮，累计约 {per_turn[-1]:,} tokens（最后一轮的量）")
# 正确断言：历史是线性累加的 → 累计总量约为「单轮 × 轮数 / 2」，
# 远大于「只带一轮」；且输入 token 随轮次单调增长。
check("每轮输入随轮次增长", per_turn[-1] > per_turn[0] * 3,
      f"{per_turn[0]} -> {per_turn[-1]}")
check("累计输入远超单轮（约 5.5 倍）", total > per_turn[-1] * 5,
      f"累计 {total} vs 单轮 {per_turn[-1]}")
check("第 10 轮输入约为第 1 轮的 9~10 倍",
      8 <= per_turn[-1] / per_turn[0] <= 11,
      f"{per_turn[-1] / per_turn[0]:.1f}x")

print(f"\n结果：{passed} 通过 / {failed} 失败")
sys.exit(1 if failed else 0)
