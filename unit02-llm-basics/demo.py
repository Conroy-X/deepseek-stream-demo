"""Unit 2 · LLM 基础概念的可运行验证。

对应原计划的 Day 2：LLM / Token / Context Window / Prompt / Temperature / API。
概念光看文字容易飘，这个文件用 4 个实验把它们钉死：

    实验 1  Token       —— 字数≠token 数，中文英文差别很大
    实验 2  Temperature —— 同一个问题跑多次，看输出是否漂移
    实验 3  Context     —— 历史越长，输入 token 越多、越贵、越慢
    实验 4  API 参数    —— max_tokens / stream 对行为的影响

运行：
    python demo.py                 # 跑全部（会花几分钱）
    python demo.py --offline       # 只跑不花钱的部分（token 估算）
    python demo.py --exp 1         # 只跑某个实验
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# --- venv 自愈：忘记 activate 也能跑（自动切换到本目录的 .venv） ---
def _reexec_in_venv():
    import os as _os
    import sys as _sys

    if _sys.prefix != _sys.base_prefix:
        return
    _venv_py = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), ".venv", "bin", "python")
    if _os.path.exists(_venv_py):
        _os.execv(_venv_py, [_venv_py, _os.path.abspath(__file__)] + _sys.argv[1:])


_reexec_in_venv()
# --- venv 自愈 结束 ---

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).with_name(".env"))

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

# deepseek-flash 空闲时段参考价（元 / 百万 token）
PRICE_IN, PRICE_OUT = 1.0, 4.0


def make_client() -> OpenAI:
    if not API_KEY or API_KEY.startswith("sk-你的"):
        print("❌ 没有 DEEPSEEK_API_KEY。请复制 .env.example 为 .env 并填写。")
        sys.exit(1)
    return OpenAI(api_key=API_KEY, base_url=BASE_URL)


def cost(prompt_tokens: int, completion_tokens: int) -> float:
    return prompt_tokens / 1e6 * PRICE_IN + completion_tokens / 1e6 * PRICE_OUT


# ---------------------------------------------------------------------------
# 实验 1：Token 不等于字数
# ---------------------------------------------------------------------------
def exp_token() -> None:
    print("=" * 66)
    print("实验 1 · Token 是什么（为什么按 token 收费，不按字数）")
    print("=" * 66)

    samples = [
        "Hello, world!",
        "你好，世界！",
        "FastAPI 是一个现代的 Python Web 框架。",
        "def add(a, b):\n    return a + b",
        "    ",  # 纯空格
        "1234567890",
    ]

    counter = None
    source = ""

    try:
        import signal

        import tiktoken  # type: ignore

        # tiktoken 首次调用会从网上下载 BPE 词表。本机 GitHub 不通会直接卡死，
        # 所以给它 5 秒上限，超时就退回字符估算（不影响实验结论）。
        def _alarm(signum, frame):  # noqa: ANN001, ARG001
            raise TimeoutError("tiktoken 下载词表超时")

        _old_handler = signal.signal(signal.SIGALRM, _alarm)
        signal.alarm(5)
        try:
            _enc = tiktoken.get_encoding("cl100k_base")
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, _old_handler)

        counter = lambda s: len(_enc.encode(s))  # noqa: E731
        source = "tiktoken(cl100k_base) 精确计数"
    except ImportError:
        pass
    except Exception:  # noqa: BLE001 - 任何原因失败都退回估算，不能让演示卡住
        print("  ⚠ tiktoken 不可用（词表下载被墙或超时），本次退回字符估算\n")

    if counter is None:

        def counter(s: str) -> int:
            cjk = sum(1 for ch in s if "\u4e00" <= ch <= "\u9fff")
            other = len(s) - cjk
            return max(1, round(cjk / 1.5 + other / 4))

        source = "字符数粗略估算（想精确计数：uv pip install tiktoken，需能访问 GitHub）"

    print(f"计数方式：{source}\n")
    print(f"{'文本':<44}{'字符':>6}{'token(约)':>10}")
    print("-" * 62)
    for s in samples:
        shown = s.replace("\n", "\\n")
        if len(shown) > 40:
            shown = shown[:37] + "..."
        print(f"{shown:<44}{len(s):>6}{counter(s):>10}")

    print("\n关键认知：")
    print("  · 中文通常 1~2 字 = 1 token，英文约 4 字符 = 1 token")
    print("  · 代码、标点、空格都会消耗 token —— 长 Prompt 的模板文字也是钱")
    print("  · 所以「让模型输出简洁」和「精简 System Prompt」都是省钱手段")


# ---------------------------------------------------------------------------
# 实验 2：Temperature 对输出稳定性的影响
# ---------------------------------------------------------------------------
def exp_temperature(client: OpenAI, runs: int = 3) -> None:
    print("\n" + "=" * 66)
    print("实验 2 · Temperature：0 一定稳定吗？2 一定乱吗？")
    print("=" * 66)
    question = "用一句话给产品经理解释什么是 token。"

    for temp in (0.0, 0.7, 1.5):
        print(f"\n--- temperature = {temp} ---")
        answers = []
        for i in range(runs):
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": question}],
                temperature=temp,
                max_tokens=120,
            )
            text = (resp.choices[0].message.content or "").strip()
            answers.append(text)
            print(f"  {i + 1}. {text}")
        unique = len(set(answers))
        print(f"  → {runs} 次里出现 {unique} 种不同回答")

    print("\n关键认知：")
    print("  · temperature=0 通常最稳定，但不保证逐字相同（浮点/批处理/MoE 路由都会带来微差）")
    print("  · 事实抽取、分类、结构化输出 → 用 0~0.3；创意写作 → 0.8~1.2")
    print("  · temperature 和 top_p 不要同时乱调，改一个就好")


# ---------------------------------------------------------------------------
# 实验 3：Context 的增长与成本
# ---------------------------------------------------------------------------
def exp_context(client: OpenAI, turns: int = 4) -> None:
    print("\n" + "=" * 66)
    print("实验 3 · Context：多轮对话为什么越聊越贵")
    print("=" * 66)

    history: list[dict[str, str]] = [
        {"role": "system", "content": "你在和用户聊天，每次回答控制在 20 字以内。"}
    ]
    total_in = total_out = 0

    print(f"{'轮次':<6}{'本次输入token':>14}{'累计输入':>10}{'本次费用':>12}")
    print("-" * 46)
    for turn in range(1, turns + 1):
        history.append({"role": "user", "content": f"这是第{turn}句话，请简短回应。"})
        resp = client.chat.completions.create(
            model=MODEL, messages=history, temperature=0.3, max_tokens=60
        )
        reply = (resp.choices[0].message.content or "").strip()
        u = resp.usage
        total_in += u.prompt_tokens
        total_out += u.completion_tokens
        print(f"{turn:<6}{u.prompt_tokens:>14}{total_in:>10}{cost(u.prompt_tokens, u.completion_tokens):>11.5f}元")
        history.append({"role": "assistant", "content": reply})

    print(f"\n合计：输入 {total_in} · 输出 {total_out} · 约 ¥{cost(total_in, total_out):.5f}")
    print("\n关键认知：")
    print("  · 每轮都把**全部历史**重新发一遍，所以输入 token 逐轮累加（平方级增长）")
    print("  · 这是 Unit 10「上下文管理」要解决的问题：截断 / 摘要 / 只带相关片段")
    print("  · 长对话的成本不是线性的，超过某个长度就会开始「变贵变慢变傻」")


# ---------------------------------------------------------------------------
# 实验 4：max_tokens 与 stream
# ---------------------------------------------------------------------------
def exp_params(client: OpenAI) -> None:
    print("\n" + "=" * 66)
    print("实验 4 · max_tokens 截断 与 stream 流式")
    print("=" * 66)

    question = "列举 5 个学习 Python 的建议，每个一句话。"
    for limit in (200, 30):
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": question}],
            temperature=0.3,
            max_tokens=limit,
        )
        text = (resp.choices[0].message.content or "").strip()
        print(f"\n--- max_tokens={limit} ---")
        print(f"  finish_reason = {resp.choices[0].finish_reason}")
        print(f"  输出 {resp.usage.completion_tokens} tokens")
        print(f"  内容：{text[:120]}{'...' if len(text) > 120 else ''}")

    print("\n--- stream=True 的观感（逐字出现）---")
    print("  ", end="")
    stream = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": "用 30 字介绍 FastAPI。"}],
        temperature=0.3,
        max_tokens=100,
        stream=True,
    )
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            print(chunk.choices[0].delta.content, end="", flush=True)
    print()

    print("\n关键认知：")
    print("  · finish_reason='length' 说明被 max_tokens 截断了 —— 不是模型答完了")
    print("  · stream=True 返回迭代器，首字延迟（TTFT）大幅下降，是 Chat UI 的标配")
    print("  · 流式时 usage 往往只在最后一个 chunk 或需要显式开启才返回")


def main() -> None:
    p = argparse.ArgumentParser(description="Unit 2 · LLM 基础概念验证")
    p.add_argument("--offline", action="store_true", help="只跑不花钱的实验")
    p.add_argument("--exp", type=int, choices=[1, 2, 3, 4], help="只跑指定实验")
    p.add_argument("--runs", type=int, default=3, help="temperature 实验重复次数")
    args = p.parse_args()

    if args.offline:
        exp_token()
        print("\n（--offline 模式结束，其余实验需要 API Key）")
        return

    client = make_client()
    print(f"模型 {MODEL} · 端点 {BASE_URL}\n")

    todo = [args.exp] if args.exp else [1, 2, 3, 4]
    if 1 in todo:
        exp_token()
    if 2 in todo:
        exp_temperature(client, runs=args.runs)
    if 3 in todo:
        exp_context(client)
    if 4 in todo:
        exp_params(client)

    print("\n" + "=" * 66)
    print("完成。把这 4 个结论写进 docs/ 笔记，本单元就算过了。")
    print("=" * 66)


if __name__ == "__main__":
    main()
