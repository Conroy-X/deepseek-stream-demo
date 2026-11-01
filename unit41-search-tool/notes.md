# Unit 41 笔记 · 搜索工具的 schema 该怎么写

> 5 条 fixture、20 行匹配规则、10 行 schema。重点不是搜索本身，是「工具声明」这一层。

## 本次做了什么

- `search.py`：`search(query, max_results=5)` 从 fixture 关键词匹配；`SEARCH_SCHEMA` 钉死工具声明；`list_fixtures()` 给页面用。
- `main.py`：4 个接口 + 55 条自检，覆盖 fixture 全表、关键词命中、空结果、max_results 钳制、schema 形状、HTTP。
- `data/fixtures.json`：5 条结果，3 个主题（MCP 协议 2 / RAG 优化 2 / LangGraph 1），让 fixture 当数据文件改而不用动代码。
- `static/index.html`：搜索框 + 结果列表 + `/tools` 返回的工具声明 + `/fixtures` 的全表。

## 一、fixture 怎么造：覆盖主题，但不平均分配

5 条 fixture 分到 3 个主题分不均，故意做成 2/2/1 ——
让 unit 44 的「多来源冲突」有一条「补充点」可以用：同一主题在不同来源里信息密度不一样，
这是真实搜索结果的特征。

每条 fixture 必须有：`id`（页面展示）、`topic`（人看的）、`title`（模型看的标题）、
`url`、`snippet`（实际匹配字段）。**没有 `content` 字段** ——
内容是 unit 42 `fetch_and_clean` 干的事，搜索只给「卡片」。

实测：每条 snippet 控制在 60-90 字符，命中关键词分布在 title / snippet / topic 三个字段上，
让 `_match` 的「hay 拼接」真的有区分度。

## 二、schema：6 个字段，每个都有理由

| 字段 | 为什么这么写 |
| --- | --- |
| `query: string` | 不写 `enum` —— 不知道模型会问什么；不写 `pattern` —— 模型不一定按你期望的格式 |
| `query` 必填 | 没关键词搜不了 |
| `max_results: integer` | 不是 `number`（number 允许 5.5 没意义） |
| `default=5` | 模型不填就用默认值，省 token |
| `minimum=0, maximum=10` | 上限挡住模型瞎填 100；下限允许「探一下有没有结果」 |
| `additionalProperties: false` | 关掉，否则模型塞 `{"context":"工程师"}` 进来你代码忽略它 = 静默丢字段 |

最容易踩的是 `additionalProperties`。默认值 `true` 看起来宽容，
实际等于「我接受任何字段但我不保证用」—— 模型下次又塞个 `priority` 进来，
你代码还是忽略，模型以为「我说的它都听了」。**关掉比开着安全。**

## 三、匹配规则：长词优先，短词只在没长词时算

```python
long_tokens = [t for t in tokens if len(t) >= 2]  # MCP / RAG / LangGraph
short_tokens = [t for t in tokens if len(t) == 1]  # 单字
# 长词命中即命中；没长词时才看短词
```

为什么这么分：`search("RAG")` 应该命中 RAG 优化主题；但 `search("R")` 也命中就荒唐了。
单字 token 命中概率太高（中文里随便一个字都能匹配一堆），只有 query 是「单字 + 没长词」时才认。

实测：`search("chunk reranker")` 命中 1 条；`search("isError error 帧")` 命中 1 条；
`search("stategraph typeddict")` 小写也能命中（hay 用 `.lower()` 钉住大小写不敏感）。

## 四、无结果返回空列表，不抛异常

这一条是「搜索工具」和「数据库写入」的本质区别。写库失败要报错（用户要知道），
搜索没结果不是错（模型换词就行）。如果 `search("量子计算")` 抛 `ValueError`，
模型看到 500 会以为工具坏了，下次不调它 —— 工具就死了。

## 五、max_results 的钳制有两层

第一层在 schema（`maximum=10`）：pydantic 直接 422 拒绝 `max_results=999`。
第二层在函数（`if max_results > HARD_MAX_RESULTS: max_results = HARD_MAX_RESULTS`）：
绕过 schema 直接调 `search("MCP", max_results=100)` 也不会越界。

为什么两层都做：HTTP 接口走 pydantic，但 `search()` 还会被 unit 45 的 Research Agent
直接 import 调用 —— 那条路径不走 schema。两层钳制 = 不论从哪进来都不会出问题。

实测：`search("MCP", max_results=100)` 命中 2 条，不会切片 100 次；
`search("MCP", max_results=-3)` 返回 `[]`。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 5 条 fixture 覆盖 3 主题 | 2+2+1，每条带 id/topic/title/url/snippet |
| `search(query, max_results=5)` | 返回 `[{title,url,snippet}]`，剥掉 topic/id |
| 无结果返回 `[]` | 空字符串、纯空格、查不到的词都返回 `[]` |
| max_results 限制生效 | `=1` 截断、`=0` 空、`=100` 钳到 10 |
| schema 形状正确 | query string 必填 / max_results int default 5 / additionalProperties false |
| HTTP 接口真能跑 | `/health` `/fixtures` `/tools` `/schema` `/search` `/` 都 200 |
| 自检 ≥ 25 条 | 55 条，连跑两次 0 失败 |

## 一句话总结

**搜索工具不是搜索，是「模型能理解的结构化查询接口」—— schema 是契约，返回形状是承诺。**

## 面试追问

**Q：为什么不直接接 Tavily / SerpAPI？**
A：本单元要的是「没有 API key 也能跑」和「能精确控制结果形状来测后续 Agent」。真接 Tavily 时本单元写的 schema、空列表处理、max_results 钳制全都仍然适用 —— 只是 `_load_fixtures()` 换成 `httpx.get`。

**Q：`additionalProperties: false` 在 OpenAI function-calling 里真的会被强制吗？**
A：会被尊重但不一定强制。OpenAI 把它放进 prompt 当约束，模型一般会遵守；但模型真塞了额外字段，OpenAI 不会替你拒绝 —— 真正的拒绝要靠你的代码（pydantic / 手写校验）。「关掉」是声明意图，不是依赖 enforcement。

**Q：fixture 写死 5 条，模型问不到的主题怎么办？**
A：这就是 unit 42-44 要解决的事 —— 搜索是入口，抓取和提取把单条结果变厚。故意只 5 条，是为了让「查不到」也是一个可演示的场景（`search("量子计算")` 返回 `[]`）。

**Q：`max_results=0` 该返回 `[]` 还是报错？**
A：本单元返回 `[]`。「给我看 0 条」是合法请求，结果就是「没东西」。你认为这是错的，那应该在 schema 里写 `minimum=1` 让它被 422 拒绝 —— 不要让它进到业务逻辑里再报错。
