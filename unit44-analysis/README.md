# Unit 44 · 多来源交叉比对与冲突检测

> 3 个来源对同一主题的不同描述（含 2 处冲突 + 1 处补充）。**冲突检测** —— 同一事实在不同来源里数值不同，要标出来。

**完成标准**：能说清「共识 / 冲突 / 补充」三种结果的判定条件，
能解释为什么冲突检测要按单位分组（ms 和 % 不能比），能讲清补充点为什么不进冲突。

## 快速开始

```bash
cd ~/ai-journey/unit44-analysis
/home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --reload --port 8044
```

打开 <http://127.0.0.1:8044/>：默认 3 来源已加载 → 点「交叉比对」看共识 / 冲突 / 补充。
改 B 的 0.55 为 0.62 再比一次，冲突消失。

## 自检

```bash
/home/rik/.cache/aj/runner/bin/python main.py
# 结果：66 通过 / 0 失败
```

九组：默认 fixture、cross_compare 输出结构、共识、冲突、补充点不重复、
sources_summary 字段、边界场景、数值提取、HTTP 层。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `analyzer.py` | `cross_compare(sources)` → `{consensus, conflicts, unique_points, sources_summary}`；`DEFAULT_SOURCES` 内置 3 来源；`_extract_numbers()` / `_extract_delta_pairs()` |
| `main.py` | 4 个接口 + 66 条自检 |
| `static/index.html` | 三列来源编辑 + 冲突红 / 共识绿 / 补充黄 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 3 个来源主题 | 全部 reranker |
| 共识条数 | 1（所有来源都在讨论 reranker） |
| 冲突条数 | 2（起始值 0.62 vs 0.55 / 最终值 0.81 vs 0.79） |
| 补充点条数 | 5（A 的 200 条 / B 的 v2 / C 的 260ms / 150ms / P99） |
| 冲突里没有 ms 单位 | ✓（延迟是补充不是冲突） |
| 补充点不与冲突数值重复 | ✓（0.55/0.62/0.79/0.81 不出现在补充点） |
| 只有 1 个来源 | 无冲突、无共识（不能交叉比对） |
| 两个主题不同的来源 | topic=mixed，无共识 |
| 两个完全相同的来源 | 无冲突 |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 后端存活 + 默认 fixture 数 + 已知主题 |
| GET | `/sources` | 默认 3 个来源（页面用） |
| GET | `/topics` | 已知主题关键词字典 |
| POST | `/compare` | `{sources: [{name,text}]}` → `{topic, consensus, conflicts, unique_points, sources_summary}` |
| POST | `/compare/default` | 跑默认 3 来源（页面默认演示） |
| GET | `/` | 实验台页面 |

## 常见坑

1. **ms 和 % 放一起比。** A 说延迟从 90ms 涨到 180ms，B 说命中率从 0.55 提升到 0.79 ——
   都是「从 X 到 Y」的 delta_pair，但单位不同不能比。本单元按 `(from_unit, to_unit)` 分组，
   同组内才比对。否则 90 和 0.55 都进「起始值冲突」，荒谬。
2. **共识写成「来源数量」。** 「3 个来源」不是共识，共识是「3 个来源都在讲 reranker」。
   共识要有 claim（陈述）和 sources（哪些来源支持）两个字段。
3. **补充点进冲突。** C 提到延迟 260ms —— A/B 都没提延迟，这不是冲突，是 C 独有的补充。
   补充点的判定是「只 1 个来源提到且数值不构成 delta_pair」。
4. **`v2` / `P99` 被当成数值。** `bge-reranker-v2` 的 2、`P99` 的 99 都被 NUMBER_RE 抽出来。
   这是规则识别的局限 —— 生产环境用 LLM + schema 让模型只回「真数值」。
   本单元把这个缺陷留着作为教学点（自检里 `b_sum` 的 numbers 包含 2.0 就是这条的体现）。
5. **完全相同的两个来源误判冲突。** A 和 B 完全一样 → delta_pairs 相同 → from_values 只 1 个 → 不冲突。
   但如果实现写成「任何不同来源的数值都算冲突」就错了。要看「数值是否不同」不是「来源是否不同」。
6. **`sources_count` 为 0 时不返回空结构。** `cross_compare([])` 必须返回 `sources_count=0` 且数组全空，
   不能抛异常 —— Agent 调用时可能传空列表（所有来源都抓失败），要能优雅返回。

## 验收清单

- [ ] `python main.py` 输出 `结果：66 通过 / 0 失败`
- [ ] 页面默认演示看到 2 个红色冲突块（0.62 vs 0.55 / 0.81 vs 0.79）
- [ ] 页面看到 1 个绿色共识块（所有来源都在讨论 reranker）
- [ ] 页面看到黄色补充块（C 的 260ms / 150ms）
- [ ] 把 B 的 0.55 改成 0.62，再比对，冲突只剩 1 个（最终值）
- [ ] 能说清为什么 90ms 和 0.55 不能放进同一组比对

## 下一单元

Unit 45 · 项目 3 交付 —— Research Agent。把 search / fetch / extract / analyze 串起来，
用 LangGraph 的 `StateGraph` 编排，输出带来源引用的 Markdown 报告。
