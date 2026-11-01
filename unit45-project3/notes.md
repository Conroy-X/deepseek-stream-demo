# Unit 45 笔记 · 把 4 个单元组装成一个 Agent

> 不写新逻辑，只把 unit41-44 的产出串起来。难点不是 LangGraph，是「跨单元拼装时的边界对齐」。

## 本次做了什么

- `agent.py`：`ResearchState` TypedDict、5 个节点（`node_search` / `node_fetch` / `node_extract` / `node_analyze` / `node_report`）、`build_graph()` 编译 StateGraph、`run_research()` 端到端入口、`render_report()` Markdown 渲染。
- `main.py`：4 个接口 + 87 条自检。
- `ARCHITECTURE.md`：链路图、状态契约、失败处理、StateGraph vs 手写 while 的对比。
- `DEMO.md`：3 个演示脚本（MCP 共识 / RAG 冲突 / 查不到）。
- `static/index.html`：拓扑可视化 + 主题输入 + Markdown 报告 + 统计条。

## 一、StateGraph 节点是纯函数，状态合并是框架的事

```python
def node_search(state: ResearchState) -> dict:
    return {"search_results": results, "error": ""}  # 只写自己负责的字段

def node_fetch(state: ResearchState) -> dict:
    return {"fetched": [...]}  # 不写 search_results
```

`TypedDict(total=False)` 让字段都可选，节点返回增量 dict，框架 merge 进 state。
比手写 `state["fetched"] = ...` 省心 —— **节点不需要知道 state 全貌，只关心自己读什么写什么。**
自检里 `node_search 不写 report 字段` 和 `node_fetch 不写 search_results` 两条钉死这个契约。

## 二、跨单元拼装的 4 个边界对齐问题

把 4 个单元组装起来时踩了 4 个坑，每个都是「接口形状看起来对、实际不对」：

**坑 1：unit41 fixture URL 和 unit42 URL_MAP 不一致。** unit41 有 5 条 fixture，
unit42 只覆盖 3 个 URL。Agent 搜到 r2 / r4 的 URL 时 fetch 报「URL 不在 fixture 里」。
解决：在 unit42 补 page4.html / page5.html + 扩 URL_MAP 到 5 个。

**坑 2：extract 默认 max=5 把冲突材料挤掉。** MCP 文章 4 句定义（含「是」的句子）
优先级高于数值句，把带 delta_pair 的句子挤出 top 5。Agent 里把 `EXTRACT_MAX_POINTS` 拉到 10。

**坑 3：node_analyze 把 point 和 source_sentence 都拼进 source text。** point 是 source_sentence
的截断版，两者都含 delta_pair 的数字。`_extract_delta_pairs` 扫两遍，冲突值被算两次。
解决：只拼 `source_sentence`（verbatim）。

**坑 4：主题检测对 MCP 文章失效。** MCP 文章的要点都是 isError / error 帧，
不含字面 "mcp"。`_find_topic` 返回 unknown，共识检测失败。解决：把 URL 拼进 source text，
URL 含 "mcp-intro" / "mcp-error"，主题检测能命中。

这 4 个坑都不是单看一个单元能发现的 —— 只有组装时才暴露。
教学价值：**单元独立正确 ≠ 组装正确，边界对齐是工程问题不是算法问题。**

## 三、冲突检测的实测：RAG 主题 4 个冲突

rag-tuning（page2）说「0.62 → 0.81 / 120ms → 260ms」；
hybrid-search（page5）说「0.55 → 0.79 / 80ms → 220ms」。

按单位分组后：
- `("","")` 组：A 的 0.62→0.81 和 B 的 0.55→0.79，2 个 pair，可比 → 2 个冲突（起始 + 最终值）
- `("ms","ms")` 组：A 的 120→260 和 B 的 80→220，2 个 pair，可比 → 2 个冲突

总共 4 个冲突，每个值的 sources 各 1 个（去重后）。报告里 4 个红色块。

如果没按单位分组：A 的 from 值 `{0.62, 120}` 和 B 的 `{0.55, 80}` 全部「不同」，
会变成 4 个值乱比 —— 0.62 vs 120 vs 0.55 vs 80，毫无意义。unit44 的按单位分组规则在这里发挥了真实价值。

## 四、失败处理：3 种「空」场景分开处理

| 场景 | 行为 |
| --- | --- |
| 空 topic | `run_research` 走兜底分支返回 `{error: "topic 不能为空", report: "# 研究报告\n\n> 错误：..."}` |
| 查不到的主题 | `search_results = []`；fetch / extract 收到空输入返回空；report 渲染「搜索 0 条」 |
| 单条 URL fetch 失败 | `node_fetch` 标 `ok=False` 不抛；其他来源继续进 analyze |

这 3 种「空」都不能掀翻 Agent。自检里 4 条断言钉死：`空 topic 整个流程不崩`、`查不到的主题 report 不崩`、`node_fetch 未知 URL 不抛异常`、`空 topic fetched=[]`。

## 五、为什么用 StateGraph 而不是手写 while

Unit 34 的 Agent 是 while 循环（模型决定下一步），适合「模型驱动」。
Unit 45 是固定 5 步流水线（节点顺序写死），适合「数据驱动」—— StateGraph 的优势：

1. **节点是纯函数**，可单测。
2. **状态合并自动**，节点只返回增量。
3. **拓扑可见**，`build_graph()` 的 `add_edge` 就是图。
4. **扩展空间**，后续加条件路由只改 `add_conditional_edges`。

代价：多约 60ms 框架开销，教学范围不值一提。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 端到端跑通 | MCP / RAG / LangGraph / 查不到 4 种场景都跑过 |
| 报告含来源引用 | `## 来源` 列表 + 每个要点带 source_sentence + 抓取状态 URL |
| 冲突被标出 | RAG 主题 4 个冲突（命中率起 / 终 + 延迟起 / 终） |
| 空主题返回提示 | `error` 字段 + 报告含「topic 不能为空」 |
| LangGraph StateGraph | `build_graph()` 编译，5 节点 6 边 |
| 自检 ≥ 30 条 | 87 条，连跑两次 0 失败 |

## 一句话总结

**Agent 项目的难点不是写新代码，是把已有零件拼起来时把边界对齐 —— 单元正确 ≠ 组装正确。**

## 面试追问

**Q：为什么不直接用 LangChain 的 AgentExecutor？**
A：本单元是固定 5 步流水线，不是「模型决定下一步」的 Agent。AgentExecutor 适合后者，前者用 StateGraph 更直接。如果做成「模型决定调哪个工具」，那就回到 unit 34 的手写循环或 AgentExecutor。

**Q：5 个节点串行，性能怎么办？**
A：教学版串行足够。生产应该 `asyncio.gather` 并行抓取多个 URL，但「节点是纯函数」不变 —— 改的是 fetch 内部实现，不是节点接口。LangGraph 1.2+ 支持异步节点，可以直接 `async def node_fetch`。**Q：节点失败要重试吗？**
A：本单元不重试。单条 URL fetch 失败就标 `ok=False` 跳过，其他来源继续。如果某条 URL 是必须的（如权威来源），应该在外面用「必须成功的来源列表」校验，而不是在 Agent 里重试 —— Agent 的职责是「跑通」，重试是策略，应该外置。

**Q：冲突检测用 LLM 不是更准？**
A：会，但 `按单位分组` 这条规则不变。LLM 可能会把「延迟」和「命中率」都当冲突，规则版兜底校验「同维度」才能挡住。教学版用规则让你看到这条规则，生产用 LLM + 规则双重校验。
