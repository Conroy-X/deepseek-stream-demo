# Unit 42 · 抓网页正文（离线 HTML fixture）

> 拿到 URL 之后怎么把正文从 HTML 里抠出来。**不是「去标签」那么简单** —— 要先去 script/style/nav 再去标签。

**完成标准**：能说清为什么 `re.sub(r"<[^>]+>", "", html)` 是错的，
能解释「先去噪音节点再去标签」和「先去标签再去噪音」结果的差别（后者正文里混着 JS 代码）。

## 快速开始

```bash
cd ~/ai-journey/unit42-fetch-tool
/home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --reload --port 8042
```

打开 <http://127.0.0.1:8042/>：选 URL → 看清洗后正文（无 script / 无 nav / 无广告）。
对照 `data/page1.html` 看原始 HTML 有多脏。

## 自检

```bash
/home/rik/.cache/aj/runner/bin/python main.py
# 结果：81 通过 / 0 失败
```

七组：三步清洗顺序、正文长度合理、page2 侧栏/广告/表格、page3 无 article 容器、
page4 navbar+popup-ad、page5 topnav+sidebar-ad、fixture 全覆盖、错误处理、HTTP 层。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `fetch.py` | `fetch_and_clean(url)` 三步清洗：去广告 → 去 script/style/nav → 取 article 容器 → 剥标签 → 清空白 |
| `main.py` | 4 个接口 + 59 条自检 |
| `data/page1.html` | MCP 入门文：含 `<nav>` + 2 个 `<div class="ad">` + 2 个 `<script>` |
| `data/page2.html` | RAG 调优：含 `<aside class="sidebar">` + `<div class="promo">` + 表格 + `<script>` |
| `data/page3.html` | LangGraph：含 `<nav>` + `<div class="banner-ad">` + `<script src>` + 残留 `<style>` |
| `data/page4.html` | MCP isError：含 `<nav class="navbar">` + 2 个 `<div class="popup-ad">` + `<script>` |
| `data/page5.html` | 混合检索：含 `<nav>` + `<aside class="sidebar-ad">` + `<script>` |
| `static/index.html` | URL 输入 + 清洗后正文 + 元数据对比 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 5 个 fixture URL | `mcp-intro` / `mcp-error` / `rag-tuning` / `hybrid-search` / `langgraph-state` |
| page1 raw / clean | 2044 / 499（比例 0.24，剥了 76%） |
| page1 清洗后不含 `<script` | ✓（包括 `_tracker` / `gtag` / `dataLayer` 全没） |
| page1 清洗后不含 `<nav` | ✓（导航条整段消失） |
| page1 清洗后不含广告文案 | ✓（SuperSearch Pro / MCP 商业课 全没） |
| page1 清洗后含 `-32002` | ✓（正文里的技术细节保留了） |
| page2 表格数字 90/120/180 | ✓（剥了 `<td>` 但内容保留） |
| page2 不含 `<td` `<tr` | ✓（标签残余也剥干净） |
| page3 无 article 容器 | 取整页清洗，仍然不含 script/nav/style |
| URL 不在 fixture | 抛 `FetchError`（HTTP 层报 400，不静默返回空） |
| 空 URL | pydantic 422 |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 后端存活 + fixture 数 + URL 列表 |
| GET | `/fixtures` | 列可抓的 URL |
| POST | `/fetch` | `{url}` → `{url,title,text,length,raw_length,fixture}` |
| GET | `/fetch?url=` | GET 版本（页面用） |
| GET | `/` | 实验台页面 |

## 常见坑

1. **先去标签再去噪音。** `<script>var x="..."</script>` 被你剥标签后变成 `var x="..."`，
   和正文混在一起，再想去掉 JS 代码就不可能了 —— 它已经长得跟正文一模一样。
   顺序必须反过来：先删整个 `<script>...</script>` 块，再剥剩余标签。
2. **只删 `<script>` 不删 `<nav>` / `<header>` / `<aside>`。** 导航条、页头、侧栏都算噪音，
   它们的内容（首页 / 文档 / 博客）混进正文比 JS 更糟 —— JS 你一眼看出是代码，
   导航文字你以为是真的内容。
3. **广告按精确 class 删。** `class="ad"` / `class="promo"` / `class="banner-ad"` 写死，
   fixture 一改就失效。本单元用关键词匹配（`ad` / `promo` / `banner` / `sidebar`），
   匹配范围更宽但会误伤 —— `class="leaderboard-ad"` 也被删（通常是想要的），
   `class="add-note"` 也会被删（可能不想要）。生产环境建议用 readability-lxml 这种成熟库。
4. **不取 article 容器。** `<article>` 标签的内容比整页干净得多（page1/page2 有），
   有就取它，没有再退回整页。本单元 page3 没有 article，全页清洗仍然干净 ——
   但如果 page3 有侧栏广告没被关键词匹配上，没 article 容器就漏了。
5. **`&nbsp;` / `&amp;` 不解码。** HTML entity 留在正文里，模型看到 `&nbsp;&nbsp;` 不知道是空格。
   `&#160;` 这种数字 entity 也常见，生产环境用 `html.unescape()`。
6. **`<br>` 不换成换行。** 直接剥标签的话 `第一行<br>第二行` 变成 `第一行第二行`，
   段落结构丢失。剥标签前先把 `<br>` 换 `\n`，`</p>` `</div>` 也换 `\n`。

## 验收清单

- [ ] `python main.py` 输出 `结果：81 通过 / 0 失败`
- [ ] 页面抓 `mcp-intro`，正文里看不到「SuperSearch」「_tracker」「首页 文档 博客」
- [ ] 页面抓 `rag-tuning`，表格数字 90/120/180 仍在
- [ ] 页面抓 `langgraph-state`，正文里有「StateGraph」「TypedDict」
- [ ] 抓一个 fixture 之外的 URL，看到 400 错误而不是空正文
- [ ] 能说清为什么「先去标签再去 script」是错的

## 下一单元

Unit 43 · 结构化要点提取 —— 拿到清洗后正文，怎么用 `OfflineModel` 提取 3-5 个要点，
且每个要点必须能在原文里找到对应句子（可溯源）。
