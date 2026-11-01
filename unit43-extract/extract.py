"""Unit 43 · 结构化要点提取 —— 用规则模拟 LLM，输出可溯源的要点。

教学核心：**提取和生成的区别**。

- **生成**：模型新写一段话，内容可能不在原文里（可能编造）。
- **提取**：从原文里**找**句子，原样或微调作为要点，必须能溯源到原文。

为什么要可溯源：RAG 里 hallucination 的根源就是「生成」伪装成「提取」——
模型说「根据文档，X 是 Y」，但文档里没说。要钉死「提取」,
就让每个要点都带 `source_sentence` 字段，且这个字段必须**逐字**出现在原文里。

`extract_key_points(text, schema)`：
- text：清洗后的正文（unit 42 输出）
- schema：要点形状，本单元固定 `{point: str, source_sentence: str}`
- 返回：3-5 个要点，每个要点必须有 source_sentence 且 source_sentence 在原文里

`OfflineModel` 用规则识别「定义句」「数值句」「因果句」三类作为要点候选：
- 定义句：含「是」「叫」「称为」「定义为」
- 数值句：含数字 + 单位 / 百分比
- 因果句：含「因为」「所以」「导致」「从而」

这三类覆盖了文档里 80% 的「值得记」的句子，剩下 20% 本单元不处理（教学范围）。
"""

from __future__ import annotations

import re
from typing import Any

DEFAULT_MIN_POINTS = 3
DEFAULT_MAX_POINTS = 5
SENTENCE_MIN_LEN = 12  # 太短的句子不算要点候选（如「如下。」）


class OfflineModel:
    """规则驱动的离线模型，输出标准 OpenAI message 格式。

    不调任何 API、不要 key。规则识别定义句 / 数值句 / 因果句，
    把它们包装成 `[{point, source_sentence}]` 放进 content。

    为什么伪装成 OpenAI 格式：让 unit 45 的 Research Agent 调它的接口和调真模型一致，
    切换真模型时只换 `complete()` 实现，不改 Agent 编排。
    """

    name = "offline-rules"

    def __init__(self) -> None:
        self.calls = 0
        self.last_input: str = ""

    def complete(self, messages: list[dict]) -> dict:
        """对接 OpenAI 风格 messages 数组，返回 assistant message。"""
        self.calls += 1
        # 找最后一条 user 消息作为输入
        user_text = ""
        for m in messages:
            if m.get("role") == "user":
                user_text = m.get("content") or ""
        self.last_input = user_text
        # 模型上下文里可能要求结构化输出 —— 这里直接调 extract_key_points
        points = extract_key_points(user_text, SCHEMA)
        return {
            "role": "assistant",
            "content": _format_points_as_text(points),
            # 也带上结构化字段，方便下游直接用
            "points": points,
        }


SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "point": {"type": "string", "description": "要点：精炼后的陈述"},
                    "source_sentence": {
                        "type": "string",
                        "description": "原文里能逐字找到的句子，必须 verbatim 出现",
                    },
                },
                "required": ["point", "source_sentence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["points"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# 句子切分
# ---------------------------------------------------------------------------
_SENT_SPLIT = re.compile(r"[。！？!?；;\n]")


def split_sentences(text: str) -> list[str]:
    """按句末标点和换行切句子，去首尾空白，过滤过短的。"""
    if not text:
        return []
    parts = _SENT_SPLIT.split(text)
    out: list[str] = []
    for p in parts:
        s = p.strip()
        if len(s) >= SENTENCE_MIN_LEN:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# 三类「值得记」的句子
# ---------------------------------------------------------------------------
DEFINITION_PATTERNS = [
    re.compile(r"(?:是|叫作|叫做|称为|称作|定义为|是指|意思是)"),
]

NUMBER_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(?:%|‰|个|条|次|ms|秒|分钟|小时|天|周|月|年|倍|分|元|kg|km|MB|GB|TB)"
)

