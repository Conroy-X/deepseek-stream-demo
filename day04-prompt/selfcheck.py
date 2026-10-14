"""Day 4 · 离线自检：不联网、不花钱，先把代码逻辑验证一遍。

用法：python selfcheck.py

和 Day 3 的 selfcheck 一个思路：Prompt 工程的成本主要在"跑调用"上，
所以先把**不需要调用 API 就能验证的部分**（模板拼装、变体完整性、合规判定、成本计算）
全部验证掉，再花钱跑实验。看到「全部通过 ✅」就可以放心开跑。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


# ---------------------------------------------------------------------------
print("=" * 62)
print("一、prompts.py —— 模板与变体")
print("=" * 62)
sys.path.insert(0, str(ROOT))
import prompts  # noqa: E402

text = prompts.five_part_text(
    role="角色X",
    task="任务Y",
    constraints=["约束A", "约束B"],
    output_format="格式Z",
    examples=[("输入1", "输出1")],
)
for seg in ["角色：", "任务：", "约束：", "输出格式：", "示例："]:
    check(f"五段式包含「{seg}」", seg in text, f"实际={text[:40]!r}")
check("约束逐条渲染成 bullet", "- 约束A" in text and "- 约束B" in text)
check("示例渲染成 输入/输出 对", "输入：输入1" in text and "输出：输出1" in text)
check("无示例时给占位而不是空", "（无示例）" in prompts.five_part_text("r", "t"))

sys_p, user_p = prompts.build_five_part("角色X", "任务Y", ["约束A"], "格式Z", [("a", "b")])
check("默认把五段放进 system", "角色：角色X" in sys_p and user_p == "{input}")
sys_u, user_u = prompts.build_five_part("角色X", "任务Y", ["约束A"], "格式Z", [("a", "b")], instruction_target="user")
check("instruction_target=user 时 system 为空", sys_u == "")
check("instruction_target=user 时 user 含全部五段", "角色：角色X" in user_u and "{input}" in user_u)

check("变体数量为 7", len(prompts.VARIANTS) == 7, f"实际 {len(prompts.VARIANTS)}")
check("变体 key 唯一", len({v.key for v in prompts.VARIANTS}) == len(prompts.VARIANTS))
check("每个变体都写了假设", all(v.hypothesis for v in prompts.VARIANTS))
check("key->变体 映射完整", set(prompts.VARIANTS_BY_KEY) == {v.key for v in prompts.VARIANTS})
check("v0 无 system（基线）", prompts.VARIANTS_BY_KEY["v0_baseline"].system == "")
check("v4 含 3 个示例", prompts.VARIANTS_BY_KEY["v4_few_shot"].system.count("输入：") == 3)
check("v5 与 v6 内容同源", prompts.VARIANTS_BY_KEY["v6_instruction_in_user"].system == "")

hit = [v.key for v in prompts.VARIANTS if "{input}" in v.user or "{input}" in v.system]
check("每个变体都留了 {input} 占位", len(hit) == len(prompts.VARIANTS), f"缺: {set(v.key for v in prompts.VARIANTS) - set(hit)}")

s, u = prompts.render(prompts.VARIANTS_BY_KEY["v5_full"], "测试输入")
check("render 正确替换 {input}", "测试输入" in u and "{input}" not in u)
check("render 不污染原变体", "{input}" in prompts.VARIANTS_BY_KEY["v5_full"].user)

# ---------------------------------------------------------------------------
print()
print("=" * 62)
print("二、prompts.py —— 合规判定与归一化")
print("=" * 62)
for sample, expect in [
    ("功能缺陷", True),
    ("功能缺陷。", True),
    ("新需求", True),
    ("我认为是功能缺陷", False),
    ("bug", False),
    ("", False),
]:
    check(f"is_compliant({sample!r}) == {expect}", prompts.is_compliant(sample) is expect)

for sample, expect in [
    ("功能缺陷", "功能缺陷"),
    ("功能缺陷。", "功能缺陷"),
    ("bug", "功能缺陷"),
    ("这是个使用问题", "使用问题"),
    ("建议加深色模式", "新需求"),
    ("完全无关的回答", ""),
    ("", ""),
]:
    got = prompts.normalize(sample)
    check(f"normalize({sample!r}) == {expect!r}", got == expect, f"实际={got!r}")

check("CASES 每条都有 expect", all(c.get("expect") in prompts.VALID for c in prompts.CASES))
check("CASES 覆盖全部三个类别", {c["expect"] for c in prompts.CASES} == set(prompts.VALID))
check("few-shot 示例覆盖三个类别", {y for _, y in prompts.FEW_SHOT_EXAMPLES} == set(prompts.VALID))

# ---------------------------------------------------------------------------
print()
print("=" * 62)
print("三、llm.py —— 用量统计与成本计算")
print("=" * 62)
import llm  # noqa: E402

u1 = llm.Usage()
u1.add(1000, 500, 400)
check("calls 累加", u1.calls == 1)
check("total_tokens = 输入+输出", u1.total_tokens == 1500)
check("cost = 1元/M输入 + 4元/M输出", abs(u1.cost_cny - (1000 / 1e6 * 1.0 + 500 / 1e6 * 4.0)) < 1e-12, f"实际={u1.cost_cny}")
check("推理占比 = 推理/输出", abs(u1.reasoning_ratio - 0.8) < 1e-9, f"实际={u1.reasoning_ratio}")
u1.add(0, 0, 0)
check("输出为 0 时推理占比不炸（除零保护）", llm.Usage().reasoning_ratio == 0.0)
check("summary 含关键信息", all(k in u1.summary() for k in ["次调用", "输入", "输出", "推理", "¥"]))

m = llm.Message("user", "你好")
check("Message.to_dict 结构正确", m.to_dict() == {"role": "user", "content": "你好"})

meta = llm.CallMeta(text="x", finish_reason="length", truncated=True)
check("CallMeta.truncated 可标记截断", meta.truncated is True)

saved = os.environ.pop("DEEPSEEK_API_KEY", None)
try:
    llm.LLMClient()
    check("缺 Key 时抛 LLMConfigError", False, "竟然没抛异常")
except llm.LLMConfigError:
    check("缺 Key 时抛 LLMConfigError", True)
os.environ["DEEPSEEK_API_KEY"] = "sk-你的密钥"
try:
    llm.LLMClient()
    check("占位 Key 也能被识别为未配置", False, "竟然没抛异常")
except llm.LLMConfigError:
    check("占位 Key 也能被识别为未配置", True)
if saved is not None:
    os.environ["DEEPSEEK_API_KEY"] = saved
else:
    os.environ.pop("DEEPSEEK_API_KEY", None)

check("默认 max_tokens 足够容纳推理 token（>=300）", llm.LLMClient.__init__.__defaults__[2] >= 300)

# ---------------------------------------------------------------------------
print()
print("=" * 62)
print("四、experiments.py —— 语法与 CLI")
print("=" * 62)
src = (ROOT / "experiments.py").read_text(encoding="utf-8")
try:
    compile(src, "experiments.py", "exec")
    check("experiments.py 语法正确（compile 通过）", True)
except SyntaxError as exc:
    check("experiments.py 语法正确（compile 通过）", False, str(exc))

for exp in ["variants", "fewshot", "system", "temperature"]:
    check(f"包含实验入口 {exp}", f'"{exp}"' in src)

# ---------------------------------------------------------------------------
print()
print("=" * 62)
total = PASS + FAIL
if FAIL == 0:
    print(f"结果：全部通过 ✅  （{PASS}/{total} 项断言）")
    print("下一步：python experiments.py variants --dry-run  先看 Prompt 长什么样（不花钱）")
else:
    print(f"结果：{FAIL} 项失败 ❌  （通过 {PASS}/{total}）")
print("=" * 62)
sys.exit(1 if FAIL else 0)
