"""Unit 44 · 多来源交叉比对与冲突检测。

教学核心：**冲突检测**。

同一个事实在不同来源里数值不同（如「5 天」 vs 「10 天」），要标出来。
本单元用规则识别数值差异 —— 把每个来源里的「数值 + 单位」抽出来，
同一主题下数值不同就是冲突。

3 个来源对同一主题（reranker 提升 RAG 召回）的描述：
- 来源 A：reranker 把 top-3 命中率从 0.62 拉到 0.81（数值 0.62 / 0.81）
- 来源 B：reranker 把 top-3 命中率从 0.55 提升到 0.79（数值 0.55 / 0.79 —— 与 A 冲突）
- 来源 C：reranker 的延迟从 120ms 涨到 260ms（补充点，不冲突，是另一维度）

预期：
- consensus：来源 A 和 B 都说 reranker 提升 top-3 命中率（共识陈述）
- conflicts：A 的 0.62/0.81 vs B 的 0.55/0.79（数值冲突）
- unique_points：C 提到延迟翻倍（A/B 都没说，是补充）

`cross_compare(sources)` 返回 `{consensus, conflicts, unique_points, sources_summary}`。
"""

from __future__ import annotations

import re
from typing import Any

# 主题关键词 → 用于把来源归类到同一主题下做对比
TOPIC_KEYWORDS = {
    "reranker": ["reranker", "重排器", "rerank"],
    "chunk": ["chunk", "分块", "切片"],
    "mcp": ["mcp", "model context protocol"],
    "langgraph": ["langgraph", "stategraph"],
}

# 数值提取：数字 + 常见单位
NUMBER_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(%|‰|ms|秒|分钟|小时|天|周|月|年|倍|分|元|kg|km|MB|GB|TB|个|条|次|档)?"
)

# 「提升 / 拉到 / 增加 / 涨到」这类动词的前缀
DELTA_VERBS = ["拉到", "提升", "提高到", "升到", "涨到", "增加到", "从"]


def _extract_numbers(text: str) -> list[dict[str, Any]]:
    """从文本里抽出所有数值 + 单位 + 位置。

    返回 `[{value: float, unit: str, raw: str, pos: int}]`。
    `raw` 是原始字符串（含数字 + 单位），用于冲突比对时显示。
    """
    out: list[dict[str, Any]] = []
    for m in NUMBER_RE.finditer(text):
        val_str = m.group(1)
        unit = m.group(2) or ""
        try:
            val = float(val_str)
        except ValueError:
            continue
        out.append({
            "value": val,
            "unit": unit,
            "raw": m.group(0),
            "pos": m.start(),
        })
    return out


def _find_topic(text: str) -> str:
    """根据文本内容归类到一个主题。返回主题字符串（小写）。
    找不到返回 "unknown"。
    """
    low = text.lower()
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(k in low for k in keywords):
            return topic
    return "unknown"


def _shared_topic(sources: list[dict[str, str]]) -> str:
    """看所有来源是否大致都在讲同一主题。返回那个共同主题或 "mixed"。
    """
    topics = [_find_topic(s.get("text", "")) for s in sources]
    unique = set(topics)
    if len(unique) == 1 and "unknown" not in unique:
        return unique.pop()
    # 多个主题但有非 unknown 的主导主题
    known = [t for t in topics if t != "unknown"]
    if known and len(set(known)) == 1:
        return known[0]
    return "mixed"


def _extract_delta_pairs(text: str) -> list[dict[str, Any]]:
    """抽出「从 X 拉到 Y」这种数值对，用于冲突比对。

    fixture 里 A/B 都说「从 0.X 拉到 0.Y」，这种 pair 是冲突检测的核心。
    """
    out: list[dict[str, Any]] = []
    # 简化：找所有「从 N... 到/L 拉到/提升到 N...」模式
    pair_re = re.compile(
        r"从\s*(\d+(?:\.\d+)?)\s*(%|ms|秒|倍|分)?[^。]*?(?:拉到|提升到|提高到|升到|涨到|到)\s*(\d+(?:\.\d+)?)\s*(%|ms|秒|倍|分)?"
    )
    for m in pair_re.finditer(text):
        try:
            v1 = float(m.group(1))
            v2 = float(m.group(3))
        except ValueError:
            continue
        u1 = m.group(2) or ""
        u2 = m.group(4) or ""
        out.append({
            "from": v1,
            "from_unit": u1,
            "to": v2,
            "to_unit": u2,
            "raw": m.group(0),
        })
    return out


