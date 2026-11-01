# Unit 45 · 项目 3 交付：Research Agent

> 把 unit41-44 串起来：输入主题 → 搜索 → 抓取 → 提取 → 分析 → 输出带来源引用的 Markdown 报告。
> LangGraph `StateGraph` 编排，5 节点 1 条链。

**完成标准**：能说清 `StateGraph` 比「手写 while 循环」多给了什么（节点是纯函数 + 状态自动合并），
能解释每个节点只写增量字段的好处，能讲清跨单元 import 而不是复制代码的理由。

## 这个项目能写进简历的三句话

- 用 LangGraph `StateGraph` 编排 search → fetch → extract → analyze → report 五节点研究 Agent，节点全部纯函数化、可单测，状态合并由框架自动完成。
- 实现多来源冲突检测：按 `(from_unit, to_unit)` 单位签名分组比对 delta_pair，避免不同维度（如命中率 vs 延迟）误判为冲突。
- 反 hallucination 的可溯源契约：每个要点都带 `source_sentence`，运行时用 `verify_sourcing` 校验 `source_sentence in text`，找不到就丢掉。

## 已知局限

- `extract_key_points` 是规则版（识别定义 / 数值 / 因果三类句），覆盖约 80% 的文档句式。
- `bge-reranker-v2` 的 `2`、`P99` 的 `99` 会被 NUMBER_RE 当成数值 —— 规则识别的已知瑕疵。
- 5 个 URL 串行抓取，没有 `asyncio.gather` 并行。
- `node_analyze` 把 URL 拼进 source text 让主题检测命中 —— 教学版补丁，生产应让 LLM 抽主题字段。
- 没有重试 / 条件路由，固定 5 节点。加条件路由要补 `max_steps` 防死循环。

## 快速开始

```bash
cd ~/ai-journey/unit45-project3
/home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --reload --port 8045
```

打开 <http://127.0.0.1:8045/>：默认演示 MCP 协议 → 点「RAG（有冲突）」看 4 个冲突块。

## 自检

```bash
/home/rik/.cache/aj/runner/bin/python main.py
# 结果：87 通过 / 0 失败
```

九组：StateGraph 构图、端到端 MCP 跑通、报告含来源引用、RAG 主题冲突被标出、
MCP 共识正确、空主题返回提示、查不到的主题优雅降级、单节点单测、render_report 单测、HTTP 层。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `agent.py` | `ResearchState` TypedDict、5 个节点函数（`node_search` 等）、`build_graph()`、`run_research()`、`render_report()` |
| `main.py` | 4 个接口 + 87 条自检 |
| `ARCHITECTURE.md` | 链路图、状态契约、跨单元依赖、失败处理、为什么用 StateGraph |
| `DEMO.md` | 3 个演示脚本（MCP 共识 / RAG 冲突 / 查不到） |
| `static/index.html` | 拓扑可视化 + 主题输入 + Markdown 报告 + 统计条 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 拓扑节点数 | 5（search / fetch / extract / analyze / report） |
| 拓扑边数 | 6（含 START / END） |
| MCP 协议端到端 | 搜索 2 / 抓取 2 / 要点 >= 3 / 共识 1 / 冲突 0 |
| RAG 端到端 | 搜索 2 / 抓取 2 / 要点 >= 8 / 共识 1 / 冲突 4 |
| 冲突值（RAG） | 0.62 vs 0.55 / 0.81 vs 0.79 / 120ms vs 80ms / 260ms vs 220ms |
| 空主题 | 返回 `error=topic 不能为空` + 报告含提示 |
| 查不到的主题 | `search_results=[]`，不抛异常 |
| 跨单元 import | 4 个兄弟单元全部 import 复用，无重复实现 |
| 单节点是纯函数 | 每个节点只写自己负责的字段（增量返回） |
| `extract_max_points` | 10（拉高避免 definition 句子把数值句挤出 top N） |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 后端存活 + 拓扑节点列表 + 跨单元依赖 |
| GET | `/graph` | 节点和边的结构（页面用） |
| POST | `/research` | `{topic, max_results}` → 完整 state（含 report / analysis） |
| POST | `/research/report` | 同上但只返回 Markdown（`text/plain`） |
| GET | `/` | 实验台页面 |

## 常见坑

1. **`extract_key_points` 默认 max=5 把冲突材料挤掉。** definition 优先级高于 number，
   一篇有 4 句定义的文档会把带 delta_pair 的数值句挤出 top 5。本单元把 `EXTRACT_MAX_POINTS` 拉到 10。
2. **`node_analyze` 同时拼 `point` 和 `source_sentence` 导致 delta_pair 出现两次。**
   `point` 字段是 source_sentence 的截断版，两者都含 delta_pair 的数字 ——
   `_extract_delta_pairs` 在文本里扫两遍，冲突值被算两次。只拼 `source_sentence` 一次。
3. **主题检测失败导致 consensus=0。** MCP 文章的要点都是关于 isError / error 帧，
   不含字面 "mcp"，`_find_topic` 返回 unknown。把 URL 拼进 source text 让主题词可见。
4. **`fetch` 失败掀翻整个 Agent。** 单条 URL 不在 URL_MAP 里就抛 `FetchError`，
   在 `node_fetch` 里 catch 住标 `ok=False`，其他来源继续抓。
5. **`StateGraph` 节点不是纯函数。** 节点直接改 `state` 字段而不返回增量 dict，
   框架 merge 不到，后续节点看到的 state 还是旧的。`return {"fetched": ...}` 才对。
6. **跨单元 import 走相对路径。** `from ..unit41.search import search` 在 LangGraph 编译时报错。
   本单元用 `sys.path.insert` 把 4 个兄弟单元加进 path，import 公开函数。

## 验收清单

- [ ] `python main.py` 输出 `结果：87 通过 / 0 失败`
- [ ] 页面默认演示 MCP 协议，看到 2 篇来源 + 1 条共识 + 0 条冲突
- [ ] 页面点「RAG（有冲突）」看到 4 条红色冲突（0.62 vs 0.55 等）
- [ ] 页面点「查不到」看到「搜索 0 条」但不报错
- [ ] `/research/report` 返回 `text/plain` 的 Markdown
- [ ] 能在 `agent.py` 里指出每个节点函数和它读 / 写的字段

## 下一单元

Unit 46 · MCP 是什么 —— 把工具从「Python 函数」搬到「JSON-RPC 服务器」。
本单元的 Research Agent 是「函数链」视角的终点，下一单元开始「服务化」视角。
