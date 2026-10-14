"""Day 4 · Prompt 实验台：同一任务跑多个 Prompt 变体，用**数据**而不是感觉下结论。

四个实验：
    python experiments.py variants      # 7 个变体对比（必做）
    python experiments.py fewshot       # 0 / 1 / 3 个示例对比（必做）
    python experiments.py system        # 指令放 System vs 放 User（必做）
    python experiments.py temperature   # temperature 对合规率的影响（选做）

常用参数：
    --cases 3        只用前 3 条用例（省钱）
    --dry-run        只拼 Prompt、不调用 API（先看长什么样，不花钱）
    --model deepseek-v4-pro
    --max-tokens 400

⚠️ 成本提示：`deepseek-flash` 是推理模型，即使只输出一个类别词，输出也在 60~100 token
   （90%+ 是推理 token）。所以"看起来很短的任务"也要花几十 token。本实验台每次运行
   都会先打印预估成本，跑完打印实际成本。
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

import prompts
from llm import LLMCallError, LLMClient, LLMConfigError

console = Console()
ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Day 4 Prompt 实验台")
    p.add_argument(
        "exp",
        nargs="?",
        default="variants",
        choices=["variants", "fewshot", "system", "temperature", "all"],
        help="跑哪个实验",
    )
    p.add_argument("--cases", type=int, default=len(prompts.CASES), help="用前 N 条用例")
    p.add_argument("--model", default=None)
    p.add_argument("--max-tokens", type=int, default=400)
    p.add_argument("--temperature", type=float, default=0.3)
    p.add_argument("--repeat", type=int, default=3, help="temperature 实验的重复次数")
    p.add_argument("--dry-run", action="store_true", help="只拼 Prompt 不调用 API")
    return p.parse_args()


ARGS = parse_args()


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def short(text: str, width: int = 34) -> str:
    t = " ".join(text.split())
    return t if len(t) <= width else t[: width - 1] + "…"


def estimate_cost(calls: int, input_tokens: int, output_tokens: int) -> float:
    """按目录价粗估：输入 1 元/百万，输出 4 元/百万。"""
    return calls * (input_tokens / 1e6 * 1.0 + output_tokens / 1e6 * 4.0)


def ask(client: LLMClient, variant: prompts.PromptVariant, case: dict[str, str]) -> dict:
    """跑一个变体 × 一条用例，返回结构化结果（方便统计）。"""
    system, user = prompts.render(variant, case["input"])
    try:
        text = client.chat_one(
            user, system or None, warn_truncated=False, max_tokens=ARGS.max_tokens
        )
    except LLMCallError as exc:
        return {"output": f"<失败: {exc}>", "ok": False, "compliant": False, "correct": False, "meta": None}

    meta = client.meta
    return {
        "output": text,
        "ok": True,
        "compliant": prompts.is_compliant(text),
        "correct": prompts.normalize(text) == case["expect"],
        "meta": meta,
    }


def print_results_table(rows: list[dict], title: str) -> None:
    t = Table(title=title, header_style="bold", show_lines=False)
    t.add_column("变体 / 用例", overflow="fold", max_width=22)
    t.add_column("模型输出", overflow="fold", max_width=26)
    t.add_column("合规", justify="center")
    t.add_column("正确", justify="center")
    t.add_column("输出token", justify="right")
    t.add_column("耗时ms", justify="right")
    for r in rows:
        meta = r.get("meta")
        out_tok = str(meta.completion_tokens) if meta else "-"
        ms = f"{meta.elapsed_ms:.0f}" if meta else "-"
        t.add_row(
            r["label"],
            short(r["output"]),
            "[green]✓[/green]" if r["compliant"] else "[red]✗[/red]",
            "[green]✓[/green]" if r["correct"] else "[red]✗[/red]",
            out_tok,
            ms,
        )
    console.print(t)


def summarize(rows: list[dict], group_key: str = "variant") -> list[dict]:
    """按变体聚合：合规率、准确率、平均输出 token。"""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r[group_key], []).append(r)

    stats = []
    for name, items in groups.items():
        ok_items = [i for i in items if i["ok"]]
        stats.append(
            {
                "variant": name,
                "n": len(items),
                "compliance": sum(i["compliant"] for i in items) / len(items),
                "accuracy": sum(i["correct"] for i in items) / len(items),
                "avg_out": statistics.mean([i["meta"].completion_tokens for i in ok_items]) if ok_items else 0,
                "avg_ms": statistics.mean([i["meta"].elapsed_ms for i in ok_items]) if ok_items else 0,
            }
        )
    return stats


def print_summary(stats: list[dict], title: str, baseline_key: str | None = None) -> None:
    t = Table(title=title, header_style="bold")
    t.add_column("变体", overflow="fold", max_width=30)
    t.add_column("合规率", justify="right")
    t.add_column("准确率", justify="right")
    t.add_column("平均输出token", justify="right")
    t.add_column("平均耗时ms", justify="right")
    for s in stats:
        comp = f"{s['compliance']:.0%}"
        acc = f"{s['accuracy']:.0%}"
        if baseline_key and s["variant"] == baseline_key:
            comp, acc = f"[bold]{comp}[/bold]", f"[bold]{acc}[/bold]"
        t.add_row(s["variant"], comp, acc, f"{s['avg_out']:.0f}", f"{s['avg_ms']:.0f}")
    console.print(t)


def save_log(name: str, payload: dict) -> Path:
    LOGS.mkdir(exist_ok=True)
    path = LOGS / f"{name}-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 实验 1：变体对比（必做）
# ---------------------------------------------------------------------------
def exp_variants(client: LLMClient) -> list[dict]:
    cases = prompts.CASES[: ARGS.cases]
    console.print(
        f"\n[bold cyan]实验 1 · 变体对比[/bold cyan]：{len(prompts.VARIANTS)} 个变体 × {len(cases)} 条用例"
        f" = {len(prompts.VARIANTS) * len(cases)} 次调用\n"
    )
    for v in prompts.VARIANTS:
        console.print(f"  [dim]·[/dim] [bold]{v.key}[/bold] {v.name}\n    [dim]假设：{v.hypothesis}[/dim]")

    if ARGS.dry_run:
        console.print("\n[yellow]--dry-run：只展示 v5 的完整 Prompt，不调用 API[/yellow]")
        system, user = prompts.render(prompts.VARIANTS_BY_KEY["v5_full"], cases[0]["input"])
        console.print(f"\n[bold]System Prompt[/bold]\n{system}\n\n[bold]User Prompt[/bold]\n{user}")
        return []

    console.print(
        f"\n[dim]预估成本：约 ¥{estimate_cost(len(prompts.VARIANTS) * len(cases), 300, 100):.4f}[/dim]"
    )
    rows: list[dict] = []
    for v in prompts.VARIANTS:
        for case in cases:
            r = ask(client, v, case)
            r["variant"], r["variant_key"] = v.name, v.key
            r["label"] = f"{v.key}\n{short(case['input'], 14)}"
            rows.append(r)

    print_results_table(rows, "实验 1 · 逐条结果")
    stats = summarize(rows)
    print_summary(stats, "实验 1 · 汇总（合规率 = 输出格式是否严格合法；准确率 = 类别是否正确）")

    gaps = [s["variant"] for s in stats if s["compliance"] < 1.0]
    if gaps:
        console.print(f"\n[yellow]合规率未满分的变体：{', '.join(gaps)}[/yellow]")
        console.print("[dim]→ 对比它们的 System Prompt，被去掉的那一段就是「合规率杀手」。[/dim]")
    return rows


# ---------------------------------------------------------------------------
# 实验 2：few-shot 数量对比（必做）
# ---------------------------------------------------------------------------
def exp_fewshot(client: LLMClient) -> list[dict]:
    cases = prompts.CASES[: ARGS.cases]
    shots = [0, 1, 3]

    def make(shot: int) -> prompts.PromptVariant:
        ex = prompts.FEW_SHOT_EXAMPLES[:shot]
        text = prompts.five_part_text(
            role="你是资深的用户反馈质检员。",
            task=prompts.TASK,
            constraints=prompts._CONSTRAINTS,
            output_format=prompts._OUTPUT_FORMAT,
            examples=ex,
        )
        return prompts.PromptVariant(
            key=f"{shot}-shot",
            name=f"{shot} 个示例",
            system=text,
            user="这条用户反馈属于哪一类？\n{input}",
            hypothesis="示例数量对格式与边界的影响",
        )

    console.print(f"\n[bold cyan]实验 2 · few-shot 数量对比[/bold cyan]：0 / 1 / 3 示例 × {len(cases)} 条用例")
    if ARGS.dry_run:
        for s in shots:
            v = make(s)
            console.print(f"\n[bold]{v.name}[/bold] 的 System Prompt：\n{v.system}")
        return []

    rows: list[dict] = []
    for s in shots:
        v = make(s)
        for case in cases:
            r = ask(client, v, case)
            r["variant"], r["variant_key"] = v.name, v.key
            r["label"] = f"{v.name}\n{short(case['input'], 14)}"
            rows.append(r)

    print_results_table(rows, "实验 2 · 逐条结果")
    print_summary(summarize(rows), "实验 2 · 汇总（示例从 0 加到 3，看哪一列在变）")
    console.print(
        "[dim]要观察的重点：示例增加时，**输入 token 涨了多少**、合规率涨了多少。"
        "如果合规率没变而 token 翻倍，说明示例对这类任务性价比低。[/dim]"
    )
    return rows


# ---------------------------------------------------------------------------
# 实验 3：指令放 System vs 放 User（必做，直击 Day 4 完成标准）
# ---------------------------------------------------------------------------
def exp_system(client: LLMClient) -> list[dict]:
    cases = prompts.CASES[: ARGS.cases]
    pair = [prompts.VARIANTS_BY_KEY["v5_full"], prompts.VARIANTS_BY_KEY["v6_instruction_in_user"]]

    console.print(
        f"\n[bold cyan]实验 3 · 指令放 System vs 放 User[/bold cyan]：同一份五段式文本，只换位置\n"
    )
    if ARGS.dry_run:
        for v in pair:
            console.print(f"[bold]{v.name}[/bold]\n  system = {short(v.system, 80)}\n  user   = {short(v.user, 80)}\n")
        return []

    rows: list[dict] = []
    for v in pair:
        for case in cases:
            r = ask(client, v, case)
            r["variant"], r["variant_key"] = v.name, v.key
            r["label"] = f"{v.key}\n{short(case['input'], 14)}"
            rows.append(r)

    print_results_table(rows, "实验 3 · 逐条结果")
    print_summary(summarize(rows), "实验 3 · 汇总（重点看平均输入 token 的差距）")

    s_stats = summarize(rows)
    if len(s_stats) == 2 and s_stats[1]["avg_out"]:
        diff = s_stats[1]["avg_out"] - s_stats[0]["avg_out"]
        console.print(
            f"\n[dim]两者输出 token 差 {diff:+.0f}；再看 run 输出的 usage 里 prompt_tokens 的差距。[/dim]"
        )
    console.print(
        "[dim]结论方向：短期单轮任务里两者效果接近；差异会在**多轮对话**和**指令优先级**上放大 —— "
        "System 是长期规则（且可能被缓存/不计入重发），User 是本次输入。Day 10 会用多轮场景验证。[/dim]"
    )
    return rows


# ---------------------------------------------------------------------------
# 实验 4：temperature 对合规率的影响（选做）
# ---------------------------------------------------------------------------
def exp_temperature(client: LLMClient) -> list[dict]:
    cases = prompts.CASES[: ARGS.cases]
    v = prompts.VARIANTS_BY_KEY["v3_format"]
    temps = [0.0, 1.2]

    console.print(
        f"\n[bold cyan]实验 4 · temperature 对合规率的影响[/bold cyan]："
        f"{temps} × {len(cases)} 条用例 × {ARGS.repeat} 次重复\n"
    )
    if ARGS.dry_run:
        console.print("[yellow]--dry-run：本实验无 preview[/yellow]")
        return []

    rows: list[dict] = []
    for temp in temps:
        for case in cases:
            for i in range(ARGS.repeat):
                system, user = prompts.render(v, case["input"])
                try:
                    text = client.chat_one(
                        user, system, temperature=temp, max_tokens=ARGS.max_tokens, warn_truncated=False
                    )
                    meta, ok = client.meta, True
                except LLMCallError as exc:
                    text, meta, ok = f"<失败: {exc}>", None, False
                rows.append(
                    {
                        "variant": f"temperature={temp}",
                        "variant_key": f"t{temp}",
                        "label": f"T={temp} #{i + 1}\n{short(case['input'], 14)}",
                        "output": text,
                        "ok": ok,
                        "compliant": prompts.is_compliant(text),
                        "correct": prompts.normalize(text) == case["expect"],
                        "meta": meta,
                    }
                )

    print_results_table(rows, "实验 4 · 逐条结果")
    print_summary(summarize(rows), f"实验 4 · 汇总（每档 {len(cases) * ARGS.repeat} 次）")
    console.print(
        "[dim]要观察的重点：temperature 升高后**同一条输入的输出是否漂移**、合规率是否下降。"
        "结论通常是：分类/抽取这类任务，temperature 应该压到 0，把随机性留给创意类任务。[/dim]"
    )
    return rows


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main() -> None:
    try:
        client = LLMClient(model=ARGS.model, temperature=ARGS.temperature, max_tokens=ARGS.max_tokens)
    except LLMConfigError as exc:
        console.print(f"[red]❌ 配置错误：{exc}[/red]")
        raise SystemExit(1)

    console.print(f"[dim]模型 {client.model} ｜ temperature {ARGS.temperature} ｜ max_tokens {ARGS.max_tokens}[/dim]")

    runner = {
        "variants": exp_variants,
        "fewshot": exp_fewshot,
        "system": exp_system,
        "temperature": exp_temperature,
    }
    exps = list(runner) if ARGS.exp == "all" else [ARGS.exp]

    all_rows: list[dict] = []
    for name in exps:
        client.usage.calls = 0
        client.usage.prompt_tokens = 0
        client.usage.completion_tokens = 0
        client.usage.reasoning_tokens = 0
        rows = runner[name](client)
        all_rows += rows
        if not ARGS.dry_run:
            console.print(f"\n[bold green]{name} 本次用量：[/bold green]{client.usage.summary()}\n")

    if not ARGS.dry_run and all_rows:
        path = save_log(
            "experiment-" + ARGS.exp,
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "model": client.model,
                "temperature": ARGS.temperature,
                "max_tokens": ARGS.max_tokens,
                "rows": [
                    {
                        "variant": r["variant_key"],
                        "input": r["label"].split("\n")[-1],
                        "output": r["output"],
                        "compliant": r["compliant"],
                        "correct": r["correct"],
                        "completion_tokens": r["meta"].completion_tokens if r["meta"] else None,
                        "reasoning_tokens": r["meta"].reasoning_tokens if r["meta"] else None,
                        "elapsed_ms": r["meta"].elapsed_ms if r["meta"] else None,
                    }
                    for r in all_rows
                ],
            },
        )
        console.print(f"[dim]结果已存 {path}（Day 4 笔记可以直接引用这些数字）[/dim]")


if __name__ == "__main__":
    main()
