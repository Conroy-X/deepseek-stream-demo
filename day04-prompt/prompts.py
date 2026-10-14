"""Day 4 · Prompt 模板库：把「角色 + 任务 + 约束 + 输出格式 + 示例」五段参数化。

为什么要参数化，而不是把 Prompt 直接写死？
    因为 Day 4 的核心动作是 **A/B 对比**：同一个任务，换个 Prompt 变体，看效果差多少。
    写死就没法对比，也没法复用。

本文件提供：
    1. FIVE_PART_TEMPLATE —— 五段式模板（Day 4 的产出标准之一）
    2. build_five_part()   —— 按五段拼一个 Prompt，system / user 自动分工
    3. VARIANTS            —— 7 个变体，从一个"裸问"逐步加到"五段式全上"
    4. CASES               —— 测试用例（同一个任务的多条输入）

配套使用：python experiments.py variants
自检：python prompts.py
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 一、五段式模板
# ---------------------------------------------------------------------------
# 记忆口诀：**角、任、约、格、例**（角色 / 任务 / 约束 / 输出格式 / 示例）
#
# 一个关键认知：五段里只有「任务」是每次都变的，其余四段都是稳定规则，
# 所以它们应该放 **System Prompt**（长期人设 + 规则），任务数据放 User Prompt（本次输入）。
# 这就是 System / User 的本质区别：**不是位置先后，而是「稳定规则」与「本次输入」的职责分离**。
FIVE_PART_TEMPLATE = """角色：{role}
任务：{task}
约束：
{constraints}
输出格式：{output_format}
示例：
{examples}"""

# ---------------------------------------------------------------------------
# 二、参数化构造函数
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptVariant:
    """一个 Prompt 变体。system 是长期规则，user 是本次输入。"""

    key: str
    name: str
    system: str
    user: str
    hypothesis: str  # 这个变体想验证什么 —— 实验前先写下假设，才叫实验
    target: str = "system"  # 指令放在哪：system / user（用于对比实验）


def five_part_text(
    role: str,
    task: str,
    constraints: list[str] | None = None,
    output_format: str = "直接给出答案，不要解释。",
    examples: list[tuple[str, str]] | None = None,
) -> str:
    """把五段渲染成一段可直接塞进 Prompt 的文本（**先看这个函数，它就是"五段式"本身**）。"""
    constraints_text = "\n".join(f"- {c}" for c in (constraints or [])) or "- 无特殊要求"
    examples_text = (
        "\n".join(f"输入：{x}\n输出：{y}" for x, y in examples) if examples else "（无示例）"
    )
    return FIVE_PART_TEMPLATE.format(
        role=role,
        task=task,
        constraints=constraints_text,
        output_format=output_format,
        examples=examples_text,
    )


def build_five_part(
    role: str,
    task: str,
    constraints: list[str] | None = None,
    output_format: str = "直接给出答案，不要解释。",
    examples: list[tuple[str, str]] | None = None,
    *,
    instruction_target: str = "system",
) -> tuple[str, str]:
    """按五段式拼 Prompt，返回 (system_prompt, user_prompt)。

    instruction_target="user" 时，把其余四段也塞进 user —— 专门用于验证
    「同样的指令放 system 和放 user 有没有差别」。
    """
    instructions = five_part_text(role, task, constraints, output_format, examples)

    if instruction_target == "system":
        return instructions, "{input}"
    return "", instructions + "\n\n输入：{input}"


def render(variant: PromptVariant, input_text: str) -> tuple[str, str]:
    """把变体里的 {input} 占位符替换成真实输入。"""
    return (
        variant.system.replace("{input}", input_text),
        variant.user.replace("{input}", input_text),
    )


# ---------------------------------------------------------------------------
# 三、任务与测试用例
# ---------------------------------------------------------------------------
# 任务选了一个"有标准答案、输出很短"的分类任务：
#   - 有标准答案 → 能自动算合规率，不用靠肉眼看
#   - 输出很短   → 省钱（记住：推理模型光推理就要 60~100 token）
TASK = "把用户反馈分类为「功能缺陷」「使用问题」「新需求」三类之一"

CASES: list[dict[str, str]] = [
    {"input": "上传头像总是失败，换了几张图都不行", "expect": "功能缺陷"},
    {"input": "这个导出按钮在哪儿啊？找了半天没找到", "expect": "使用问题"},
    {"input": "希望能支持批量删除聊天记录", "expect": "新需求"},
    {"input": "刚才还好好的，现在一打开就白屏", "expect": "功能缺陷"},
    {"input": "能不能加个深色模式？晚上看着刺眼", "expect": "新需求"},
]

# few-shot 示例（用于 few-shot 实验，注意示例要覆盖全部三个类别）
FEW_SHOT_EXAMPLES: list[tuple[str, str]] = [
    ("点了保存，提示网络错误，重试三次都不行", "功能缺陷"),
    ("我想改绑手机号，请问在哪里操作？", "使用问题"),
    ("希望可以按标签筛选订单", "新需求"),
]

# ---------------------------------------------------------------------------
# 四、7 个变体：从一个"裸问"逐步加到"五段式全上"
# ---------------------------------------------------------------------------
_ROLE = "你是资深的用户反馈质检员，负责给产品团队的反馈池做归类。"
_CONSTRAINTS = [
    "只能从「功能缺陷」「使用问题」「新需求」三个类别里选一个",
    "拿不准时选最接近的那个，不要新增类别",
    "不要解释、不要复述反馈、不要加标点以外的任何字符",
]
_OUTPUT_FORMAT = "只输出类别名称本身，例如：功能缺陷"

# 五段式的完整文本（v5 放 system、v6 放 user，用同一份内容做对照实验）
_FIVE_PART_TEXT = five_part_text(
    role=_ROLE,
    task=TASK,
    constraints=_CONSTRAINTS,
    output_format=_OUTPUT_FORMAT,
    examples=FEW_SHOT_EXAMPLES,
)
_ASK = "这条用户反馈属于哪一类？"

VARIANTS: list[PromptVariant] = [
    PromptVariant(
        key="v0_baseline",
        name="① 裸问（基线）",
        system="",
        user="这条用户反馈属于哪一类？\n{input}",
        hypothesis="没有任何指令约束时，模型会怎么答？大概会输出一句话解释，无法直接使用。",
    ),
    PromptVariant(
        key="v1_role",
        name="② +角色",
        system=_ROLE,
        user="这条用户反馈属于哪一类？\n{input}",
        hypothesis="只给角色，输出格式仍然不受控（但语气会变专业）。",
    ),
    PromptVariant(
        key="v2_constraint",
        name="③ +约束",
        system=f"{_ROLE}\n\n要求：\n" + "\n".join(f"- {c}" for c in _CONSTRAINTS),
        user="这条用户反馈属于哪一类？\n{input}",
        hypothesis="约束能明显瘦身输出，但类别名仍可能出现变体写法（如「功能bug」）。",
    ),
    PromptVariant(
        key="v3_format",
        name="④ +输出格式",
        system=f"{_ROLE}\n\n要求：\n" + "\n".join(f"- {c}" for c in _CONSTRAINTS) + f"\n\n输出格式：{_OUTPUT_FORMAT}",
        user="这条用户反馈属于哪一类？\n{input}",
        hypothesis="输出格式是合规率的关键——给了格式示例后应当基本 100% 合规。",
    ),
    PromptVariant(
        key="v4_few_shot",
        name="⑤ +3 个示例（few-shot）",
        system=f"{_ROLE}\n\n要求：\n"
        + "\n".join(f"- {c}" for c in _CONSTRAINTS)
        + f"\n\n输出格式：{_OUTPUT_FORMAT}\n\n示例：\n"
        + "\n".join(f"输入：{x}\n输出：{y}" for x, y in FEW_SHOT_EXAMPLES),
        user="这条用户反馈属于哪一类？\n{input}",
        hypothesis="few-shot 主要不是提准确率，而是**锁定格式和边界**（比如教会它「拿不准时的倾向」）。",
    ),
    PromptVariant(
        key="v5_full",
        name="⑥ 五段式全上（指令在 System）",
        system=_FIVE_PART_TEXT,
        user=f"{_ASK}\n{{input}}",
        hypothesis="五段齐全的版本应当是准确率与合规率都最高的基线，作为后续对比的参照。",
    ),
    PromptVariant(
        key="v6_instruction_in_user",
        name="⑦ 同样指令，但放 User",
        system="",
        user=f"{_ASK}\n{{input}}\n\n{_FIVE_PART_TEXT}",
        hypothesis="对比 v5：指令放 system 还是 user，效果应当接近，但 system 更稳定、更省 token"
        "（多轮对话里 system 只发一次，Day 10 会验证这点）。",
        target="user",
    ),
]

VALID = ("功能缺陷", "使用问题", "新需求")

VARIANTS_BY_KEY = {v.key: v for v in VARIANTS}


# ---------------------------------------------------------------------------
# 五、合规校验（把"好不好"变成可计算的数字）
# ---------------------------------------------------------------------------
def is_compliant(text: str) -> bool:
    """回答是否严格等于三个合法类别之一（去空白后精确匹配）。"""
    return text.strip().strip("。.!！") in VALID


def normalize(text: str) -> str:
    """宽松归一化：把模型的写法收敛到标准类别，用于算准确率。"""
    t = text.strip().strip("。.!！\"'")
    for v in VALID:
        if v in t:
            return v
    aliases = {
        "缺陷": "功能缺陷",
        "bug": "功能缺陷",
        "故障": "功能缺陷",
        "使用": "使用问题",
        "咨询": "使用问题",
        "需求": "新需求",
        "建议": "新需求",
    }
    low = t.lower()
    for k, v in aliases.items():
        if k in low:
            return v
    return ""


if __name__ == "__main__":
    # 自检：不联网、不花钱，确认模板拼装正确
    print("=== 五段式模板 ===")
    system, user = build_five_part(
        role=_ROLE, task=TASK, constraints=_CONSTRAINTS, output_format=_OUTPUT_FORMAT, examples=FEW_SHOT_EXAMPLES
    )
    print(system)
    print(f"\n--- user ---\n{user}")

    print(f"\n=== 变体清单（共 {len(VARIANTS)} 个）===")
    for v in VARIANTS:
        assert v.key and v.name and v.hypothesis, f"变体字段缺失: {v.key}"
        print(f"  {v.key:<22} {v.name:<26} 指令位置={v.target}")

    assert len({v.key for v in VARIANTS}) == len(VARIANTS), "变体 key 有重复"

    print("\n=== 合规校验 ===")
    for sample, expected in [("功能缺陷", True), ("功能缺陷。", True), ("这条属于功能缺陷", False), ("新需求", True)]:
        got = is_compliant(sample)
        print(f"  is_compliant({sample!r}) = {got}")
        assert got is expected

    print("\n=== 归一化 ===")
    for sample, expected in [("功能缺陷", "功能缺陷"), ("bug", "功能缺陷"), ("我觉得是新需求", "新需求"), ("???", "")]:
        got = normalize(sample)
        print(f"  normalize({sample!r}) = {got!r}")
        assert got == expected

    print("\n✅ prompts.py 自检通过")
