# Unit 41 · 搜索工具（离线 fixture）

> 不联网、不要 API key、不真调搜索。先解决两件事：**fixture 怎么造**、**工具 schema 怎么写**。

**完成标准**：能说清 `query` 为什么是 string（不是 enum）、`max_results` 为什么有 default，
返回为什么必须是结构化列表而不是一坨文本，能解释「无结果返回空列表」和「抛异常」对模型意味着什么。

## 快速开始

```bash
cd ~/ai-journey/unit41-search-tool
/home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --reload --port 8041
```

打开 <http://127.0.0.1:8041/>：搜索框敲 `MCP 协议` / `RAG` / `LangGraph`，
看结果列表；敲 `量子计算` 看「空列表而非报错」长什么样。

## 自检

```bash
/home/rik/.cache/aj/runner/bin/python main.py
# 结果：55 通过 / 0 失败
```

七组：fixture 全覆盖、按关键词命中、无结果返回空列表、max_results 限制生效、
schema 形状、外部 fixture 文件可被读出、HTTP 层。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `search.py` | `search(query, max_results=5)`、`SEARCH_SCHEMA`、`search_tool_spec()`、`list_fixtures()` |
| `main.py` | 4 个接口 + 38 条自检 |
| `data/fixtures.json` | 5 条 fixture（覆盖 3 个主题） |
| `static/index.html` | 搜索框 + 结果列表 + 工具声明 + fixture 全表 |

## 实测

| 项目 | 结果 |
| --- | --- |
| fixture 总数 | 5 条 |
| 主题覆盖 | MCP 协议（2）/ RAG 优化（2）/ LangGraph（1） |
| 查 `MCP 协议` | 命中 2 条 |
| 查 `RAG` | 命中 2 条 |
| 查 `LangGraph` | 命中 1 条 |
| 查 `量子计算` | `[]`（空列表，不是 500） |
| 查空字符串 | `[]`（不抛异常） |
| `max_results=1` 查 `协议` | 1 条（命中 2 条但被截断） |
| `max_results=0` | `[]`（合法请求，不是报错） |
| `max_results=100`（直接调 `search`） | 被钳到 10，仍只返回 2 条 |
| schema `additionalProperties` | `false`（挡住模型瞎塞字段） |
| schema `query.enum` | 不存在（不限制模型问什么） |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 后端存活 + fixture 数 + 主题列表 + 默认 max_results |
| GET | `/fixtures` | 列全部 fixture（页面用，看到工具背后有什么） |
| GET | `/tools` | OpenAI function-calling 格式的工具声明 |
| GET | `/schema` | 裸 schema |
| POST | `/search` | `{query, max_results}` → `{query, count, results}` |
| GET | `/` | 实验台页面 |

## 常见坑

1. **把搜索结果拼成一坨字符串返回。** 模型拿到 `"1. xxx\n2. yyy\n..."` 还得自己切片，
   切错就漏掉一条。结构化列表 `[{title,url,snippet}]` 让它一眼看到「3 条」。
2. **`query` 写成 `enum`。** 你永远不知道模型会用什么词，把它绑死成「MCP / RAG / LangGraph」
   模型就永远问不到第四个主题。enum 用于「世界就这几样」的字段（如城市）。
3. **`max_results` 不给 default。** 模型每次都得显式填，浪费 token；不给上限模型会填 100，
   浪费请求。`default=5, minimum=0, maximum=10` 一组就把行为钉死。
4. **无结果抛 `ValueError`。** 模型看到 500 会以为工具坏了，下次就不再调它了。
   返回 `[]` 让它自己决定换词还是承认查不到 —— 这是「搜索工具」和「数据库写入」的本质区别。
5. **`additionalProperties=true`。** 模型会塞 `{"query":"MCP","context":"用户是工程师"}` 进来，
   你的 schema 接受了但代码忽略它 —— 静默丢字段是最难调试的 bug。
6. **fixture 字段往外漏。** 内部 `id` / `topic` 是给页面用的，工具返回要剥掉只留 `title/url/snippet`。
   模型看到 `id:"r1"` 不会更聪明，反而可能照搬进答案。

## 验收清单

- [ ] `python main.py` 输出 `结果：55 通过 / 0 失败`
- [ ] 页面搜 `MCP 协议` 看到 2 条结果
- [ ] 页面搜 `量子计算` 看到绿色提示「空列表（不是报错）」
- [ ] `max_results` 填 1，搜 `协议` 只看到 1 条
- [ ] `/tools` 返回的 schema 里 `additionalProperties=false`，`query` 没有 `enum`
- [ ] 能说清「无结果返回空列表」和「抛异常」对模型行为的差别

## 下一单元

Unit 42 · 抓网页正文 —— 拿到 URL 之后怎么把正文从 HTML 里抠出来，
为什么正文提取不是「去标签」那么简单（要先去 script/style/nav 再去标签，否则正文里混着 JS 代码）。
