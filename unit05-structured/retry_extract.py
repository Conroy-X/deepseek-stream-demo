"""Unit 5 · 选做：校验失败自动重试（把错误喂回模型）—— 结构化输出的"自愈"闭环。

Unit 4 结论的延伸：Prompt 不是一次写好的，是**反馈循环**调出来的。
本文件演示最小可用版本：
    抽取 → Pydantic 校验 → 失败？把 ValidationError 原文塞回对话让模型改 → 再校验
    （最多 N 轮，still 失败就如实报错，绝不带病通过）

真实生产里这个循环由 Instructor 库封装好了（见 README 资料），但手写一遍才知道
Instructor 到底帮你做了什么 —— 面试被问"用过 Instructor 吗"时，你可以说：
"手写过它的核心循环，所以我知道它的重试参数该怎么调。"

用法：
    python retry_extract.py                # 正常简历（大概率一轮就过，看不到重试）
    python retry_extract.py --dirty        # 内置的"脏简历"（诱导模型输出脏数据看重试）
    python retry_extract.py --max-retries 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


# --- venv 自愈：忘记 activate 也能跑（自动切换到本目录的 .venv） ---
def _reexec_in_venv():
    import os as _os
    import sys as _sys

    if _sys.prefix != _sys.base_prefix:
        return  # 已经在 venv 里，什么都不做
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _venv_py = _os.path.join(_here, ".venv", "bin", "python")
    if _os.path.exists(_venv_py):
        _os.execv(_venv_py, [_venv_py, _os.path.abspath(__file__)] + _sys.argv[1:])


_reexec_in_venv()
# --- venv 自愈 结束 ---

from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import LLMCallError, LLMClient, LLMConfigError  # noqa: E402
from models import RESUME_JSON_INSTRUCTION, Resume, build_extract_messages  # noqa: E402

console = Console()

# 这份"脏简历"埋了几个雷：年限写成"6-7 年"（模型可能输出字符串）、职级写的是中文、
# 电话是"面谈"（诱导非 null 脏值）—— 目的是让第一轮校验失败，触发重试
DIRTY_RESUME = """王小明
工作 6-7 年吧，反正挺久了
职级：高级
电话：面谈
技能：会一点 python，还有 JS 那些都行
干过的活：在几家公司待过，做网站的
毕业好些年了"""


def extract_with_retry(client: LLMClient, resume_text: str, max_retries: int) -> tuple[Resume | None, list[dict]]:
    """带重试的抽取。返回 (最终结果或 None, 每轮记录)。"""
    messages = list(build_extract_messages(resume_text))
    history: list[dict] = []

    for attempt in range(1, max_retries + 2):  # 首轮 + max_retries 轮重试
        label = f"第 {attempt} 轮" if attempt == 1 else f"重试 {attempt - 1}/{max_retries}"
        try:
            raw = client.chat(messages, max_tokens=3000)
        except LLMCallError as exc:
            history.append({"round": label, "ok": False, "error": str(exc)})
            console.print(f"[red]{label} 调用失败：{exc}[/red]")
            break
        # 打印模型输出信息
        console.print(Panel(raw, title=f"{label} 模型输出原文", border_style="blue"))
        try:
            resume = Resume.model_validate_json(raw)
            history.append({"round": label, "ok": True, "rounds_used": attempt})
            console.print(f"[green]{label} ✅ 校验通过[/green]（{client.meta.completion_tokens} tokens）")
            return resume, history
        except ValidationError as exc:
            errors_short = "\n".join(f"- {'; '.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors()[:5])
            history.append({"round": label, "ok": False, "validation_errors": exc.errors()})
            if attempt > max_retries:
                console.print(f"[red]{label} ❌ 仍不合法，放弃（共尝试 {attempt} 轮）[/red]")
                console.print(Panel(errors_short, title="最后的校验错误", border_style="red"))
                break
            console.print(Panel(errors_short, title=f"{label} 校验失败，把错误喂回模型", border_style="yellow"))
            # ★ 核心：错误信息进对话，模型下一轮就能看到自己错哪了
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": "你输出的 JSON 未通过 Pydantic 校验，错误如下：\n"
                    + errors_short
                    + "\n\n请修正后重新输出完整 JSON（仍只输出 JSON 本身，不要解释）。",
                }
            )

    return None, history


def main() -> None:
    p = argparse.ArgumentParser(description="Unit 5 选做：校验失败自动重试")
    p.add_argument("--dirty", action="store_true", help="用内置脏简历触发重试")
    p.add_argument("--text", default=None)
    p.add_argument("--max-retries", type=int, default=2)
    args = p.parse_args()

    text = args.text or (DIRTY_RESUME if args.dirty else None)
    if text is None:
        from extract import SAMPLE_RESUME  # 正常简历

        text = SAMPLE_RESUME

    try:
        client = LLMClient(temperature=0.0, max_tokens=3000)
    except LLMConfigError as exc:
        console.print(f"[red]❌ 配置错误：{exc}[/red]")
        sys.exit(1)

    console.print(f"[bold cyan]重试式抽取[/bold cyan] ｜ max_retries={args.max_retries} ｜ 输入：{'脏简历' if args.dirty else '正常简历'}\n")
    resume, history = extract_with_retry(client, text, args.max_retries)

    if resume:
        console.print(Panel(resume.model_dump_json(indent=2), title="最终 Resume（通过校验）", border_style="green"))
    else:
        console.print("[yellow]未能产出合法结构 —— 这本身就是重要结论：重试不是万能的，Prompt 才是根因[/yellow]")

    rounds_used = next((h["rounds_used"] for h in reversed(history) if h.get("ok")), len(history))
    console.print(f"\n[dim]总轮数 {rounds_used} ｜ {client.usage.summary()}[/dim]")


if __name__ == "__main__":
    main()
