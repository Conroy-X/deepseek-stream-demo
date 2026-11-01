# Research Agent · 演示脚本

> 3 个端到端演示，每个都能用一条命令跑出来。

## 演示 1：MCP 协议（共识，无冲突）

两篇 MCP 文章（mcp-intro / mcp-error）都在讲 MCP 协议，但讲的角度不同 ——
一篇讲握手流程，一篇讲 isError vs error 帧。analyze 检测到「都在讲 mcp」的共识，
没有冲突（两篇没有同维度的数值对比）。

```bash
cd ~/ai-journey/unit45-project3
/home/rik/.cache/aj/runner/bin/python -c "
from agent import run_research
r = run_research('MCP 协议')
print(r['report'])
"
```

预期：
- 搜索 2 条 / 抓取 2 篇 / 要点 >= 3
- 主题 = mcp
- 共识 1 条（所有来源都在讨论 mcp）
- 冲突 0 条
- 报告含 `## 来源`、`## 各来源要点`、`## 交叉比对`、`### 共识（1）`、`### 冲突（0）`

## 演示 2：RAG（4 个冲突）

rag-tuning（page2）说「reranker 把 top-3 命中率从 0.62 拉到 0.81，延迟从 120ms 涨到 260ms」；
hybrid-search（page5）说「reranker 把 top-3 命中率从 0.55 提升到 0.79，延迟从 80ms 涨到 220ms」。
两条 delta_pair 在同维度（命中率 / 延迟）数值不同 → 4 个冲突。

```bash
cd ~/ai-journey/unit45-project3
/home/rik/.cache/aj/runner/bin/python -c "
from agent import run_research
r = run_research('RAG')
print(r['report'])
"
```

预期：
- 搜索 2 条 / 抓取 2 篇 / 要点 >= 8
- 主题 = reranker
- 共识 1 条（都在讲 reranker）
- 冲突 4 条：
  - 命中率起始值不同（0.55 vs 0.62）
  - 命中率最终值不同（0.79 vs 0.81）
  - 延迟(ms)起始值不同（80 vs 120）
  - 延迟(ms)最终值不同（220 vs 260）
- 补充点 >= 5（rag-tuning 独有的 200 条 / 256 / 512 / 1024 等）

## 演示 3：查不到的主题（优雅降级）

搜「量子计算」—— unit41 的 fixture 里没有这个主题，search 返回 `[]`。
后续节点收到空输入全部返回空，report 渲染「搜索 0 条」而不是抛异常。

```bash
cd ~/ai-journey/unit45-project3
/home/rik/.cache/aj/runner/bin/python -c "
from agent import run_research
r = run_research('量子计算')
print('error:', r.get('error'))
print('search_results:', r.get('search_results'))
print('report head:')
print(r['report'][:300])
"
```

预期：
- `error` 为空（不是错误状态）
- `search_results == []`
- `fetched == []`、`extracted == []`
- `report` 含「# 研究报告：量子计算」+「搜索 0 条 / 抓取 0 篇」
- 整个流程不抛异常

## 端口启动

```bash
cd ~/ai-journey/unit45-project3
/home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --reload --port 8045
```

打开 <http://127.0.0.1:8045/>：默认演示 MCP 协议，点「RAG（有冲突）」按钮看 4 个冲突块。
