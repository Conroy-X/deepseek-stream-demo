# Unit 44 笔记 · 冲突检测的难点是按维度分组

> 数值不同不等于冲突。0.62 和 90 都跟 0.55 不同，但前者是冲突、后者是不同维度。本单元的核心是「按单位分组」。

## 本次做了什么

- `analyzer.py`：`cross_compare(sources)` 输出 `{topic, consensus, conflicts, unique_points, sources_summary}`；`_extract_numbers()` / `_extract_delta_pairs()` 用正则抽数值和「从 X 到 Y」的对；`DEFAULT_SOURCES` 内置 3 来源（A/B/C 各一段，含 2 处冲突 + 1 处补充）。
- `main.py`：4 个接口 + 66 条自检。
- `static/index.html`：三列来源可编辑 + 红 / 绿 / 黄三色块标冲突 / 共识 / 补充。

## 一、3 个来源的设计：2 冲突 + 1 补充

```python
DEFAULT_SOURCES = [
  {"name": "A · RAG 实测",   "text": "reranker 把 top-3 命中率从 0.62 拉到 0.81，但延迟从 90ms 涨到 180ms。"},
  {"name": "B · 另一项实测", "text": "reranker 把 top-3 命中率从 0.55 提升到 0.79，用了 bge-reranker-v2。"},
  {"name": "C · 延迟补充",   "text": "补充：reranker 部署后 P99 延迟涨到 260ms，需要 batch 累积才能压回 150ms。"},
]
```

故意这样设计：A 和 B 都讲命中率提升（同维度，数值不同 → 冲突），A 还讲延迟（90→180ms），
C 也讲延迟（260ms / 150ms）但不是 delta_pair（无「从 X 到 Y」结构），所以延迟是补充点而不是冲突。

实测：cross_compare 输出 `consensus=1 / conflicts=2 / unique_points=5`。冲突是「命中率起始值不同」（0.62 vs 0.55）和「命中率最终值不同」（0.81 vs 0.79），延迟没进冲突。

## 二、按单位分组：ms 和 % 不能比

```python
unit_groups: dict[tuple[str, str], list] = {}
for ps in pair_sources:
    sig = (ps["pair"]["from_unit"], ps["pair"]["to_unit"])
    unit_groups.setdefault(sig, []).append(ps)

for sig, group in unit_groups.items():
    if len(group) < 2: continue  # 同单位只有 1 个 pair 不能比对
    # 比对 from 和 to 值
```

A 的 `0.62→0.81`（无单位）和 `90ms→180ms` 是不同单位签名 `("","")` vs `("ms","ms")`，分开两组。
B 只有 `0.55→0.79`（无单位），落到 `("","")` 组。所以 `("","")` 组有 2 个 pair 可比；
`("ms","ms")` 组只有 1 个 pair，不可比，不进冲突。

如果没按单位分组：A 的 from 值 `{0.62, 90}`，B 的 `{0.55}`，三个数都不同都成「冲突」—— 荒谬。

## 三、补充点不进冲突：判断条件是「不在 delta_pair 里」

```python
pair_values: set[float] = set()
for ps in pair_sources:
    pair_values.add(ps["pair"]["from"])
    pair_values.add(ps["pair"]["to"])
# 只 1 个来源独有的数值，且 val 不在 pair_values 里，才算补充点
for (val, unit), srcs in value_unit_sources.items():
    if len(srcs) == 1 and val not in pair_values:
        unique_points.append(...)
```

A 的 200 条文档、C 的 260ms —— 数值只 1 个来源提到，且不在任何 delta_pair 里 → 补充点。
A 的 90ms / 180ms 在 delta_pair 里 → 不进补充点。这条规则保证：冲突的数值不会重复出现在补充点里。
自检 `补充点不与冲突数值重复` 钉死它。

## 四、共识：用主题关键词作为 claim

```python
if topic != "mixed" and topic != "unknown":
    hit_sources = [s["name"] for s in summaries if s["topic"] == topic]
    if len(hit_sources) >= 2:
        consensus.append({"claim": f"所有来源都在讨论 {topic}", "sources": hit_sources})
```

本单元的共识是粗粒度的「都在讲同一主题」。生产应该用 LLM 抽「具体共识陈述」
（如「3 个来源都说 reranker 提升了命中率」），但教学版用主题关键词够。
`hit_sources >= 2` 是关键：只 1 个来源不形成共识。

## 五、规则识别的局限：v2 / P99 被当数值

`bge-reranker-v2` 的 `2`、`P99` 的 `99` 都被 `NUMBER_RE` 抽出来。
自检里 `b_sum` 的 numbers 包含 `2.0` —— 这就是规则版的局限展示。
解决方法是用更严格的正则或 LLM + schema。本单元留着这个缺陷作为教学点 ——
让自检体现「规则版的局限」比假装它完美更有教学价值。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 3 个来源含 2 冲突 + 1 补充 | A/B 命中率冲突，C 延迟补充 |
| `cross_compare(sources)` | 返回 `{topic, consensus, conflicts, unique_points, sources_summary}` |
| 检测到冲突 | 2 条（起始值 + 最终值），按单位分组 |
| 共识点正确 | 1 条，列出全部 3 个来源 |
| 补充点不重复 | 不与冲突数值重复，每个只属于 1 个来源 |
| HTTP 接口真能跑 | `/health` `/sources` `/topics` `/compare` `/compare/default` `/` 都 200 |
| 自检 ≥ 25 条 | 66 条，连跑两次 0 失败 |

## 一句话总结

**冲突检测不是「找不同」，是「在同维度里找不同」—— 单位签名分组是这道闸门。**

## 面试追问

**Q：为什么不用 LLM 做冲突检测？**
A：本单元是教学，要让「按单位分组」这条规则可见。生产环境用 LLM 可以，但「同维度比对」这件事不变 —— LLM 输出冲突后你仍然要校验「这两个数值是不是同维度」，否则 LLM 把 90ms 和 0.55 也当成冲突，你需要规则版兜底。

**Q：补充点和冲突的边界是什么？**
A：补充点是「只 1 个来源提到」且「不构成 delta_pair」。如果一个数值只 1 个来源提到但是 delta_pair 的部分（如 A 的 90ms→180ms，B 没提延迟），它不算补充点 —— 它是「未比对」的 delta，留在 sources_summary 里。

**Q：3 个来源都是同主题，怎么决定共识陈述？**
A：本单元用主题关键词做粗粒度共识。生产用 LLM 抽细粒度陈述（如「都说 reranker 提升了命中率」），但需要 LLM 输出 schema 化的 `{claim, sources}` 列表，然后校验 sources 是否真的支持 claim。

**Q：完全相同的两个来源为什么不算冲突？**
A：冲突的定义是「同一事实数值不同」，数值相同就不是冲突。文本完全一样时 from_values 只有 1 个键 → 不触发冲突分支。这是「看数值不看来源」的体现。
