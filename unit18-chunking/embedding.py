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

═══════════════════════════════════════════════════════════════════
给 Unit 18 的一句提醒（实测结论，别忘）：
  所有作用在相似度上的阈值（语义切分 threshold、拒答 τ）
  都是刻在这个 embedder 的分数分布上的。换 backend = 分布变了 = 全部重标。
  本地哈希：相邻句中位数 0.096 → 阈值 0.05
  真语义模型：相邻句中位数 0.4+ → 同一阈值会把全文切成 1 块（永不触发）
═══════════════════════════════════════════════════════════════════
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

# 只加载与本文件同目录的 .env（不往上找），避免误读别的单元配置
load_dotenv(Path(__file__).with_name(".env"))

# 中日韩统一表意文字 + 扩展 A
# 基本区（\u4e00-\u9fff）覆盖常用汉字；扩展 A（\u3400-\u4dbf）是生僻字，
# 少数字库文档会用到，漏了会把它们当"非中文"丢掉
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
# 英文/数字串：连续字母数字作为一个词（"HNSW" / "3-5" 拆成 3 和 5，
# 这里不为连字符做特殊处理 —— 词袋模型对 "3-5" 的拆法无所谓）
WORD_RE = re.compile(r"[a-zA-Z0-9]+")


# ---------------------------------------------------------------------------
# 数学：本单元的核心，手写一遍就再也不会忘
# ---------------------------------------------------------------------------
def l2_normalize(vec: list[float]) -> list[float]:
    """L2 归一化：把向量缩放到长度为 1。

    归一化之后，**余弦相似度就等于点积**，少算一次除法。
    这也是为什么几乎所有向量库都要求先归一化再入库。

    代价与陷阱：
      · 零向量（norm == 0）不能除 —— 直接返回副本，否则 ZeroDivisionError；
        零向量在检索里意味着"这段文本没有任何可识别 token"（比如纯符号）
      · 归一化后"长度"这个维度被抹掉了，这也正是我们要的：
        相似度只该看方向，不该被文本长度影响
    """
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec[:]  # 返回副本而非原对象，避免调用方误改共享列表
    return [x / norm for x in vec]


