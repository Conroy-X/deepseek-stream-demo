"""Unit 3 · CLI 多轮对话（今天的主产出）。

运行：
    python chat.py
    python chat.py --stream                 # 选做：流式输出
    python chat.py --system "你只说文言文"   # 自定义人设
    python chat.py --temperature 0          # 更稳定的回答

核心认知：
    LLM 没有记忆。多轮对话 = 每次请求都把「System + 全部历史消息」重新发一遍。
    所以轮次越多，输入 token 越多、越贵、越慢 —— 这就是 Unit 10 要解决的问题。

可用命令：
    /exit            退出
    /reset           清空上下文
    /tokens          查看本会话用量与估算费用
    /system <文本>   修改 System Prompt
    /help            帮助
"""

from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from llm import LLMClient, LLMCallError, LLMConfigError, Message

console = Console()

DEFAULT_SYSTEM = (
    "你是一位 AI 应用开发导师。回答准确、简洁，涉及代码时给出可运行的 Python 示例。"
)

HELP_TEXT = """
[bold]可用命令[/bold]
  /exit, /quit     退出程序
  /reset           清空对话上下文（观察模型立刻"失忆"）
  /tokens          查看本会话 token 用量与估算费用
  /system <文本>   修改 System Prompt（例如：/system 你只能回答一个字）
  /help            显示本帮助
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Unit 3 · DeepSeek CLI Chat")
    parser.add_argument("--system", default=DEFAULT_SYSTEM, help="System Prompt")
    parser.add_argument("--temperature", type=float, default=0.3)
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--stream", action="store_true", help="流式输出（选做）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        client = LLMClient(temperature=args.temperature, max_tokens=args.max_tokens)
    except LLMConfigError as exc:
        console.print(f"[red]❌ 配置错误：{exc}[/red]")
        return 1

    # history 里只放 system / user / assistant 三种消息，且 user 与 assistant 交替出现。
    history: list[Message] = [Message("system", args.system)]

    console.print(
        Panel.fit(
            f"[bold green]Unit 3 · CLI Chat[/bold green]\n"
            f"模型 [cyan]{client.model}[/cyan] · temperature [cyan]{args.temperature}[/cyan]"
            f" · 流式 [cyan]{'开' if args.stream else '关'}[/cyan]\n"
            f"输入 /help 查看命令，/exit 退出",
            border_style="green",
        )
    )

    while True:
        try:
            user_input = console.input("\n[bold blue]你 >[/bold blue] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]再见！[/dim]")
            return 0

        if not user_input:
            continue

        # ---------------------------------------------------------- 命令处理
        if user_input in {"/exit", "/quit"}:
            console.print("[dim]再见！[/dim]")
            return 0

        if user_input == "/help":
            console.print(HELP_TEXT)
            continue

        if user_input == "/tokens":
            console.print(f"[yellow]{client.usage.summary()}[/yellow]")
            continue

        if user_input == "/reset":
            history = [Message("system", history[0].content if history else args.system)]
            console.print("[yellow]上下文已清空（历史消息全部丢弃）。[/yellow]")
            continue

        if user_input.startswith("/system"):
            new_system = user_input[len("/system") :].strip()
            if not new_system:
                console.print(f"[yellow]当前 System：{history[0].content}[/yellow]")
                continue
            if history and history[0].role == "system":
                history[0] = Message("system", new_system)
            else:
                history.insert(0, Message("system", new_system))
            console.print(f"[yellow]System Prompt 已更新：{new_system}[/yellow]")
            continue

        if user_input.startswith("/"):
            console.print("[red]未知命令，输入 /help 查看帮助。[/red]")
            continue

        # ---------------------------------------------------------- 对话请求
        history.append(Message("user", user_input))
        console.print("\n[bold green]AI >[/bold green]")

        try:
            if args.stream:
                parts: list[str] = []
                for delta in client.stream(history):
                    parts.append(delta)
                    console.print(delta, end="", soft_wrap=True, highlight=False)
                console.print()
                answer = "".join(parts)
            else:
                with console.status("[dim]思考中…[/dim]", spinner="dots"):
                    answer = client.chat(history)
                console.print(Markdown(answer))
        except LLMCallError as exc:
            # 失败时把刚加入的 user 消息回滚，保持 user/assistant 交替的干净状态
            history.pop()
            console.print(f"[red]❌ {exc}[/red]")
            console.print("[dim]提示：这次失败不计入上下文，可以直接重试。[/dim]")
            continue

        # 关键一步：把模型回答也存进历史，下一轮才会带上它
        history.append(Message("assistant", answer))

        # 实时提示上下文规模，直观感受「历史被重复计费」
        console.print(
            f"[dim]— {client.usage.summary()} · 当前上下文 {len(history)} 条消息 —[/dim]"
        )


if __name__ == "__main__":
    sys.exit(main())
