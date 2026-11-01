"""Unit 42 · 抓网页正文 —— 离线 HTML fixture。

教学核心：**正文提取不是「去标签」那么简单**。

很多人写 `fetch_and_clean` 上来就 `re.sub(r"<[^>]+>", "", html)` —— 错的。
结果是正文里混着 JS 代码、CSS、广告文案、导航链接，模型看到的是：

    var _tracker = "UA-12345"; MCP 协议入门 首页 文档 博客 ...

正确的顺序是：

1. **先去噪音节点**：`<script>` / `<style>` / `<nav>` / 广告 div（带特征 class 或 id）。
2. **再剥剩余 HTML 标签**：这时候剩下的是真正文 + 内联标签，去标签才是干净的文本。
3. **最后整理空白**：多个空行、行首尾空格、`&nbsp;` 都要清理。

把顺序反过来，结果完全不一样 —— 这是本单元要在断言里钉死的事。

`fetch_and_clean(url)` 读 `data/pageN.html`（不联网，fixture 模拟），
返回 `{url, title, text, length, raw_length}`。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent / "data"

# URL → fixture 文件的映射。让"看起来像 URL"和"实际是本地文件"解耦。
URL_MAP: dict[str, str] = {
    "https://example.com/mcp-intro": "page1.html",
    "https://example.com/rag-tuning": "page2.html",
    "https://example.com/langgraph-state": "page3.html",
    "https://example.com/mcp-error": "page4.html",
    "https://example.com/hybrid-search": "page5.html",
}

# 广告特征：class / id 含这些词的 div / aside / section 都视为广告
AD_KEYWORDS = ("ad", "ads", "promo", "banner-ad", "sidebar-promo", "sponsor")

# 噪音标签：整块删掉（含内容）
NOISE_TAGS = ("script", "style", "nav", "header", "footer", "aside", "noscript")

# 正文容器标签（按优先级取第一个命中的）
ARTICLE_TAGS = ("article", "main")


class FetchError(Exception):
    """抓取失败。用于「URL 不在 fixture 里」「文件读不到」这种结构错误，
    不是「正文为空」（正文为空返回空字符串，不报错）。"""


def _load_fixture(url: str) -> str:
    """根据 URL 找到本地 fixture HTML 文件并读出。"""
    if url not in URL_MAP:
        raise FetchError(f"URL 不在 fixture 里：{url}")
    path = DATA_DIR / URL_MAP[url]
    if not path.exists():
        raise FetchError(f"fixture 文件缺失：{path}")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 三步清洗
# ---------------------------------------------------------------------------
def _strip_noise_blocks(html: str) -> str:
    """第一步：删掉 script / style / nav / header / footer / aside / noscript 整块。

    必须比"剥标签"先做：`<script>...</script>` 里的内容是 JS 代码，
    如果先去标签，JS 代码就混进正文了。
    """
    out = html
    for tag in NOISE_TAGS:
        # DOTALL 让 . 匹配换行；忽略大小写；非贪婪
        out = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}\s*>",
            "",
            out,
            flags=re.DOTALL | re.IGNORECASE,
        )
        # 自闭合 / 无闭合的 nav 也算（极端情况）
    return out


def _strip_ad_blocks(html: str) -> str:
    """删掉广告块：class / id 含广告关键词的 div / aside / section。

    fixture 里 page1 用 `<div class="ad">`、page2 用 `<aside class="sidebar">`
    + `<div class="promo">`、page3 用 `<div class="banner-ad">`。
    这里只看 class / id 是否包含广告关键词（不精确匹配，避免 fixture 一改就失效）。
    """
    pattern = re.compile(
        r"<(?:div|aside|section)\b[^>]*"
        r"(?:class|id)\s*=\s*[\"'][^\"']*(?:"
        + "|".join(re.escape(k) for k in AD_KEYWORDS)
        + r")[^\"']*[\"'][^>]*>.*?</(?:div|aside|section)\s*>",
        re.DOTALL | re.IGNORECASE,
    )
    return pattern.sub("", html)


def _extract_title(html: str) -> str:
    """从 <title>...</title> 里抠出标题。"""
    m = re.search(r"<title\b[^>]*>(.*?)</title\s*>", html, re.DOTALL | re.IGNORECASE)
    if not m:
        return ""
    return _clean_text(m.group(1))


def _pick_article(html: str) -> str:
    """如果有 <article> / <main>，优先取它的内容（正文容器比整页干净）。

    没有就返回整页（已经去过噪音和广告）。
    """
    for tag in ARTICLE_TAGS:
        m = re.search(rf"<{tag}\b[^>]*>(.*?)</{tag}\s*>", html, re.DOTALL | re.IGNORECASE)
        if m:
            return m.group(1)
    return html


def _strip_tags(html: str) -> str:
    """第二步：剥掉剩余 HTML 标签（这时候剩下的都是真正文 + 内联标签）。"""
    # 先把 <br> 换成换行，保留段落结构
    out = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    # 把 </p> </div> </li> 等块级闭合换成换行
    out = re.sub(r"</(?:p|div|li|h[1-6]|tr|section)\s*>", "\n", out, flags=re.IGNORECASE)
    # 剥剩余标签
    out = re.sub(r"<[^>]+>", "", out)
    return out


def _clean_text(text: str) -> str:
    """第三步：整理空白。HTML entity、连续空行、行首尾空格。"""
    # 常见 entity（不全，但够 fixture 用）
    entities = {
        "&nbsp;": " ",
        "&amp;": "&",
        "&lt;": "<",
        "&gt;": ">",
        "&quot;": '"',
        "&#39;": "'",
        "&mdash;": "—",
        "&hellip;": "…",
    }
    for k, v in entities.items():
        text = text.replace(k, v)
    # 行首尾空白
    lines = [ln.strip() for ln in text.split("\n")]
    # 折叠连续空行为一个
    out: list[str] = []
    blank = False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def fetch_and_clean(url: str) -> dict[str, Any]:
    """读 fixture HTML，剥 script/style/nav/广告，返回纯正文。

    返回：
        {url, title, text, length, raw_length, fixture}

    - `text`：清洗后正文（无标签 / 无 JS / 无广告 / 无导航）
    - `length`：清洗后字符数（不含首尾空白）
    - `raw_length`：原始 HTML 字符数（用于对比）
    - `fixture`：实际读的 fixture 文件名（debug 用）

    URL 不在 fixture 里抛 `FetchError`，不在 HTTP 层吃掉。
    """
    raw = _load_fixture(url)
    raw_length = len(raw)

    title = _extract_title(raw)
    # 顺序：先去广告、再去噪音块、再取正文容器、再剥标签、再清空白
    step1 = _strip_ad_blocks(raw)
    step2 = _strip_noise_blocks(step1)
    step3 = _pick_article(step2)
    step4 = _strip_tags(step3)
    text = _clean_text(step4)

    return {
        "url": url,
        "title": title,
        "text": text,
        "length": len(text),
        "raw_length": raw_length,
        "fixture": URL_MAP[url],
    }


def list_fixtures() -> list[dict[str, str]]:
    """给页面用：列出所有可抓的 URL。"""
    return [{"url": u, "fixture": f} for u, f in URL_MAP.items()]
