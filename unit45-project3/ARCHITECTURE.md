# Research Agent · 架构与链路

> LangGraph `StateGraph` 把 5 个节点串成一条链。本单元不做条件路由（教学版），
> 但 `StateGraph` 的结构天然支持后续扩展（失败重试 / 并行抓取 / 条件分支）。

## 拓扑

```
START
  │
  ▼
┌─────────┐   topic, max_results
│ search  │   ─────────────────►  search_results: [{title,url,snippet}]
└─────────┘
  │
  ▼
┌─────────┐   search_results
│ fetch   │   ─────────────────►  fetched: [{url,title,text,length,ok,error}]
└─────────┘
  │
  ▼
┌─────────┐   fetched
│ extract │   ─────────────────►  extracted: [{url, points:[{point,source_sentence}]}]
└─────────┘
  │
  ▼
┌─────────┐   extracted
│ analyze │   ─────────────────►  analysis: {topic, consensus, conflicts, unique_points, sources_summary}
└─────────┘
  │
  ▼
┌─────────┐   全部状态
│ report  │   ─────────────────►  report: "# 研究报告：..."
└─────────┘
  │
  ▼
END
```

## 状态契约：每个节点只写自己负责的字段

| 节点 | 读 | 写 |
| --- | --- | --- |
| `search` | `topic`, `max_results` | `search_results`, `error` |
| `fetch` | `search_results` | `fetched` |
| `extract` | `fetched` | `extracted` |
| `analyze` | `extracted`（间接读 `topic` 写进 source text） | `analysis` |
| `report` | 全部 | `report` |

`TypedDict(total=False)` 让所有字段都可选 —— 节点返回的 dict 只含它写的字段，
LangGraph 框架自动 merge 进 state。这是比手写 while 循环省心的地方：
**节点是纯函数，不持有状态，状态合并是框架的事。**

## 跨单元依赖

```
unit45-project3/agent.py
    │
    ├── imports unit41.search.search(query, max_results) → [{title,url,snippet}]
    ├── imports unit42.fetch.fetch_and_clean(url) → {url,title,text,length,raw_length,fixture}
    ├── imports unit43.extract.extract_key_points(text, max_points) → [{point,source_sentence}]
    │                verify_sourcing(text, points) → [{point,source_sentence,found,source_pos}]
    │                OfflineModel().complete(messages) → OpenAI message
    └── imports unit44.analyzer.cross_compare(sources) → {topic,consensus,conflicts,unique_points,sources_summary}
```

通过 `sys.path.insert` 把 4 个兄弟单元加进 path，import 他们的公开函数。
**不复制代码、不重新实现** —— unit45 的教学价值就是把前 4 个单元的产出组装起来。

## 失败处理

| 失败类型 | 处理 |
| --- | --- |
| `topic` 为空 | `node_search` 返回 `error` 字段；`run_research` 走兜底分支返回提示报告 |
| `topic` 在 fixture 里查不到 | `search_results = []`；后续节点收到空输入，返回空列表；报告渲染「搜索 0 条」 |
| 单条 URL 抓取失败 | `node_fetch` 标 `ok=False` 不抛；`node_extract` 跳过这条；其他来源照常进入 analyze |
| 单篇正文没命中三类句 | `extract_key_points` 返回 `[]`；`node_analyze` 把它排除在 sources 之外 |
| sources 不足 2 个 | `node_analyze` 返回 `{note: "来源不足 2 个"}`；报告渲染 note |

## 为什么用 StateGraph 而不是手写 while

Unit 34 的 Agent 是 while 循环（模型决定下一步调什么工具），适合「模型驱动」的 Agent。
Unit 45 是固定 5 步流水线（节点顺序写死），适合「数据驱动」的 Agent —— 用 StateGraph 的优势是：

1. **节点是纯函数**，可以单测（`node_search(state)` 不依赖外部状态）。
2. **状态合并自动**，节点只返回增量，不需要手写 `state["fetched"] = ...`。
3. **拓扑可见**，`build_graph()` 里的 `add_edge` 就是拓扑图，可视化直接对得上。
4. **扩展空间**，后续要加条件路由（如 fetch 失败重试 / 并行抓多 URL）只改 `add_conditional_edges`，节点函数不动。

代价：多 60ms 的框架开销（实测 unit45 比手写同样 5 步慢约 60ms）。教学范围不值一提。

## 已知局限

- `extract_key_points` 的规则版会把 `bge-reranker-v2` 的 `2` 当数值（unit44 已记录）。
- `node_analyze` 把 URL 拼进 source text 让主题检测命中 —— 这是「教学版补丁」，
  生产应该让 LLM 抽真正的主题字段。
- 没有重试 / 并行：5 个 URL 是串行抓的，生产应该 `asyncio.gather`。
- 没有 max_steps：固定 5 节点，不会死循环。但如果加条件路由就要补 max_steps。
