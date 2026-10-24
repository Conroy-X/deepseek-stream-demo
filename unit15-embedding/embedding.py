"""Unit 15 · Embedding：把文字变成向量，再用余弦相似度衡量"意思有多近"。

两种后端，同一个接口：

    local（默认）  纯 Python 的「字符 bigram 哈希向量」
                   ✅ 零依赖、离线、免费、结果确定
                   ❌ 只捕捉**字面重叠**，捕捉不到语义。
                      "汽车" 和 "轿车" 的相似度几乎为 0，因为它们没有任何共同字。

    api            OpenAI 兼容的 /embeddings 接口（硅基流动 / 通义 / OpenAI 都行）
                   ✅ 真语义。同义词、近义句、跨语言都能对上
                   ❌ 要 key、要钱、有网络延迟

**这个对比本身就是本单元最重要的知识点**：
向量检索能work，靠的不是"向量"这个容器，而是模型在训练中学到的语义。
自己哈希出来的向量只是"稀疏词袋"的另一种存法，它验证的是数学，不是语义。

所以：本单元先用 local 把数学和管线跑通（零成本、可反复），
再用 api 建一个真索引做同样的实验，亲手看到差距。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Protocol

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

# 中日韩统一表意文字 + 扩展 A
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
WORD_RE = re.compile(r"[a-zA-Z0-9]+")


# ---------------------------------------------------------------------------
# 数学：本单元的核心，手写一遍就再也不会忘
# ---------------------------------------------------------------------------
def l2_normalize(vec: list[float]) -> list[float]:
    """L2 归一化：把向量缩放到长度为 1。

    归一化之后，**余弦相似度就等于点积**，少算一次除法。
    这也是为什么几乎所有向量库都要求先归一化再入库。
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec[:]
    return [x / norm for x in vec]


def dot(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"维度不一致：{len(a)} vs {len(b)}")
    return sum(x * y for x, y in zip(a, b))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """余弦相似度 = 点积 / (|a|·|b|)，取值 [-1, 1]。

    为什么用余弦而不是欧氏距离：
        余弦只看**方向**，不看长度。一段话和它自己复制三遍，
        方向完全相同（余弦=1），但欧氏距离很大。
        文本检索关心的是"讲的是不是同一件事"，不是"写得多长"。

    手算示例（二维）：
        a = (1, 0), b = (1, 1)
        a·b = 1
        |a| = 1, |b| = √2 ≈ 1.414
        cos = 1 / 1.414 ≈ 0.707   ← 45 度角
    """
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot(a, b) / (na * nb)


def euclidean_distance(a: list[float], b: list[float]) -> float:
    """欧氏距离，用于和余弦做对比（归一化后两者单调等价）。"""
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
class Embedder(Protocol):
    name: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


# ---------------------------------------------------------------------------
# 后端 A：本地哈希向量（离线、免费）
# ---------------------------------------------------------------------------
def tokenize(text: str) -> list[str]:
    """中文切 bigram，英文切词 —— 不引入 jieba 这种重依赖。

    为什么中文要用 bigram 而不是单字：
        单字会把"上""海"和"海""上"混成一堆，语义信息几乎为零。
        bigram（相邻两字）能天然带住一部分词边界，是低成本分词的标准做法
        （Elasticsearch 的 CJKAnalyzer 就是这么干的）。
    """
    tokens: list[str] = []
    for seg in CJK_RE.findall(text):
        if len(seg) == 1:
            tokens.append(seg)
            continue
        tokens.extend(seg[i : i + 2] for i in range(len(seg) - 1))
    tokens.extend(w.lower() for w in WORD_RE.findall(text))
    return tokens


def _bucket(token: str, dim: int) -> int:
    """把词映射到固定的桶。用 blake2b 而不是内置 hash()：
    内置 hash 对 str 加了随机盐，**换个进程结果就不一样**，
    会导致写进去的向量和查出来的向量对不上（这个坑非常隐蔽）。
    """
    h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big") % dim


class LocalHashingEmbedder:
    """字符 n-gram 哈希向量（hashed bag-of-words）。

    流程：分词 → 词频 → 哈希到 dim 维 → sublinear tf 加权 → L2 归一化。

    它等价于一个稀疏的词袋模型被"压"成了稠密向量，
    所以只能反映**字面重叠**。这是它和真 embedding 的本质差别。
    """

    name = "local-hashing"

    def __init__(self, dim: int = 512):
        self.dim = dim

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        counts = Counter(tokenize(text))
        for tok, cnt in counts.items():
            # sublinear tf：出现 100 次的词不该比 1 次的重要 100 倍
            weight = 1.0 + math.log(cnt)
            vec[_bucket(tok, self.dim)] += weight
        return l2_normalize(vec)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]


# ---------------------------------------------------------------------------
# 后端 B：真语义 embedding（OpenAI 兼容接口）
# ---------------------------------------------------------------------------
class APIEmbedder:
    """任何 OpenAI 兼容 /embeddings 服务都能用。

    国内可选（都需要注册拿 key）：
        硅基流动  https://api.siliconflow.cn/v1   model=BAAI/bge-m3
        阿里百炼  https://dashscope.aliyuncs.com/compatible-mode/v1
                  model=text-embedding-v3
        智谱      https://open.bigmodel.cn/api/paas/v4  model=embedding-3
    """

    name = "api"

    def __init__(self, base_url: str, api_key: str, model: str, dim: int = 1024):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        url = f"{self.base_url}/embeddings"
        payload = {"model": self.model, "input": texts}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        # 按 index 排序，别假设返回顺序和输入顺序一致
        items = sorted(data["data"], key=lambda d: d.get("index", 0))
        vecs = [l2_normalize([float(x) for x in it["embedding"]]) for it in items]
        if vecs:
            self.dim = len(vecs[0])
        return vecs


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
def get_embedder() -> Embedder:
    provider = os.getenv("EMBEDDING_PROVIDER", "local").strip().lower()
    if provider == "api":
        key = os.getenv("EMBEDDING_API_KEY", "").strip()
        base = os.getenv("EMBEDDING_BASE_URL", "").strip()
        model = os.getenv("EMBEDDING_MODEL", "").strip()
        if not key or not base or not model:
            # 配置不全时**降级**而不是崩掉 —— 否则单元一跑就挂，体验很差
            print(
                "[warn] EMBEDDING_PROVIDER=api 但缺少 EMBEDDING_API_KEY/BASE_URL/MODEL，"
                "已回退到本地哈希向量（只能反映字面重叠）"
            )
            return LocalHashingEmbedder()
        return APIEmbedder(base, key, model)
    return LocalHashingEmbedder(dim=int(os.getenv("EMBEDDING_DIM", "512")))


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------
async def rank(
    embedder: Embedder, query: str, docs: list[str], top_k: int = 5
) -> list[dict[str, object]]:
    """最朴素的暴力检索：全量算相似度再排序。

    这叫 **flat / brute-force** 检索，复杂度 O(N·d)。
    N 在几千以内完全够用 —— 所以"要不要上向量库"的第一个判断是数据量，
    不是"别人都在用"。数据大了再换 HNSW/IVFFlat（Unit 16 讲）。
    """
    if not docs:
        return []
    vecs = await embedder.embed([query] + docs)
    qv, dvs = vecs[0], vecs[1:]
    scored = [
        {"index": i, "text": doc, "score": round(cosine_similarity(qv, dv), 4)}
        for i, (doc, dv) in enumerate(zip(docs, dvs))
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