def cross_compare(sources: list[dict[str, str]]) -> dict[str, Any]:
    """对多个来源做交叉比对。

    输入：`[{name, text}]`，每个来源一段文本。

    输出：
        {
          "topic": str,                  # 共同主题
          "sources_count": int,
          "consensus": [{claim, sources}],      # 共识陈述
          "conflicts": [{claim, values}],       # 冲突点（数值不同）
          "unique_points": [{claim, source}],   # 补充点（只 1 个来源提到）
          "sources_summary": [{name, topic, numbers, delta_pairs}],
        }
    """
    if not sources:
        return {
            "topic": "none",
            "sources_count": 0,
            "consensus": [],
            "conflicts": [],
            "unique_points": [],
            "sources_summary": [],
        }

    topic = _shared_topic(sources)

    summaries: list[dict[str, Any]] = []
    for s in sources:
        text = s.get("text", "") or ""
        summaries.append({
            "name": s.get("name", ""),
            "topic": _find_topic(text),
            "numbers": _extract_numbers(text),
            "delta_pairs": _extract_delta_pairs(text),
        })

    # 共识：所有来源都提到的关键词（用主题关键词里命中的那一个）
    consensus: list[dict[str, Any]] = []
    if topic != "mixed" and topic != "unknown":
        # 用主题关键词作为共识陈述
        kw = TOPIC_KEYWORDS.get(topic, [topic])
        hit_sources = [s["name"] for s in summaries if s["topic"] == topic]
        if len(hit_sources) >= 2:
            consensus.append({
                "claim": f"所有来源都在讨论 {topic} 相关的事实",
                "sources": hit_sources,
            })

    # 冲突：同一主题下 delta_pairs 的 from / to 数值不同
    # 按 (from_unit, to_unit) 分组，单位一致的 pair 才能比对
    conflicts: list[dict[str, Any]] = []
    pair_sources: list[dict[str, Any]] = []
    for s in summaries:
        for p in s["delta_pairs"]:
            pair_sources.append({"name": s["name"], "pair": p})

    # 按单位签名分组：只在同一类型（如都是无单位 = 命中率，都是 ms = 延迟）内比对
    unit_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for ps in pair_sources:
        sig = (ps["pair"]["from_unit"], ps["pair"]["to_unit"])
        unit_groups.setdefault(sig, []).append(ps)

    unit_label = {
        ("", ""): "命中率",
        ("ms", "ms"): "延迟(ms)",
        ("%", "%"): "百分比(%)",
        ("倍", "倍"): "倍数",
        ("秒", "秒"): "秒数",
    }

    for sig, group in unit_groups.items():
        if len(group) < 2:
            continue
        label = unit_label.get(sig, f"单位 {sig}")
        # 比 from 值
        from_values: dict[float, list[str]] = {}
        for ps in group:
            v = ps["pair"]["from"]
            from_values.setdefault(v, []).append(ps["name"])
        if len(from_values) > 1:
            conflicts.append({
                "claim": f"{label}起始值不同",
                "dimension": label,
                "values": [{"value": v, "sources": srcs} for v, srcs in sorted(from_values.items())],
            })
        # 比 to 值
        to_values: dict[float, list[str]] = {}
        for ps in group:
            v = ps["pair"]["to"]
            to_values.setdefault(v, []).append(ps["name"])
        if len(to_values) > 1:
            conflicts.append({
                "claim": f"{label}最终值不同",
                "dimension": label,
                "values": [{"value": v, "sources": srcs} for v, srcs in sorted(to_values.items())],
            })

    # 补充点：只 1 个来源提到的数值（用 numbers 而不是 delta_pairs）
    unique_points: list[dict[str, Any]] = []
    # 收集每个数值（value+unit）出现在哪些来源
    value_unit_sources: dict[tuple[float, str], list[str]] = {}
    for s in summaries:
        for n in s["numbers"]:
            key = (n["value"], n["unit"])
            value_unit_sources.setdefault(key, [])
            if s["name"] not in value_unit_sources[key]:
                value_unit_sources[key].append(s["name"])
    # 只 1 个来源独有的数值（且这个数值不构成 delta_pair 的 from/to，否则就进冲突了）
    pair_values: set[float] = set()
    for ps in pair_sources:
        pair_values.add(ps["pair"]["from"])
        pair_values.add(ps["pair"]["to"])
    for (val, unit), srcs in value_unit_sources.items():
        if len(srcs) == 1 and val not in pair_values:
            unique_points.append({
                "claim": f"来源 {srcs[0]} 独有数值：{val}{unit}",
                "source": srcs[0],
                "value": val,
                "unit": unit,
            })

    return {
        "topic": topic,
        "sources_count": len(sources),
        "consensus": consensus,
        "conflicts": conflicts,
        "unique_points": unique_points,
        "sources_summary": summaries,
    }


# 内置的 3 来源 fixture（页面和自检共用）
DEFAULT_SOURCES: list[dict[str, str]] = [
    {
        "name": "A · RAG 实测",
        "text": "在 200 条文档的语料上，reranker 把 top-3 命中率从 0.62 拉到 0.81，"
        "但延迟从 90ms 涨到 180ms。",
    },
    {
        "name": "B · 另一项实测",
        "text": "我们的实验里，reranker 把 top-3 命中率从 0.55 提升到 0.79，"
        "用了 bge-reranker-v2 模型。",
    },
    {
        "name": "C · 延迟补充",
        "text": "补充：reranker 部署后 P99 延迟涨到 260ms，"
        "需要做 batch 累积才能压回 150ms。",
    },
]
