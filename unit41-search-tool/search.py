"""Unit 41 · 搜索工具 —— 离线 fixture，不联网、不要 API key。

教学核心：**搜索工具的 schema 该怎么写**。

很多人把搜索工具写成 `search(query: str) -> str`，让模型自己解析一坨文本 —— 错的。
正确的写法：

1. `query` 是 string，**不要**写成 `enum` 或 `pattern`（你不知道模型会问什么）。
2. `max_results` 给默认值（5），上限别太高（10 够了），让模型能省 token 就省。
3. 返回是**结构化列表**（`[{title,url,snippet}]`），不是字符串。
   模型读字符串得自己切片，结构化列表它一眼就看到「3 条结果」。

本模块的 fixture 是 5 条结果，覆盖 3 个主题：
- MCP 协议（2 条）
- RAG 优化（2 条）
- LangGraph（1 条）

匹配规则：把 query 拆词，命中 title / snippet / topic 任意一个就算命中。
不命中就返回空列表 `[]` —— **不抛异常**，让模型自己决定换词还是承认查不到。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURE_PATH = Path(__file__).parent / "data" / "fixtures.json"

DEFAULT_MAX_RESULTS = 5
HARD_MAX_RESULTS = 10  # schema 里的 maximum，挡住模型瞎填 100

# 5 条 fixture，3 个主题。所有字段都是字符串 —— 别让模型去猜枚举。
FIXTURES: list[dict[str, Any]] = [
    {
        "id": "r1",
        "topic": "MCP 协议",
        "title": "Model Context Protocol 入门：一次握手能干什么",
        "url": "https://example.com/mcp-intro",
        "snippet": "MCP 用 JSON-RPC 2.0 在客户端和工具服务器之间建一条通道，"
        "先 initialize 再 notifications/initialized，之后 tools/list 才有效。",
    },
    {
        "id": "r2",
        "topic": "MCP 协议",
        "title": "为什么 MCP 工具失败用 isError 而不是 error 帧",
        "url": "https://example.com/mcp-error",
        "snippet": "工具内部抛异常要包成 result.isError=true，"
        "如果包成 error 帧，客户端会按协议异常处理，模型看不到原因。",
    },
    {
        "id": "r3",
        "topic": "RAG 优化",
        "title": "RAG 检索质量调优：chunk 大小、overlap、reranker 的实测对比",
        "url": "https://example.com/rag-tuning",
        "snippet": "在 200 条文档的语料上测了 chunk 256/512/1024 三档，"
        "reranker 把 top-3 命中率从 0.62 拉到 0.81，但延迟翻倍。",
    },
    {
        "id": "r4",
        "topic": "RAG 优化",
        "title": "RAG 混合检索：BM25 + 向量不是简单加权",
        "url": "https://example.com/hybrid-search",
        "snippet": "RAG 检索里 BM25 抓关键词强、向量抓语义强，"
        "融合要按 RRF 而不是加权打分，加权会把两边的尺度错位。",
    },
    {
        "id": "r5",
        "topic": "LangGraph",
        "title": "LangGraph StateGraph：把节点串成图而不是链",
        "url": "https://example.com/langgraph-state",
        "snippet": "StateGraph 用 TypedDict 定义状态，每个节点返回增量，"
        "框架自动 merge，比 LangChain 的链式 Runnable 更适合多分支。",
    },
]


def _load_fixtures() -> list[dict[str, Any]]:
    """优先读 data/fixtures.json；缺失时退化到内置 FIXTURES。

    这样 fixture 即可当数据文件改，又能保证 main.py 自检不依赖外部文件存在。
    """
    if FIXTURE_PATH.exists():
        try:
            data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return FIXTURES


def _tokenize(query: str) -> list[str]:
    """把 query 拆成关键词。

    真搜索会用分词器；离线 fixture 里我们走简单规则：
    - 中英文字符都按空格 / 标点切
    - 保留长度 >= 2 的 token（"RAG" "MCP" "LangGraph" 都满足）
    - 单字 token 也收（"雨" "图" 这种），但权重低（命中单字不一定是真命中）
    """
    # 按 non-word 字符切，但保留中文字符
    tokens: list[str] = []
    cur = ""
    for ch in query:
        if ch.isalnum() or "\u4e00" <= ch <= "\u9fff":
            cur += ch
        else:
            if cur:
                tokens.append(cur)
                cur = ""
    if cur:
        tokens.append(cur)
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _match(result: dict[str, Any], tokens: list[str]) -> bool:
    """任意一个 token 命中 title/snippet/topic 即算命中。

    长度 >= 2 的 token 必须命中才算（"MCP" "RAG" "LangGraph"）；
    单字 token 也参与，但只有当没更长 token 时才用。
    """
    if not tokens:
        return False
    hay = f"{result.get('title','')} {result.get('snippet','')} {result.get('topic','')}".lower()
    long_tokens = [t for t in tokens if len(t) >= 2]
    short_tokens = [t for t in tokens if len(t) == 1]
    # 长词命中即命中
    for t in long_tokens:
        if t.lower() in hay:
            return True
    # 没长词时才看短词
    if not long_tokens:
        for t in short_tokens:
            if t.lower() in hay:
                return True
    return False


def search(query: str, max_results: int = DEFAULT_MAX_RESULTS) -> list[dict[str, str]]:
    """按关键词在 fixture 里找结果。

    返回 `[{title, url, snippet}]`（**剥掉 topic / id**，工具对外只暴露这三字段）。
    不命中返回 `[]`，不抛异常。
    `max_results` 上限是 HARD_MAX_RESULTS，超出按上限截断。
    """
    if not isinstance(query, str) or not query.strip():
        return []
    # 钳制 max_results：负数 / 0 / 超大值都给兜底
    if not isinstance(max_results, int) or max_results < 0:
        max_results = 0
    elif max_results > HARD_MAX_RESULTS:
        max_results = HARD_MAX_RESULTS

    fixtures = _load_fixtures()
    tokens = _tokenize(query)
    hits = [r for r in fixtures if _match(r, tokens)]
    return [{"title": r["title"], "url": r["url"], "snippet": r["snippet"]} for r in hits[:max_results]]


# ---------------------------------------------------------------------------
# schema —— 这一段是本单元真正要讲的东西
# ---------------------------------------------------------------------------
SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "搜索关键词。中英文都行，多个词用空格分隔。",
            # 注意：**没有** enum / pattern —— 你不知道模型会问什么
        },
        "max_results": {
            "type": "integer",
            "description": "最多返回多少条结果。默认 5，最大 10。",
            "default": DEFAULT_MAX_RESULTS,
            "minimum": 0,
            "maximum": HARD_MAX_RESULTS,
        },
    },
    "required": ["query"],
    "additionalProperties": False,  # 关掉，模型别瞎塞字段
}


def search_tool_spec() -> dict[str, Any]:
    """OpenAI function-calling 格式的工具声明。"""
    return {
        "type": "function",
        "function": {
            "name": "search",
            "description": "在本地知识库里搜文档。返回 [{title,url,snippet}]。不命中返回空列表。",
            "parameters": SEARCH_SCHEMA,
        },
    }


def list_fixtures() -> list[dict[str, Any]]:
    """给页面 / health 用：把全部 fixture 列出来，让人看到「这工具背后到底有什么」。"""
    return [
        {"id": r["id"], "topic": r["topic"], "title": r["title"], "url": r["url"]}
        for r in _load_fixtures()
    ]
