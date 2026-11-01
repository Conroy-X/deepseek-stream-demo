# Unit 37 笔记 · LangGraph State

> Unit 34 手写了 while 循环。这一章用 LangGraph 的 StateGraph 替代它 —— 看看框架替你省了什么。

## 本次做了什么
- 写 `graph.py`：ResearchState(TypedDict) + 4 个图构建函数（单节点/三节点/noop/覆盖）+ 手写循环对比。
- 写 `main.py`：4 接口 + 41 自检，0 失败，连跑两次一致。

## 一、TypedDict 不是运行时类型
`class ResearchState(TypedDict)` 在运行时就是 `dict`。它的价值是让开发者知道「这个图里有哪些状态」。
LangGraph 用 dict 操作，TypedDict 只是声明。

## 二、merge 语义：partial dict
每个节点只返回要改的字段。返回 `{"query": "xxx"}` 只改 query，其他保留。
返回 `{}` 什么都不改。

关键：**top-level key 覆盖，不是 deep merge**。
`{"rounds": 2}` 会把 rounds 整个替换为 2，不是在旧值上累加。
所以节点里要 `state.get("rounds", 0) + 1` 自己算好再返回。

## 三、对比手写循环
手写循环（Unit 34）：你自己管 messages 列表，每步 append。
LangGraph：你只管「这一步要改什么」，框架帮你 merge。

实测两者结果完全一致 —— 框架的价值不是「算得更快」，是「结构更清晰、可可视化、可 checkpoint」。

## 一句话总结
**LangGraph 的核心不是图，是 State —— 一个 TypedDict + partial merge 语义。**
