"""Day 5 · 抽取器：自由文本简历 → Pydantic 校验过的 Resume 对象。

三种模式对比（Day 5 的核心认知）：
    ① naive      直接拿模型输出 json.loads —— 会死在 markdown 代码块/前后闲话上
    ② json_mode  API 层开 response_format —— 保证是合法 JSON，但**不管字段对不对**
    ③ schema     Prompt 里塞 JSON Schema + 解析后 Pydantic 校验 —— 字段级兜底

用法：
    python extract.py                    # 默认：内置示例简历 + 三种模式对比
    python extract.py --mode schema      # 只跑 schema 模式
    python extract.py --text "简历内容"   # 抽取自定义文本
    python extract.py --rounds 5         # 连抽 5 次统计通过率（完成标准：10 次至少 9 次）
    python extract.py --adversarial      # 喂一段非简历文本，看校验怎么拦住（C 段实验）
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm import LLMCallError, LLMClient, LLMConfigError  # noqa: E402
from models import RESUME_JSON_INSTRUCTION, Resume, build_extract_messages  # noqa: E402

console = Console()

SAMPLE_RESUME = """李玉斌（rik）
邮箱：rik@example.com ｜ 电话：138****0000

工作年限：6 年半

技能：
- 前端：TypeScript、Angular、Vue
- 后端：Node.js、Python、FastAPI
- 工具：Git、Docker、Linux

工作经历：
1. 华为 OD · 前端工程师 · 2022-03 至今
   - 负责云管平台前端架构，Angular +微前端改造，包体积降 40%
   - 搭建 Node.js BFF 层，接口聚合后首屏耗时从 3.2s 降到 1.4s
2. 某互联网公司 · Web 开发工程师 · 2019-07 ~ 2022-02
   - 用 Vue 开发运营活动系统，支撑 20+ 场活动上线

教育：
上海电机学院 · 本科 · 网络工程 · 2019 届"""

ADVERSARIAL_TEXT = """今天天气不错，我和朋友去打了场篮球，比分 21:15。
晚上吃了火锅，微辣，花了 168 元。回来路上听了期播客，讲的是古罗马历史，
其中凯撒渡过卢比孔河的故事挺有意思。"""

FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def parse_naive(text: str) -> dict:
    """① 朴素模式：直接 json.loads。教学目的：展示它有多脆。"""
    return json.loads(text)


def parse_fenced(text: str) -> dict:
    """①' 朴素模式 + 补丁：先剥 markdown 围栏再 loads（工程里常见的第一反应）。"""
    m = FENCE_RE.search(text)
    return json.loads(m.group(1) if m else text)


def parse_schema(text: str) -> Resume:
    """③ schema 模式：剥围栏 → Pydantic 校验（失败会抛 ValidationError）。"""
    m = FENCE_RE.search(text)
    clean = m.group(1) if m else text.strip()
    return Resume.model_validate_json(clean)


def run_mode(client: LLMClient, mode: str, text: str) -> dict:
    """跑一种模式，返回 {ok, result, error, raw_preview, tokens}。"""
    messages = build_extract_messages(text)

    extra_body = {"response_format": {"type": "json_object"}} if mode == "json_mode" else None
    if mode == "schema":
        messages[0]["content"] += (
            "\n\nJSON Schema（字段定义的权威版本，与上述要求冲突时以 Schema 为准）：\n"
            + json.dumps(Resume.model_json_schema(), ensure_ascii=False)
        )

    try:
        raw = client.chat_one(messages[1]["content"], messages[0]["content"], extra_body=extra_body, max_tokens=3000)
    except LLMCallError as exc:
        return {"ok": False, "error": f"调用失败: {exc}", "raw_preview": "", "tokens": 0}

    try:
        if mode == "naive":
            result: object = parse_naive(raw)
            note = "（未做任何防护）"
        elif mode == "json_mode":
            result = json.loads(raw)
            note = "（API 保证是 JSON，但字段不受控）"
        else:
            result = parse_schema(raw)
            note = "（Pydantic 字段级校验）"
        return {"ok": True, "result": result, "note": note, "raw_preview": raw[:120], "tokens": client.meta.completion_tokens}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:180]}", "raw_preview": raw[:160], "tokens": client.meta.completion_tokens}


def main() -> None:
    p = argparse.ArgumentParser(description="Day 5 简历结构化抽取")
    p.add_argument("--mode", choices=["naive", "json_mode", "schema"], default=None, help="只跑指定模式")
    p.add_argument("--text", default=None, help="自定义简历文本")
    p.add_argument("--rounds", type=int, default=1, help="重复次数（测稳定性，如 10）")
    p.add_argument("--adversarial", action="store_true", help="喂非简历文本，看校验拦截")
    p.add_argument("--max-tokens", type=int, default=3000)
    args = p.parse_args()

    text = ADVERSARIAL_TEXT if args.adversarial else (args.text or SAMPLE_RESUME)

    try:
        client = LLMClient(temperature=0.0, max_tokens=args.max_tokens)
    except LLMConfigError as exc:
        console.print(f"[red]❌ 配置错误：{exc}[/red]")
        sys.exit(1)

    modes = [args.mode] if args.mode else ["naive", "json_mode", "schema"]
    header = "对抗输入（非简历）" if args.adversarial else "示例简历"
    console.print(f"[bold cyan]Day 5 抽取实验[/bold cyan] ｜ 输入：{header} ｜ 模式：{' → '.join(modes)}\n")

    t = Table(title=f"三种模式对比 × {args.rounds} 轮", header_style="bold")
    t.add_column("模式", style="cyan")
    t.add_column("结果", justify="center")
    t.add_column("说明")
    t.add_column("输出token", justify="right")

    for mode in modes:
        ok_n = 0
        last: dict = {}
        for i in range(args.rounds):
            last = run_mode(client, mode, text)
            ok_n += bool(last["ok"])
            if not last["ok"] and args.rounds == 1:
                console.print(f"[dim]── {mode} 的原始输出前 160 字 ──\n{last['raw_preview']}[/dim]\n")
        rate = ok_n / args.rounds
        verdict = f"{ok_n}/{args.rounds}"
        if last["ok"]:
            detail = "成功 " + last.get("note", "")
            if isinstance(last.get("result"), Resume):
                r: Resume = last["result"]
                detail += f"\n姓名={r.name}｜年限={r.years_of_experience}｜职级={r.seniority.value}｜技能{len(r.skills)}项"
        else:
            detail = f"失败：{last['error']}"
        t.add_row(mode, f"[green]{verdict}[/green]" if rate == 1 else f"[yellow]{verdict}[/yellow]", detail, str(last.get("tokens", "-")))

    console.print(t)

    if args.adversarial:
        console.print(
            "\n[bold]对抗实验要看什么：[/bold]\n"
            "· naive/json_mode 可能'成功'——模型硬编了一个人名出来（**幻觉**，schema 模式也可能）\n"
            "· 但 years_of_experience 这类字段会被 Pydantic 的范围校验拦下（ge=0/le=50）\n"
            "· 结论：校验拦的是[u]结构性错误[/u]，拦不住[u]内容幻觉[/u]——所以抽取类任务要加来源核验"
        )
    else:
        console.print(
            "\n[dim]完成标准对照：10 次抽取至少 9 次通过 Pydantic 校验 → 跑 --rounds 10 --mode schema 验证[/dim]"
        )
    console.print(f"\n[dim]{client.usage.summary()}[/dim]")


if __name__ == "__main__":
    main()
