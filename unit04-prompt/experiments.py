"""Unit 4 · Prompt 实验台：同一任务跑多个 Prompt 变体，用**数据**而不是感觉下结论。

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

from rich.console import Console
from rich.table import Table

import prompts
from llm import LLMCallError, LLMClient, LLMConfigError

console = Console()
ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    ┌─ argparse 是什么？────────────────────────────────────────────────┐
    │ Python 标准库的命令行解析器。你敲                                   │
    │     python experiments.py fewshot --cases 2 --dry-run              │
    │ 它负责把这串字符变成程序里的变量，顺便帮你做类型转换、默认值、       │
    │ 非法值报错。相当于 Node 里的 commander / yargs。                    │
    └────────────────────────────────────────────────────────────────────┘

    解析结果是一个 Namespace 对象，属性名 = 参数名（中划线变下划线）：
        args.exp  args.cases  args.max_tokens  args.dry_run ...
    所以 `--max-tokens 400` 在代码里用 `ARGS.max_tokens` 取。
    """
    # prog 名 + 描述只会显示在 --help 里，不影响运行
    p = argparse.ArgumentParser(description="Unit 4 Prompt 实验台")

    # ---- ① 位置参数 exp（不带 -- 前缀的那个）---------------------------
    # 你敲 `python experiments.py fewshot` 时，"fewshot" 就落在 exp 上。
    p.add_argument(
        "exp",
        # nargs="?" 表示「最多 1 个、可以不传」：
        #   python experiments.py            → exp = "variants"（走 default）
        #   python experiments.py fewshot    → exp = "fewshot"
        #   python experiments.py a b        → 报错（多了，装不下）
        # 对比：nargs="*" 是「要几个都行」，nargs=3 是「必须正好 3 个」。
        nargs="?",
        # 不传时的默认值：什么都不敲 = 跑 variants 实验
        default="variants",
        # choices = 白名单。传了白名单之外的值（如 `python experiments.py foo`）
        # argparse 直接报错退出，不用我们自己写 if 判断。
        # 这就是为什么脚本能保证 exp 一定是这五个值之一。
        choices=["variants", "fewshot", "system", "temperature", "all"],
        help="跑哪个实验",
    )

    # ---- ② 可选参数（--开关）-------------------------------------------
    # 注意三种典型写法，后面 90 天会一直用到：

    # 写法 A：带值 + 类型转换
    # `--cases 2` → 字符串 "2" 经 type=int 变成数字 2。
    # 不写 type 的话拿到的是字符串，做切片 CASES[:'2'] 会炸。
    # default=len(prompts.CASES) 是「运行时求值」：
    #   prompts.CASES 有 5 条用例，所以默认 = 5（全用）。
    #   --cases 2 就是"只用前 2 条"，省钱用的。
    p.add_argument("--cases", type=int, default=len(prompts.CASES), help="用前 N 条用例")

    # 写法 A'：带值 + 默认 None =「用户没传就沿用别处的默认」
    # 这里 None 会被传给 LLMClient(model=None)，LLMClient 内部再落到
    # .env 里的 DEEPSEEK_MODEL（deepseek-flash）。
    # 好处：这个脚本不写死模型，换模型只改 .env 不改代码。
    p.add_argument("--model", default=None)

    # 写法 A''：带值 + 固定默认
    # max_tokens=400：Unit 3 实测推理模型会用掉 60~100 推理 token，
    # 400 给分类任务留了余量，又不至于截断。
    p.add_argument("--max-tokens", type=int, default=400)

    # type=float：`--temperature 0.8` → 0.8（浮点）。default=0.3 是实验基线。
    p.add_argument("--temperature", type=float, default=0.3)

    # 只有 temperature 实验用到（每个温度重复跑几遍看漂移）
    p.add_argument("--repeat", type=int, default=3, help="temperature 实验的重复次数")

    # 写法 B：布尔开关（不带值）
    # `action="store_true"`：命令行里出现了 --dry-run，值就是 True；没出现就是 False。
    # 敲 `python experiments.py --dry-run` → ARGS.dry_run == True（注意中划线变下划线）
    # 敲 `python experiments.py`            → ARGS.dry_run == False
    # 不能写 `--dry-run true`，这个开关不接受值。
    p.add_argument("--dry-run", action="store_true", help="只拼 Prompt 不调用 API")

    # parse_args() 才是真正去读 sys.argv（命令行参数列表）的那一步。
    # 传了非法值（choices 之外、类型转不过来）时，在这里报错并退出（exit code 2）。
    return p.parse_args()