def dot(a: list[float], b: list[float]) -> float:
    """逐位相乘再求和 —— 内积/点积，同一个东西。

    维度检查放在这里而不是靠 zip 静默截断：
    zip 遇到长度不一致会"默默按短的算完"，返回一个看起来合法但错误的分数。
    这类静默错误在检索里表现为"分数总是偏低"，极难排查。
    """
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

    和 pgvector 的换算（Unit 16 的 score 就来自这里）：
        <=> 余弦距离 = 1 - cos          范围 [0, 2]
        score = 1 - 距离 = cos          范围 [-1, 1]
    所以 score 出现负数是正常的：夹角超过 90° 就会为负。
    """
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0 or nb == 0:
        # 任一为零向量：夹角无定义，约定返回 0（"无关"），
        # 比抛异常好 —— 检索要能容忍脏数据继续跑
        return 0.0
    return dot(a, b) / (na * nb)


def euclidean_distance(a: list[float], b: list[float]) -> float:
    """欧氏距离，用于和余弦做对比（归一化后两者单调等价）。

    单调等价的意思是：都归一化到单位长度后，
    ||a-b||² = 2 - 2·cos，所以按欧氏距离排序和按余弦排序结果完全相同。
    区别只在"距离"和"相似度"的方向（越小越像 vs 越大越像）。
    """
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
class Embedder(Protocol):
    """结构性子类型（鸭子类型的类型化写法）。

    不定义基类，只约定"有 name / dim / embed"三样东西就是 embedder。
    好处：LocalHashingEmbedder 和 APIEmbedder 互不相识，都能直接替换。
    """

    name: str
    dim: int

    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    # 为什么是 async：API 后端要发网络请求，批量 embed 必须能并发/非阻塞。
    # 本地实现虽然是同步计算，也统一成 async 接口 —— 上层代码只写一份。


# ---------------------------------------------------------------------------
# 后端 A：本地哈希向量（离线、免费）
# ---------------------------------------------------------------------------
def tokenize(text: str) -> list[str]:
    """中文切 bigram，英文切词 —— 不引入 jieba 这种重依赖。

    为什么中文要用 bigram 而不是单字：
        单字会把"上""海"和"海""上"混成一堆，语义信息几乎为零。
        bigram（相邻两字）能天然带住一部分词边界，是低成本分词的标准做法
        （Elasticsearch 的 CJKAnalyzer 就是这么干的）。

    具体规则：
      中文连续段 → 相邻两字滑窗："上海天气" → 上海/海天/天气
      单字段（只有一个汉字）→ 原样保留，否则整个字会消失
      英文/数字 → 小写后按整词："HNSW" → hnsw

    已知弱点（正是它只能做字面匹配的根源）：
      "汽车" 和 "轿车" 没有共同 bigram → 相似度 0；
      "退货" 和"退款"共享"退"字但不共享 bigram → 相似度也是 0。
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

    这就是"哈希向量必须用稳定哈希"的全部理由：
      · 建索引的进程算出的 vec 和查询进程算出的 vec 必须一致
      · Python 的 hash() 受 PYTHONHASHSEED 影响，每次启动都变
      · blake2b 是密码学哈希，同输入永远同输出，跨机器跨进程都一样

    哈希碰撞：两个不同的词落到同一桶 → 分数叠加（噪声）。
    dim=512 时，中文 bigram 几千个 token 必然碰撞，这是"稠密化"的代价；
    好在碰撞是随机对称的，对排序影响有限，且不影响"完全相同文本相似度=1"。
    """
    h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    # 8 字节大端整数 % dim：digest_size 固定 8 → 结果稳定可复现
    return int.from_bytes(h, "big") % dim


class LocalHashingEmbedder:
    """字符 n-gram 哈希向量（hashed bag-of-words）。

    流程：分词 → 词频 → 哈希到 dim 维 → sublinear tf 加权 → L2 归一化。

    它等价于一个稀疏的词袋模型被"压"成了稠密向量，
    所以只能反映**字面重叠**。这是它和真 embedding 的本质差别。

    ══ 为什么 Unit 18/19 都用它做默认 ══
      零成本、确定性、离线 —— 才能反复跑评测、控变量做对比实验。
      代价是：语义改写题的召回会系统性偏低（只认字面）。
      等你配了 API key，同一个评测脚本换个 provider 就能看到真实差距。
    """

    name = "local-hashing"

    def __init__(self, dim: int = 512):
        self.dim = dim
        # 注意：dim 一旦确定就不能改。改写历史的向量库 = 全量重建索引，
        # 这和 Unit 16 里 "vector(512) 定维、换模型必须重建表" 是同一件事

    def _embed_one(self, text: str) -> list[float]:
        """单条文本 → 512 维稠密向量（下划线前缀：内部方法，批量走 embed）。"""
        vec = [0.0] * self.dim
        counts = Counter(tokenize(text))
        for tok, cnt in counts.items():
            # sublinear tf：出现 100 次的词不该比 1 次的重要 100 倍
            # log 压缩是 IR 里的经典做法（词频饱和），
            # 防止"的""是"这类高频词靠数量主导向量方向
            weight = 1.0 + math.log(cnt)  # cnt=1 → 1.0，cnt=e → 2.0
            vec[_bucket(tok, self.dim)] += weight
        # 归一化放在最后：顺序不能反（先归一再加权会把权重抹平）
        return l2_normalize(vec)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        # 本地计算没有 I/O，写成 async 只为对齐 Embedder 协议
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

    和 LocalHashingEmbedder 的契约完全一致 —— 上层换后端只改 .env：
        EMBEDDING_PROVIDER=api
        EMBEDDING_BASE_URL=...
        EMBEDDING_API_KEY=...
        EMBEDDING_MODEL=...
    """

    name = "api"

    def __init__(self, base_url: str, api_key: str, model: str, dim: int = 1024):
        self.base_url = base_url.rstrip("/")  # 容忍用户把 /v1/ 带尾斜杠
        self.api_key = api_key
        self.model = model
        self.dim = dim  # 占位值，首次请求后用真实返回的维度覆盖

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            # 空列表直接返回，不然某些服务会返回 400（input 为空）
            return []
        url = f"{self.base_url}/embeddings"
        payload = {"model": self.model, "input": texts}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        # 每次调用新建 client：简单可靠，代价是丢了连接复用。
        # 高频场景应改成共享 client（httpx.AsyncClient 可在 lifespan 里建一个）
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            # raise_for_status：4xx/5xx 直接抛，别让错误响应被当成正常数据往下走
            resp.raise_for_status()
            data = resp.json()
        # 按 index 排序，别假设返回顺序和输入顺序一致
        # （OpenAI 协议里 index 就是这个用途；不排序会导致向量和文本错位，
        #   表现是"检索结果莫名其妙"，且极难发现）
        items = sorted(data["data"], key=lambda d: d.get("index", 0))
        # 服务端返回的向量未必归一化（各家不一），统一在这里归一 ——
        # 保证下游 cosine_similarity / 点积 / pgvector 的行为一致
        vecs = [l2_normalize([float(x) for x in it["embedding"]]) for it in items]
        if vecs:
            # 用真实维度覆盖占位值：不同模型 768/1024/1536 不等
            self.dim = len(vecs[0])
        return vecs


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
def get_embedder() -> Embedder:
    """按 .env 选后端。默认 local —— 没配任何东西也能跑起来。

    降级策略：声明要 api 但配置不全时，打警告 + 回退 local，而不是抛异常。
    理由：这是教学项目，单元"一跑就挂"的体验比"降级但能跑"差得多。
    生产环境则应相反：配置错误必须启动即失败（fail fast），
    否则你会带着"以为在用真模型、其实在用哈希"的错误认知跑一整天实验。
    """
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

    一个实现细节：query 和 docs 一起送进 embed（[query] + docs），
    对 API 后端来说省了一次往返 —— 批量接口一次能处理的就一次处理完。
    """
    if not docs:
        return []
    vecs = await embedder.embed([query] + docs)
    qv, dvs = vecs[0], vecs[1:]
    scored = [
        # 千分位分数直接算完就排序，不做任何截断/阈值：
        # 阈值决策应该在上层（拒答逻辑），检索层保持"只排序不判断"
        {"index": i, "text": doc, "score": round(cosine_similarity(qv, dv), 4)}
        for i, (doc, dv) in enumerate(zip(docs, dvs))
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
