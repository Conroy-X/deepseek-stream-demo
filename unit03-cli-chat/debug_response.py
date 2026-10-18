#!/usr/bin/env python
"""Unit 3 · 调试脚本：把一次 LLM 调用的「请求 → 原始 HTTP 响应 → 解析字段」全链路打出来。

用法（在 unit03-cli-chat 目录下执行）：
    python debug_response.py                 # 默认：请求视图 + 原始 JSON body + 字段解释
    python debug_response.py --raw-http      # 额外打印 SDK 的 HTTP 层日志（POST 行 / 状态码）
    python debug_response.py --stream        # 用流式调用，看每个 chunk 长什么样
    python debug_response.py -p "你的问题"    # 换个问题
    python debug_response.py --json-only     # 只吐原始 JSON（方便重定向到文件）

每次运行都会把原始响应写入 logs/last_response.json，方便和历史对比。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM 调用调试器：看清请求与原始响应")
    p.add_argument("-p", "--prompt", default="用一句话解释什么是 LLM 的 token。")
    p.add_argument("-s", "--system", default="你是一位耐心的 AI 应用开发导师，回答简洁。")
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--max-tokens", type=int, default=256)
    p.add_argument("--stream", action="store_true", help="用流式调用，逐块打印 chunk")
    p.add_argument("--raw-http", action="store_true", help="打印 SDK 的 HTTP 层调试日志")
    p.add_argument("--json-only", action="store_true", help="只输出原始 JSON 响应")
    return p.parse_args()


ARGS = parse_args()

# OPENAI_LOG=debug 必须在 import openai 之前设置，否则 SDK 已经初始化完日志系统。
if ARGS.raw_http:
    os.environ["OPENAI_LOG"] = "debug"

from dotenv import load_dotenv  # noqa: E402
from openai import OpenAI  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.json import JSON  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.table import Table  # noqa: E402

console = Console()

# ---------------------------------------------------------------- 配置读取
load_dotenv(ROOT / ".env")

API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip()

if not API_KEY or API_KEY.startswith("sk-你的"):
    console.print("[red]❌ 没有读到 DEEPSEEK_API_KEY[/red]，请先按 README 配置 .env")
    sys.exit(1)


def mask(key: str) -> str:
    """只露头尾，日志/截图里可以放心展示。"""
    return f"{key[:7]}…{key[-4:]}" if len(key) > 14 else "***"


# 计费参考价（元 / 百万 token），空闲时段；高峰翻倍，详见官方价格页
PRICE_IN = 1.0
PRICE_OUT = 4.0

client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=60.0)

LOGS = ROOT / "logs"
LOGS.mkdir(exist_ok=True)


def show_request() -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": ARGS.system},
            {"role": "user", "content": ARGS.prompt},
        ],
        "temperature": ARGS.temperature,
        "max_tokens": ARGS.max_tokens,
        "stream": ARGS.stream,
    }
    console.print(
        Panel(
            JSON.from_data(payload),
            title="① 请求 payload（SDK 转成 JSON 发给服务端）",
            border_style="cyan",
        )
    )
    console.print(f"[dim]POST {BASE_URL}/chat/completions[/dim]")
    console.print(f"[dim]Authorization: Bearer {mask(API_KEY)}   （Key 只露头尾）[/dim]\n")
    return payload


# 排查时最先看的头（★ 标记）
KEY_HEADERS = {
    "x-ds-trace-id",  # DeepSeek 的追踪 ID，报障唯一凭证
    "content-type",
    "x-request-id",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "retry-after",  # 被限速时告诉你要等几秒
}


def dump_raw(raw_text: str, headers: dict | None, elapsed_ms: float, status_code: int | None = None) -> None:
    (LOGS / "last_response.json").write_text(raw_text, encoding="utf-8")

    if ARGS.json_only:
        print(raw_text)
        return

    console.print(Panel(JSON(raw_text), title="② 原始响应 body（服务端返回的完整 JSON）", border_style="green"))
    console.print(f"[dim]原始 body 已存到 logs/last_response.json（{len(raw_text)} 字符）[/dim]\n")

    if not headers:
        return

    items = list(headers.items())
    starred = [kv for kv in items if kv[0].lower() in KEY_HEADERS]
    others = [kv for kv in items if kv[0].lower() not in KEY_HEADERS]

    t = Table(
        title=f"③ 响应头（HTTP {status_code} ｜ 共 {len(items)} 个，★ = 排查时最先看）",
        header_style="bold magenta",
        show_header=True,
    )
    t.add_column("Header", no_wrap=True)
    t.add_column("Value", overflow="fold")
    for k, v in starred:
        t.add_row(f"[bold cyan]★ {k}[/bold cyan]", f"[bold]{v}[/bold]")
    for k, v in others:
        t.add_row(f"  [cyan]{k}[/cyan]", str(v))
    console.print(t)

    trace_id = dict(headers).get("x-ds-trace-id")
    if trace_id:
        console.print(f"[dim]代码里取：raw.headers['x-ds-trace-id'] → {trace_id}[/dim]")
        console.print(f"[dim]日志里带上它，线上问题可直接让服务商定位：trace_id={trace_id}[/dim]\n")
    else:
        console.print("[yellow]没有 x-ds-trace-id（非 DeepSeek 服务商时可能叫 x-request-id）[/yellow]\n")


def explain(resp, elapsed_ms: float) -> None:
    choice = resp.choices[0]
    usage = resp.usage
    usage_dict = usage.model_dump() if usage else {}

    cost = usage.prompt_tokens / 1e6 * PRICE_IN + usage.completion_tokens / 1e6 * PRICE_OUT
    tps = usage.completion_tokens / (elapsed_ms / 1000) if elapsed_ms > 0 else 0

    if not ARGS.json_only:
        t = Table(title="④ 解析后的关键字段（这些才是你代码里会用的）", header_style="bold")
        t.add_column("字段", style="cyan", no_wrap=True)
        t.add_column("值", overflow="fold")
        t.add_column("含义", style="dim")
        rows = [
            ("id", resp.id, "服务端请求 ID，报障时提供"),
            ("model", resp.model, "实际使用的模型（可能被服务端映射）"),
            ("created", resp.created, "Unix 时间戳"),
            ("choices[0].message.role", choice.message.role, "固定 assistant"),
            ("choices[0].finish_reason", choice.finish_reason, "stop=正常结束 / length=被 max_tokens 截断"),
            ("usage.prompt_tokens", usage.prompt_tokens, "输入 token（含 system+user+历史）"),
            ("usage.completion_tokens", usage.completion_tokens, "输出 token（计费大头）"),
            ("usage.total_tokens", usage.total_tokens, "合计"),
        ]
        for extra in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            if extra in usage_dict:
                rows.append((f"usage.{extra}", usage_dict[extra], "DeepSeek 上下文缓存命中/未命中（省钱关键）"))
        rows += [
            ("latency", f"{elapsed_ms:.0f} ms", "端到端耗时"),
            ("throughput", f"{tps:.1f} tok/s", "输出速度"),
            ("cost", f"¥{cost:.6f}", "按目录价估算"),
        ]
        for name, value, note in rows:
            t.add_row(name, str(value), note)
        console.print(t)
        console.print()

        console.print(Panel(choice.message.content or "(空)", title="⑤ 回答正文", border_style="yellow"))


def run_normal(payload: dict) -> None:
    t0 = time.perf_counter()
    try:
        # with_raw_response 能同时拿到「原始 body + 响应头 + 解析后的对象」
        raw = client.chat.completions.with_raw_response.create(**payload)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]❌ 调用失败：{type(exc).__name__}: {exc}[/red]")
        sys.exit(1)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    resp = raw.parse()
    dump_raw(raw.text, dict(raw.headers), elapsed_ms, status_code=raw.status_code)
    explain(resp, elapsed_ms)


def run_stream(payload: dict) -> None:
    payload = {**payload, "stream": True, "stream_options": {"include_usage": True}}
    console.print("[bold]流式模式[/bold]：把前 5 个 chunk 的原始 JSON 打印出来，再看拼装后的回答\n")

    t0 = time.perf_counter()
    first_token_ms = None
    text_parts: list[str] = []
    usage = None

    try:
        stream = client.chat.completions.create(**payload)
        for i, chunk in enumerate(stream):
            if i < 5:
                console.print(f"[dim]chunk #{i}[/dim]")
                console.print(JSON.from_data(chunk.model_dump()))
            if chunk.usage:
                usage = chunk.usage
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                if first_token_ms is None:
                    first_token_ms = (time.perf_counter() - t0) * 1000
                text_parts.append(delta)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]❌ 流式调用失败：{type(exc).__name__}: {exc}[/red]")
        sys.exit(1)

    total_ms = (time.perf_counter() - t0) * 1000
    answer = "".join(text_parts)

    console.print()
    console.print(Panel(answer, title="拼装后的完整回答", border_style="yellow"))
    if usage:
        cost = usage.prompt_tokens / 1e6 * PRICE_IN + usage.completion_tokens / 1e6 * PRICE_OUT
        console.print(
            f"[green]首 token 延迟[/green] {first_token_ms or 0:.0f} ms ｜ "
            f"[green]总耗时[/green] {total_ms:.0f} ms ｜ "
            f"[green]用量[/green] 输入 {usage.prompt_tokens} / 输出 {usage.completion_tokens} "
            f"｜ [green]约[/green] ¥{cost:.6f}"
        )
        console.print("[dim]注意：流式下 usage 只出现在最后一块，所以统计必须在循环里接收。[/dim]")
    else:
        console.print("[yellow]没拿到 usage（可能服务端没返回），这正说明流式的 usage 需要 include_usage。[/yellow]")


def main() -> None:
    payload = show_request()
    if ARGS.stream:
        run_stream(payload)
    else:
        run_normal(payload)


if __name__ == "__main__":
    main()
