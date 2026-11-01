# Unit 37 · LangGraph State

> 用 TypedDict 定义 State，跑通单节点图。对比手写 while 循环（Unit 34），看框架帮你省了什么。

**完成标准**：能用 StateGraph 定义 State + add_node + compile + invoke；
能解释 merge 语义（返回 partial dict，top-level key 覆盖，不是 deep merge）；
能说清 LangGraph 和手写循环的本质区别。

## 快速开始
```bash
cd ~/ai-journey/unit37-langgraph-state
uv sync
uv run uvicorn main:app --port 8037
```

## 自检
```bash
uv run python main.py
# 结果：41 通过 / 0 失败
```

## 这个目录里有什么
| 文件 | 作用 |
| --- | --- |
| `graph.py` | ResearchState(TypedDict) + 4 个图构建函数 + 手写循环对比 |
| `main.py` | 4 接口 + 41 自检 |
| `static/index.html` | 三节点图可视化 + merge 演示 |

## 实测
| 项目 | 结果 |
| --- | --- |
| State 字段 | question / query / results / answer / rounds |
| 单节点图 | rewrite → END，rounds=1 |
| 三节点图 | rewrite → search → answer，rounds=3 |
| noop 节点返回 {} | state 完全不变 |
| 覆盖 vs merge | top-level key 覆盖，不是 deep merge |
| 对比手写循环 | 答案一致、轮次一致 |

## 常见坑
1. **以为 TypedDict 是运行时类型**。它是给类型检查器看的，运行时就是 dict。
2. **以为 merge 是 deep merge**。实际是 top-level key 覆盖：返回 `{"rounds": 2}` 会覆盖整个 rounds，不是累加。
3. **没传初始字段**。如果 invoke 时没传 `rounds`，第一个节点里 `state.get("rounds", 0)` 会兜底，但返回的 dict 必须显式设。

## 下一单元
Unit 38 · Node / Edge —— 多节点工作流（改写 → 检索 → 生成），加条件路由。