# 模块级调用：import 本文件的时候就会执行（不只是 python 直接运行时）。
# 后面所有函数都直接引用全局 ARGS（如 ARGS.max_tokens、ARGS.dry_run），
# 不用一层层传参——小脚本这么写省事；正式工程会放进 main() 里，方便测试。
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
    """跑一个变体 × 一条用例，返回结构化结果（方便统计）。

    这是所有实验共用的「最小执行单元」：variants / fewshot / system
    三个实验都靠它发请求、收结果。返回的 dict 结构是整个实验台的
    「通用行格式」，summarize 分组和 main 落盘都认这个格式。
    """
    # ① render：把变体模板里的 {input} 占位符替换成这条用例的真实文本，
    #    得到 (system, user) 两个字符串 —— 就是最终发给模型的两条消息。
    system, user = prompts.render(variant, case["input"])
    try:
        # ② 真正调 LLM。chat_one 是 llm.py 里的单轮便捷方法（system + user）。
        #    system or None：v0/v6 这类变体的 system 是空串 ""，
        #    空串 or None → None（Python 的 or 短路：空串是假值），
        #    这样就不会给 API 发一条空的 system 消息。
        #    warn_truncated=False：实验场景不需要截断告警刷屏。
        text = client.chat_one(
            user, system or None, warn_truncated=False, max_tokens=ARGS.max_tokens
        )
    except LLMCallError as exc:
        # ③ 失败也返回「同样形状」的行（ok=False、meta=None），不抛异常：
        #    这样统计和表格代码不需要区分成功/失败两条路径。
        #    思路是"错误也是数据"——失败的调用照样进结果表，看得见、算得进。
        return {"output": f"<失败: {exc}>", "ok": False, "compliant": False, "correct": False, "meta": None}

    # ④ 成功路径：client.meta 是 llm.py 每次调用后更新的元数据
    #    （token 用量/耗时/finish_reason/trace_id），取引用即可
    meta = client.meta
    return {
        "output": text,
        # ok=True 显式写出来：失败分支返回 ok=False，两侧行形状必须完全一致，
        # summarize 里 i["ok"] 才不会 KeyError（刚才注释重写时漏了这行，跑真实
        # 实验立刻炸出来——这也正是 selfcheck 没覆盖到的盲区：它 mock 了 ask，
        # 没测真实返回行的键完整性。留个教训：改"共用函数"必须跑一遍真实链路）
        "ok": True,
        # 合规 = 输出格式严格合法（恰好是三个类别词之一）—— 格式层面
        "compliant": prompts.is_compliant(text),
        # 正确 = 归一化后的类别与用例期望一致 —— 内容层面
        # 注意两者分开算：Unit 4 最大的发现就是"答对了"≠"能用"
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
    """按变体聚合：合规率、准确率、平均输出 token。

    输入是 ask() 产出的「行」列表，输出是每个变体一行的统计。
    这就是 Unit 4 的核心方法论：把"哪个 Prompt 更好"从主观印象
    变成 合规率 / 准确率 / 成本 三列数字。
    """
    # ---- 第一步：分组 ----
    # setdefault(key, [])：字典里没有这个键就先放个空列表，再 append。
    # 等价于 Node 里 reduce 累积成 Map<variant, rows[]>。
    # group_key 做成了参数：默认按 "variant" 分组，需要时也能按别的字段分组。
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r[group_key], []).append(r)

    stats = []
    for name, items in groups.items():
        # 只统计成功调用的行 —— 失败行的 meta 是 None，直接取 token 会炸
        ok_items = [i for i in items if i["ok"]]
        stats.append(
            {
                "variant": name,
                "n": len(items),  # 总次数（含失败）
                # sum(布尔列表)：True 当 1 相加 = 成功次数，除以总数即比率。
                # 注意分母是 len(items) 而不是 len(ok_items)：
                # 失败按"不合规/不正确"计入，让失败真实拉低指标——
                # 如果用 ok_items 做分母，失败会被悄悄"洗掉"。
                "compliance": sum(i["compliant"] for i in items) / len(items),
                "accuracy": sum(i["correct"] for i in items) / len(items),
                # statistics.mean = 算术平均；空列表会抛 StatisticsError，
                # 所以用条件表达式兜底成 0
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
# 实验 3：指令放 System vs 放 User（必做，直击 Unit 4 完成标准）
# ---------------------------------------------------------------------------
def exp_system(client: LLMClient) -> list[dict]:
    """实验 3：同一份五段式指令，分别放 System 和放 User，对比效果。

    实验设计的灵魂在 prompts.py 里：
        v5_full                → 五段式文本放 system，user 只放问题
        v6_instruction_in_user → 完全相同的五段式文本放 user，system 留空
    两者文字内容一样、只有位置不同 —— 这才是"控制变量"：
    如果结果有差异，唯一可能的原因就是"放的位置"。
    """
    # 用例切片：CASES[:N]。ARGS.cases 来自命令行 --cases，默认全部（5 条）。
    # 本实验只关心"位置差异"，不需要太多用例，跑 2 条也够看趋势（省 token）。
    cases = prompts.CASES[: ARGS.cases]
    # VARIANTS_BY_KEY 是 {key: 变体} 的字典（prompts.py 底部建好），
    # 按 key 直接取，比遍历 VARIANTS 列表找更直观。
    pair = [prompts.VARIANTS_BY_KEY["v5_full"], prompts.VARIANTS_BY_KEY["v6_instruction_in_user"]]

    console.print(
        f"\n[bold cyan]实验 3 · 指令放 System vs 放 User[/bold cyan]：同一份五段式文本，只换位置\n"
    )
    # dry-run 分支：不花一分钱先看看两组 Prompt 长什么样（每个实验函数都有这段）
    if ARGS.dry_run:
        for v in pair:
            console.print(f"[bold]{v.name}[/bold]\n  system = {short(v.system, 80)}\n  user   = {short(v.user, 80)}\n")
        return []  # 空列表 → main 里 all_rows 为空，自然跳过落盘

    rows: list[dict] = []
    # 双层循环：2 个变体 × N 条用例，每个组合都是一次真实 API 调用
    for v in pair:
        for case in cases:
            # ask() 干全部脏活：render → 调 LLM → 判合规/正确 → 打包成一行 dict
            r = ask(client, v, case)
            # ask 本身不知道"变体"维度的信息，这里补上：
            # variant/variant_key 供 summarize 分组，label 供表格显示
            r["variant"], r["variant_key"] = v.name, v.key
            # label 两行：第一行变体 key，第二行截断的用例文本（适配窄表格列）
            r["label"] = f"{v.key}\n{short(case['input'], 14)}"
            rows.append(r)

    # 先打印逐条明细（每一行长什么样），再打印聚合汇总（每变体一行统计）
    print_results_table(rows, "实验 3 · 逐条结果")
    print_summary(summarize(rows), "实验 3 · 汇总（重点看平均输入 token 的差距）")

    # ---- 单独算一次差值，给出"数字结论"，不让读者自己肉眼比 ----
    s_stats = summarize(rows)
    # len==2 确保两个变体都出了数据；avg_out 非 0 排除"全失败"的退化情况
    if len(s_stats) == 2 and s_stats[1]["avg_out"]:
        # {diff:+.0f} 里的 + 号：正数也带符号（如 +35），符合"差值"的语义
        diff = s_stats[1]["avg_out"] - s_stats[0]["avg_out"]
        console.print(
            f"\n[dim]两者输出 token 差 {diff:+.0f}；再看 run 输出的 usage 里 prompt_tokens 的差距。[/dim]"
        )
    # 打印"预登记"的结论方向 —— 好的实验习惯：先写预期，再看数据是否支持它
    console.print(
        "[dim]结论方向：短期单轮任务里两者效果接近；差异会在**多轮对话**和**指令优先级**上放大 —— "
        "System 是长期规则（且可能被缓存/不计入重发），User 是本次输入。Unit 10 会用多轮场景验证。[/dim]"
    )
    return rows


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
    """主流程：建客户端 → 跑实验 → 记录结果。

    整体就三步，读的时候脑子里挂着这条主线：
        ① 建一次 LLMClient（四个实验共用，省得重复初始化）
        ② 按命令行选实验，逐个跑
        ③ 跑完把所有结果存成 JSON 到 logs/
    """
    # ---- ① 建客户端 -------------------------------------------------------
    # 把命令行参数"注入"到客户端：模型/温度/max_tokens 一次配好，
    # 后面所有实验都用同一个 client（同一个 .usage 计数器）。
    # 注意 ARGS.model 可能是 None —— LLMClient 内部会落到 .env 的默认模型
    # （这就是 parse_args 注释里说的"默认值级联"）。
    try:
        client = LLMClient(model=ARGS.model, temperature=ARGS.temperature, max_tokens=ARGS.max_tokens)
    except LLMConfigError as exc:
        # 配置错误（没 Key / 占位 Key）不该拿 traceback 吓人：
        # 打一句人话，然后以非零码退出（1 = 出错，方便外层脚本判断成败）。
        console.print(f"[red]❌ 配置错误：{exc}[/red]")
        raise SystemExit(1)

    # 开跑前先回显本次关键参数（习惯问题）：
    # 实验结论只有在"参数明确"的前提下才有意义，回头翻 logs 也要能对上。
    console.print(f"[dim]模型 {client.model} ｜ temperature {ARGS.temperature} ｜ max_tokens {ARGS.max_tokens}[/dim]")

    # ---- ② 选实验并执行 ---------------------------------------------------
    # 字典当"分发器"（dispatch table）：键 = 命令行里的实验名，值 = 函数本身。
    # Python 里函数是一等公民，能像值一样存进 dict —— 这是替代
    #   if exp == "variants": ... elif exp == "fewshot": ...
    # 一长串 if/elif 的写法。Node 里对应"策略表"/handlers 对象。
    runner = {
        "variants": exp_variants,
        "fewshot": exp_fewshot,
        "system": exp_system,
        "temperature": exp_temperature,
    }
    # list(dict) 得到键列表（保持定义顺序：variants→fewshot→system→temperature）。
    # exp == "all" → 四个实验全跑；否则只跑选中的那一个。
    # 注意 [ARGS.exp] 把单个字符串包成列表：下面的 for 循环就只需要写一份，
    # 不用区分"跑一个"和"跑多个"两种情况。
    exps = list(runner) if ARGS.exp == "all" else [ARGS.exp]

    all_rows: list[dict] = []
    for name in exps:
        # 清零 usage 计数器 → 每个实验结束时打印的是"本实验"的用量，
        # 而不是从程序启动以来的累计值。
        # （llm.Usage 没有提供 reset() 方法，所以手动把四个字段归零；
        #   想改进的话可以给 Usage 加个 reset() —— 这就是重构的入口。）
        client.usage.calls = 0
        client.usage.prompt_tokens = 0
        client.usage.completion_tokens = 0
        client.usage.reasoning_tokens = 0
        # runner[name] 取出函数，(client) 立刻调用：
        # 每个实验函数拿同一个 client，返回"变体×用例"的所有结果行
        rows = runner[name](client)
        # 汇总进总表，循环结束后统一写盘
        all_rows += rows
        if not ARGS.dry_run:
            console.print(f"\n[bold green]{name} 本次用量：[/bold green]{client.usage.summary()}\n")

    # ---- ③ 结果落盘 -------------------------------------------------------
    # 两个条件：真跑过（非 dry-run）且有数据（all_rows 非空；
    # dry-run 时实验函数返回的是空列表，这里自然跳过）。
    if not ARGS.dry_run and all_rows:
        path = save_log(
            # 文件名形如 experiment-variants-20261014-230045.json（save_log 自动加时间戳）
            "experiment-" + ARGS.exp,
            {
                # timespec="seconds"：精确到秒就够，微秒是噪音
                "time": datetime.now().isoformat(timespec="seconds"),
                "model": client.model,
                "temperature": ARGS.temperature,
                "max_tokens": ARGS.max_tokens,
                # 列表推导式（list comprehension）：Python 最常用的"map+瘦身"写法。
                # 四个实验函数返回的行结构统一（variant_key/label/output/
                # compliant/correct/meta），所以这里能一段代码处理所有实验。
                # Node 对应：rows.map(r => ({...}))
                "rows": [
                    {
                        "variant": r["variant_key"],
                        # label 形如 "v0_baseline\n上传头像总是失败…"，
                        # split("\n")[-1] 取最后一段 = 纯输入文本（去掉变体名前缀）
                        "input": r["label"].split("\n")[-1],
                        "output": r["output"],
                        "compliant": r["compliant"],
                        "correct": r["correct"],
                        # meta 里是 token/耗时等调用元数据；
                        # 调用失败的行没有 meta（None），所以逐字段兜底成 null，
                        # 否则 r["meta"].xxx 会在 None 上炸 AttributeError。
                        "completion_tokens": r["meta"].completion_tokens if r["meta"] else None,
                        "reasoning_tokens": r["meta"].reasoning_tokens if r["meta"] else None,
                        "elapsed_ms": r["meta"].elapsed_ms if r["meta"] else None,
                    }
                    for r in all_rows
                ],
            },
        )
        console.print(f"[dim]结果已存 {path}（Unit 4 笔记可以直接引用这些数字）[/dim]")


# ── Python 的经典入口惯用法 ────────────────────────────────────────────────
# `python experiments.py xxx` 直接运行时，这个文件的 __name__ 是 "__main__"，
# 条件成立 → 执行 main()。
# 被别处 `import experiments`（比如 selfcheck 复用函数）时，__name__ 是
# "experiments"，条件不成立 → 不会执行 main()。
# 一句话：保证"直接运行才跑主流程，被当库导入时不跑"。
# Node 里最接近的对应物：if (require.main === module) { ... }
if __name__ == "__main__":
    main()
