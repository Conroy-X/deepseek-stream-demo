"""Unit 15 · Embedding 与语义相似度。

本单元只干三件事，但每一件都是后面 RAG 的基石：

    1. 把文本变成向量（两种后端：本地哈希 / 真语义 API）
    2. 用余弦相似度衡量"意思有多近"
    3. 拿它做 Top-K 检索 —— 这就是 RAG 的召回环节

启动：
    uv run uvicorn main:app --reload --port 8000
    打开 http://127.0.0.1:8000/

自检（默认本地后端，零依赖、不联网、不花钱）：
    uv run python main.py
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).with_name(".env"))

import embedding as emb  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")


def _raises(fn) -> bool:
    """自检小工具：确认某个操作会抛异常。"""
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


@asynccontextmanager
async def lifespan(_app: FastAPI):
    e = emb.get_embedder()
    print(f"[embedder] {e.name} · dim={e.dim}", file=sys.stderr)
    yield


app = FastAPI(title="Unit 15 · Embedding", version="0.1.0", lifespan=lifespan)

# 内置语料：用来做检索演示，不用自己准备数据
CORPUS = [
    "FastAPI 是一个现代的 Python Web 框架，支持异步和自动生成接口文档。",
    "Django 是重量级 Python Web 框架，自带 ORM 和后台管理。",
    "PostgreSQL 是开源的关系型数据库，支持 JSONB 和向量检索扩展。",
    "MySQL 是最流行的开源关系数据库，生态成熟、运维资料多。",
    "向量数据库用于存储 embedding，并支持近似最近邻搜索。",
    "余弦相似度通过计算两个向量的夹角来衡量它们的语义接近程度。",
    "Prompt 工程是设计输入提示词，让模型输出更符合预期的技术。",
    "上下文窗口决定了模型一次能处理多少 token 的输入。",
    "今天上海下了小雨，气温比昨天低了三度。",
    "周末打算去图书馆待一下午，顺便把借的书还了。",
    "咖啡因摄入过多会导致心悸和失眠。",
    "跑步是成本最低的有氧运动，每周三次每次三十分钟就有效。",
]


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, max_length=64)


class SimilarityRequest(BaseModel):
    a: str = Field(min_length=1)
    b: str = Field(min_length=1)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    docs: list[str] | None = None
    top_k: int = Field(default=5, ge=1, le=20)


@app.get("/health", summary="当前用的是哪种后端")
def health() -> dict[str, object]:
    e = emb.get_embedder()
    return {
        "ok": True,
        "provider": e.name,
        "dim": e.dim,
        "semantic": e.name == "api",
        "note": "local 后端只反映字面重叠，不代表语义",
        "corpus_size": len(CORPUS),
    }


@app.post("/embed", summary="把文本转成向量")
async def embed(req: EmbedRequest) -> dict[str, object]:
    e = emb.get_embedder()
    vecs = await e.embed(req.texts)
    return {
        "provider": e.name,
        "dim": len(vecs[0]) if vecs else 0,
        # 向量很长，默认只回前 8 维，避免响应过大
        "vectors_preview": [v[:8] for v in vecs],
        "norms": [round(sum(x * x for x in v) ** 0.5, 6) for v in vecs],
        "count": len(vecs),
    }


@app.post("/similarity", summary="两句话的相似度（附手算过程）")
async def similarity(req: SimilarityRequest) -> dict[str, object]:
    e = emb.get_embedder()
    a, b = await e.embed([req.a, req.b])
    cos = emb.cosine_similarity(a, b)
    dist = emb.euclidean_distance(a, b)
    return {
        "provider": e.name,
        "cosine": round(cos, 4),
        "euclidean": round(dist, 4),
        # 手算分解，方便对照公式
        "dot": round(emb.dot(a, b), 6),
        "norm_a": round(sum(x * x for x in a) ** 0.5, 6),
        "norm_b": round(sum(x * x for x in b) ** 0.5, 6),
        "interpretation": _interpret(cos),
    }


def _interpret(cos: float) -> str:
    if cos >= 0.95:
        return "几乎相同"
    if cos >= 0.7:
        return "高度相关"
    if cos >= 0.4:
        return "有点关系"
    if cos >= 0.1:
        return "关系很弱"
    return "基本无关（注意：本地后端下，同义词也会落到这里）"


@app.post("/search", summary="Top-K 检索（RAG 的召回环节）")
async def search(req: SearchRequest) -> dict[str, object]:
    docs = req.docs if req.docs else CORPUS
    e = emb.get_embedder()
    results = await emb.rank(e, req.query, docs, top_k=req.top_k)
    return {"provider": e.name, "query": req.query, "results": results}


@app.get("/", include_in_schema=False)
def index():
    if os.path.exists(STATIC_INDEX):
        return FileResponse(STATIC_INDEX)
    return {"hint": "static/index.html 不存在，看 /docs"}


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import asyncio

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 15 · Embedding 自检（本地后端，不联网）\n")

    # --- 纯数学：先确认公式没错，再谈业务 ---
    print("  —— 余弦相似度的数学性质 ——")
    check("cos(v, v) = 1", abs(emb.cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) - 1.0) < 1e-9)
    check("正交向量 cos = 0", abs(emb.cosine_similarity([1.0, 0.0], [0.0, 1.0])) < 1e-9)
    check("相反向量 cos = -1", abs(emb.cosine_similarity([1.0, 2.0], [-1.0, -2.0]) + 1.0) < 1e-9)
    # 手算：(1,0)·(1,1) = 1，|a|=1，|b|=√2 ⇒ 1/√2 ≈ 0.7071
    check(
        "手算校验 (1,0) vs (1,1) ≈ 0.7071",
        abs(emb.cosine_similarity([1.0, 0.0], [1.0, 1.0]) - 0.70710678) < 1e-6,
        str(emb.cosine_similarity([1.0, 0.0], [1.0, 1.0])),
    )
    n = emb.l2_normalize([3.0, 4.0])
    check("L2 归一化后模长为 1", abs(sum(x * x for x in n) ** 0.5 - 1.0) < 1e-9, str(n))
    check("零向量不除零", emb.l2_normalize([0.0, 0.0]) == [0.0, 0.0])
    check("维度不一致会报错", _raises(lambda: emb.dot([1.0], [1.0, 2.0])))

    # --- 分词 ---
    print("\n  —— 分词 ——")
    toks = emb.tokenize("我爱上海")
    check("中文按 bigram 切分", toks == ["我爱", "爱上", "上海"], str(toks))
    check("英文按词切分并小写", emb.tokenize("FastAPI is Fast") == ["fastapi", "is", "fast"], str(emb.tokenize("FastAPI is Fast")))
    check("bigram 覆盖到句尾的字", "本书" in emb.tokenize("我买了一本书"), str(emb.tokenize("我买了一本书")))

    # --- 向量本身 ---
    print("\n  —— 向量与相似度 ——")
    e = emb.LocalHashingEmbedder(dim=512)

    async def run():
        v1, v2, v3 = await e.embed(["机器学习", "机器学习", "今天天气不错"])
        same = emb.cosine_similarity(v1, v2)
        diff = emb.cosine_similarity(v1, v3)
        return v1, v2, v3, same, diff

    v1, v2, v3, same, diff = asyncio.run(run())
    check("维度固定为 512", len(v1) == 512, str(len(v1)))
    check("相同文本相似度 = 1", abs(same - 1.0) < 1e-9, str(same))
    check("不相关文本相似度明显更低", diff < 0.2, str(round(diff, 4)))
    check("向量已归一化（模长≈1）", abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-9)

    # --- 跨进程确定性 ---
    check(
        "哈希是确定的（跨进程结果一致）",
        emb.LocalHashingEmbedder(dim=512)._embed_one("上海") == emb.LocalHashingEmbedder(dim=512)._embed_one("上海"),
    )

    # --- 检索 ---
    print("\n  —— Top-K 检索 ——")

    async def run_search():
        return await emb.rank(e, "哪个 Python 框架适合做 API", CORPUS, top_k=3)

    hits = asyncio.run(run_search())
    check("返回 3 条结果", len(hits) == 3, str(len(hits)))
    check("按分数降序", all(hits[i]["score"] >= hits[i + 1]["score"] for i in range(len(hits) - 1)), str([h["score"] for h in hits]))
    check(
        "Top-1 属于 Python Web 框架类（FastAPI 或 Django）",
        any(k in str(hits[0]["text"]) for k in ("FastAPI", "Django")),
        str(hits[0]["text"])[:30],
    )

    # --- 诚实地暴露局限 ---
    print("\n  —— 本地后端的局限（这是本单元最重要的一节）——")

    async def limit_test():
        a, b, c = await e.embed(["汽车", "轿车", "汽车"])
        return emb.cosine_similarity(a, b), emb.cosine_similarity(a, c)

    syn, ident = asyncio.run(limit_test())
    check(f"同义词「汽车」vs「轿车」本地相似度很低（{syn:.3f}）", syn < 0.3, str(round(syn, 4)))
    check(f"同一个词「汽车」vs「汽车」相似度为 1（{ident:.3f}）", abs(ident - 1.0) < 1e-9)

    async def subword_test():
        a, b = await e.embed(["API", "FastAPI"])
        return emb.cosine_similarity(a, b)

    sub = asyncio.run(subword_test())
    check(f"「API」vs「FastAPI」本地相似度≈0（{sub:.3f}）：分词粒度不同就连不上", sub < 0.01, str(round(sub, 4)))
    print("        ↑ 结论：本地哈希向量只认字面重叠，不认语义，也不做子词切分。")
    print("          连 FastAPI 和 API 都联系不起来 —— 因为它把 fastapi 当成一个整词。")
    print("          真语义向量靠模型学到的表示，同义词、子词、跨语言都能对上。")
    print("          这正是 RAG 里 embedding 模型选型为什么是头等大事。")

    # --- 边界 ---
    print("\n  —— 边界情况 ——")

    async def edge():
        empty = await e.embed([""])
        return emb.cosine_similarity(empty[0], v1), await emb.rank(e, "test", [], 3)

    empty_sim, empty_rank = asyncio.run(edge())
    check("空文本不崩，相似度为 0", empty_sim == 0.0, str(empty_sim))
    check("空语料返回空结果", empty_rank == [])

    # --- HTTP 层 ---
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    check("GET /health 返回后端信息", r.status_code == 200 and r.json()["provider"] == "local-hashing", str(r.status_code))

    r = client.post("/similarity", json={"a": "向量数据库", "b": "向量数据库"})
    check("POST /similarity 相同文本 cos≈1", abs(r.json()["cosine"] - 1.0) < 1e-6, str(r.json()["cosine"]))

    r = client.post("/search", json={"query": "怎么存向量", "top_k": 2})
    check("POST /search 返回 2 条", len(r.json()["results"]) == 2)

    r = client.post("/embed", json={"texts": ["abc"]})
    check("POST /embed 返回归一化向量", abs(r.json()["norms"][0] - 1.0) < 1e-6, str(r.json()["norms"]))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
