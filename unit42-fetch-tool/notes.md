# Unit 42 笔记 · 正文提取不是「去标签」那么简单

> 一个 `re.sub(r"<[^>]+>", "", html)` 是新手最容易写出的 bug。本单元把「先去噪音再去标签」这件事用断言钉死。

## 本次做了什么

- `fetch.py`：`fetch_and_clean(url)` 三步清洗（去广告 → 去 script/style/nav → 取 article 容器 → 剥标签 → 清空白）；`URL_MAP` 把 URL 映射到本地 fixture；`FetchError` 区分「URL 不存在」和「正文为空」。
- `main.py`：4 个接口 + 81 条自检。
- 5 个 HTML fixture：page1（MCP 入门）/ page2（RAG 调优）/ page3（LangGraph）/ page4（MCP isError）/ page5（混合检索），每个都故意塞了 script + nav + 广告。
- `static/index.html`：URL 输入 + 清洗后正文 + 元数据对比。

## 一、清洗的顺序：三步，反过来就不行

```python
step1 = _strip_ad_blocks(raw)      # 1. 删广告 div / aside（关键词匹配 class/id）
step2 = _strip_noise_blocks(step1) # 2. 删 script/style/nav/header/footer/aside/noscript 整块
step3 = _pick_article(step2)       # 3. 优先取 <article>/<main> 内容（更干净）
step4 = _strip_tags(step3)         # 4. 剥剩余 HTML 标签
text  = _clean_text(step4)         # 5. 解码 entity、折叠空行、行首尾空白
```

为什么顺序不能反：`<script>var x="hello";</script>` 剥标签后变成 `var x="hello";`，
和正文混在一起。这时候你想再去 JS 代码 —— 不可能，它已经长得跟正文一模一样。
断言 `"_tracker" not in r["text"]` 和 `"gtag" not in r["text"]` 钉死这件事。

实测：page1 原始 2044 字符，清洗后 499 字符（剥了 76%），比例 0.244。
如果不删 script，`var _tracker = "UA-12345"` 这种字符串会出现在正文里。

## 二、广告特征匹配：宽匹配 vs 精确匹配

```python
AD_KEYWORDS = ("ad", "ads", "promo", "banner-ad", "sidebar-promo", "sponsor")
# 匹配 class 或 id 包含这些关键词的 div / aside / section
```

fixture 故意写了三种广告 markup：

- page1：`<div class="ad">` —— 直接命中关键词 `ad`
- page2：`<aside class="sidebar">` + `<div class="promo">` —— `sidebar` 没在我的关键词里，但 `promo` 命中
  （注意：sidebar 整块没被删，断言 `相关阅读 not in text` 验证 `sidebar` 内容也消失了，
  因为 `<aside>` 在 NOISE_TAGS 里被整块删了 —— 顺序是先去广告再去 nav/aside，但 sidebar 也命中 `aside` 标签）
- page3：`<div class="banner-ad">` —— 命中关键词 `banner-ad`

宽匹配的代价：`class="add-note"` 会误删。生产建议用 readability-lxml，它走 DOM 树 + 文本密度算法。

## 三、article 容器优先：能取就别取整页

`<article>` 标签的内容是 HTML5 语义化标签里专门标记正文的。
page1 / page2 有 `<article>`，page3 没有。

有就取，没有就退回整页 —— 这是兜底逻辑。如果 page3 有侧栏广告没被关键词匹配上，
没 article 容器就漏了。这就是为什么 page3 fixture 故意没有 article：测兜底路径。

实测：page3 清洗后不含「培训班 ¥199」「banner-ad」「analytics.js」—— 兜底路径也能干净。

## 四、URL_MAP 把 URL 和本地文件解耦

```python
URL_MAP = {
    "https://example.com/mcp-intro": "page1.html",
    "https://example.com/rag-tuning": "page2.html",
    "https://example.com/langgraph-state": "page3.html",
}
```

URL 长得像真 URL（`https://example.com/...`），实际读本地 fixture —— 这样 unit 45 的 Research Agent
把 unit41 搜出来的 URL 直接喂给 `fetch_and_clean`，不需要改 URL 形状。这是离线第一原则的关键技巧：
**接口形状像真的，数据是假的。**

## 五、FetchError vs 空正文：区分两种「没东西」

URL 不在 fixture → 抛 `FetchError`（结构错误，HTTP 400）。
URL 在 fixture 但正文为空 → 返回 `{text: "", length: 0}`（合法响应）。

为什么不都抛异常：unit 45 的 Research Agent 会处理多个 URL，其中某个 fixture 真的空了，
Agent 应该跳过它而不是整个崩。但 URL 本身不在 fixture 是配置错，要早点报出来。
断言 `"空 URL 抛 FetchError"` 和 `"URL 不在 fixture 抛 FetchError"` 把这两个分支钉死。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 5 个 HTML fixture | page1~page5，每个含 script + nav + 广告 |
| `fetch_and_clean(url)` | 三步清洗，返回 `{url,title,text,length,raw_length,fixture}` |
| 不含 `<script>` | 5 个 page 都断言过 |
| 不含 `<nav>` | page1/page3 断言过（page2 用 aside） |
| 正文长度合理 | page1 比例 0.244，在 0.2~0.7 区间 |
| 广告 div 被去掉 | SuperSearch / 商业课 / 付费课 / 培训班 都断言不在正文里 |
| HTTP 接口真能跑 | `/health` `/fixtures` `/fetch` POST/GET `/` 都 200 |
| 自检 ≥ 25 条 | 81 条，连跑两次 0 失败 |

## 一句话总结

**正文提取的难度不在剥标签，在「剥之前先分清什么是正文什么是噪音」—— 顺序反一次，结果全错。**

## 面试追问

**Q：为什么不用 BeautifulSoup / lxml？**
A：本单元的教学重点是「顺序」，不是「解析器」。用 BeautifulSoup 写出来的顺序问题一样存在 —— 你一样得先 `for s in soup(["script","style","nav"]): s.decompose()` 再 `soup.get_text()`。生产环境当然用 BeautifulSoup 或 readability-lxml，但本单元用正则是因为正则让你看到每一步删掉了什么 —— 库帮你藏起来反而学不到。

**Q：广告匹配这么宽，会误伤吗？**
A：会。`class="add-note"` / `id="header-ad-section"` 都会被删。前者是误伤（可能是正文），后者是你想要的。生产环境的做法是用 readability-lxml 的文本密度算法：算每个 div 的「文本 / 标签」比例，密度高的算正文。本单元的关键词匹配是教学版的简化。

**Q：`<article>` 容器如果嵌套多层怎么办？**
A：`re.search` 用 `.*?` 非贪婪匹配会取最外层 `<article>` 到第一个 `</article>`，如果嵌套就会断在中间。生产用 BeautifulSoup 的 `.find("article")` 走 DOM 树就没这个问题。本单元 fixture 没有嵌套 article，断言里不测这条 —— 教学范围控制。

**Q：`&nbsp;` 这种 entity 不解码会怎样？**
A：正文里到处是 `&nbsp;&nbsp;&nbsp;` 模拟缩进，模型读不懂这是空格，会把它当文字算 token。`html.unescape()` 一行解决，本单元手写了一个 mini 版（覆盖 7 个常见 entity），够 fixture 用。