CAUSE_PATTERNS = [
    re.compile(r"因为"),
    re.compile(r"所以"),
    re.compile(r"导致"),
    re.compile(r"从而"),
    re.compile(r"因此"),
    re.compile(r"由于"),
]


def _classify(sentence: str) -> str | None:
    """把句子归类成 definition / number / cause / None。"""
    if any(p.search(sentence) for p in DEFINITION_PATTERNS):
        return "definition"
    if NUMBER_PATTERN.search(sentence):
        return "number"
    if any(p.search(sentence) for p in CAUSE_PATTERNS):
        return "cause"
    return None


def _point_from_sentence(sentence: str, kind: str) -> str:
    """从源句生成要点（精炼版）。

    本单元的「精炼」是简单规则：
    - definition：去掉「是」「是指」之后的内容做主谓宾
    - number：保留数值和它的语境
    - cause：保留「因为/所以」分句
    """
    if kind == "definition":
        # 简化：把句子里的「，」换成「；」让结构更紧
        return sentence.replace("，", "；")[:80]
    if kind == "number":
        return sentence[:80]
    if kind == "cause":
        return sentence[:80]
    return sentence[:80]


def extract_key_points(
    text: str,
    schema: dict[str, Any] | None = None,
    min_points: int = DEFAULT_MIN_POINTS,
    max_points: int = DEFAULT_MAX_POINTS,
) -> list[dict[str, str]]:
    """从正文里提取 3-5 个要点。

    每个要点是 `{point, source_sentence}`：
    - `source_sentence`：**逐字**出现在原文里（断言 `source_sentence in text` 必须成立）
    - `point`：精炼后的要点（可以截断或微调，但内容来自源句）

    空正文返回 `[]`（不报错）。命中不到 3 条时返回实际命中的数量（不补全）。
    """
    if not text or not text.strip():
        return []
    # schema 参数本单元不强校验（教学版），但保留参数让 unit 45 调用时和真模型一致
    sentences = split_sentences(text)
    if not sentences:
        return []

    candidates: list[tuple[str, str, str]] = []  # (kind, sentence, source_sentence)
    for s in sentences:
        kind = _classify(s)
        if kind:
            candidates.append((kind, s, s))

    # 去重：同一句子只算一次（即使命中多种 pattern）
    seen: set[str] = set()
    deduped: list[tuple[str, str, str]] = []
    for kind, s, src in candidates:
        if src in seen:
            continue
        seen.add(src)
        deduped.append((kind, s, src))

    # 按优先级排序：definition > number > cause（业务直觉：定义最有价值）
    priority = {"definition": 0, "number": 1, "cause": 2}
    deduped.sort(key=lambda x: priority[x[0]])

    # 截到 max_points
    selected = deduped[:max_points]

    return [{"point": _point_from_sentence(s, kind), "source_sentence": src}
            for kind, s, src in selected]


def _format_points_as_text(points: list[dict[str, str]]) -> str:
    """把结构化要点格式化成文本（给 OpenAI content 字段用）。"""
    if not points:
        return "（未提取到要点）"
    lines = []
    for i, p in enumerate(points, 1):
        lines.append(f"{i}. {p['point']}")
        lines.append(f"   源句：{p['source_sentence']}")
    return "\n".join(lines)


def verify_sourcing(text: str, points: list[dict[str, str]]) -> list[dict[str, Any]]:
    """校验每个要点的 source_sentence 是否在原文里逐字出现。

    返回每个要点的校验结果：`[{point, source_sentence, found, source_pos}]`
    `source_pos` 是首字符位置（找不到为 -1）。
    """
    out: list[dict[str, Any]] = []
    for p in points:
        src = p.get("source_sentence", "")
        pos = text.find(src) if src else -1
        out.append({
            "point": p.get("point", ""),
            "source_sentence": src,
            "found": pos >= 0,
            "source_pos": pos,
        })
    return out
