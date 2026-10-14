"""Day 4 · 选做：代码审查助手（用 few-shot 把输出格式钉死）。

这是计划里 Day 4 的「选做」项，也是三天里第一个"看起来有用"的小工具：
    输入一段代码 → 输出固定格式的问题清单。

它演示了一件比"写得好看"更重要的事：
    **few-shot 的真正价值不是提高准确率，而是锁定输出结构**，
    这样下游才能稳定解析（Day 5 的结构化输出就是在这条路上继续走）。

用法：
    python code_reviewer.py                 # 审查内置的示例代码（有 3 个刻意埋的坑）
    python code_reviewer.py --file x.py     # 审查指定文件
    python code_reviewer.py --code "..."    # 直接传代码
    python code_reviewer.py --show-prompt   # 只看 Prompt（不花钱）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from rich.console import Console

from llm import LLMCallError, LLMClient, LLMConfigError

console = Console()

# ---------------------------------------------------------------------------
# 一、few-shot 示例：只给"输入代码 → 输出格式"的样例，让模型照着格式走
# ---------------------------------------------------------------------------
ROLE = "你是严格但务实的 Python 代码审查员。"
OUTPUT_RULE = """输出格式（严格遵守，每条问题一行，不要输出别的任何内容）：
[级别] 行号 | 问题 | 修改建议
级别只能是：BLOCKER / MAJOR / MINOR"""

CONSTRAINTS = [
    "只报真实存在的问题，不要编造，也不要报纯风格偏好",
    "同一行有多个问题就分多行写",
    "最多报 5 条，按严重程度从高到低排序",
    "没有问题时输出：OK 没有发现问题",
]

EXAMPLES = [
    (
        "def div(a, b):\n    return a / b",
        "[MAJOR] 2 | 未处理除数为 0 的情况，b=0 时会抛 ZeroDivisionError | 加 if b == 0 判断或抛自定义异常",
    ),
    (
        "import os\n\ndef read(p):\n    f = open(p)\n    return f.read()",
        "[MAJOR] 4 | 文件句柄没有关闭，泄漏资源 | 用 with open(p) as f: 改写\n"
        "[MINOR] 1 | os 导入后未使用 | 删除该导入",
    ),
]

# 刻意埋了 3 个坑：裸 except、SQL 拼接、可变的默认参数
SAMPLE_CODE = '''import sqlite3

def get_user(name, conn):
    cur = conn.cursor()
    sql = "SELECT * FROM users WHERE name = '" + name + "'"
    try:
        cur.execute(sql)
        return cur.fetchall()
    except:
        return None

def add_tags(tag, tags=[]):
    tags.append(tag)
    return tags
'''

OUTPUT_RE = re.compile(r"^\[(BLOCKER|MAJOR|MINOR)\]\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+)$", re.MULTILINE)


def build_prompt(code: str) -> tuple[str, str]:
    examples = "\n\n".join(f"输入：\n{c}\n输出：\n{o}" for c, o in EXAMPLES)
    system = (
        f"角色：{ROLE}\n\n"
        f"任务：审查用户给出的 Python 代码，指出其中的缺陷。\n\n"
        f"约束：\n" + "\n".join(f"- {c}" for c in CONSTRAINTS) + "\n\n"
        f"{OUTPUT_RULE}\n\n"
        f"示例：\n{examples}"
    )
    user = f"输入：\n{code}"
    return system, user


def parse_issues(text: str) -> list[dict[str, str]]:
    return [
        {"level": m.group(1), "line": m.group(2), "problem": m.group(3), "fix": m.group(4)}
        for m in OUTPUT_RE.finditer(text)
    ]


def main() -> None:
    p = argparse.ArgumentParser(description="Day 4 选做：few-shot 代码审查助手")
    p.add_argument("--file", help="要审查的 Python 文件")
    p.add_argument("--code", help="直接传代码字符串")
    p.add_argument("--show-prompt", action="store_true", help="只打印 Prompt，不调用 API")
    p.add_argument("--max-tokens", type=int, default=900)
    args = p.parse_args()

    if args.file:
        code = Path(args.file).read_text(encoding="utf-8")
    elif args.code:
        code = args.code
    else:
        code = SAMPLE_CODE
        console.print("[dim]未指定代码，使用内置示例（含 3 个刻意埋的坑）[/dim]\n")

    system, user = build_prompt(code)

    if args.show_prompt:
        console.print(f"[bold]System Prompt[/bold]\n{system}\n\n[bold]User Prompt[/bold]\n{user}")
        return

    try:
        client = LLMClient(max_tokens=args.max_tokens, temperature=0.0)
    except LLMConfigError as exc:
        console.print(f"[red]❌ 配置错误：{exc}[/red]")
        sys.exit(1)

    console.print("[bold]待审查代码[/bold]")
    console.print(code, style="dim")
    console.print("\n[bold]审查结果[/bold]")

    try:
        text = client.chat_one(user, system)
    except LLMCallError as exc:
        console.print(f"[red]❌ 调用失败：{exc}[/red]")
        sys.exit(1)

    console.print(text)

    issues = parse_issues(text)
    console.print()
    if issues:
        console.print(f"[green]✅ 解析出 {len(issues)} 条结构化问题[/green]（顺序/格式均符合模板）")
        for i in issues:
            color = {"BLOCKER": "red", "MAJOR": "yellow", "MINOR": "cyan"}.get(i["level"], "white")
            console.print(f"  [{color}]{i['level']}[/{color}] 第 {i['line']} 行：{i['problem']}")
    else:
        console.print("[yellow]⚠️ 一条都没解析出来：说明模型没严格按格式输出。[/yellow]")
        console.print("[dim]这就是 few-shot 要解决的问题 —— 试着给示例再补一条，或者把 OUTPUT_RULE 写得更死。[/dim]")

    console.print(f"\n[dim]{client.usage.summary()}[/dim]")
    console.print("[dim]内置示例里埋的坑：① 裸 except 吞异常 ② SQL 字符串拼接（注入风险）③ 可变默认参数 tags=[]。看模型抓到几个。[/dim]")


if __name__ == "__main__":
    main()
