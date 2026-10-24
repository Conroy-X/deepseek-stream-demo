# Unit 15 · Embedding 与语义相似度

RAG 的召回环节。把句子变成向量，用余弦相似度衡量"意思有多近"，再做 Top-K 检索。

## 快速开始

```bash
uv sync
cp .env.example .env       # 默认用本地后端，不需要任何 key
uv run uvicorn main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/>：左边调相似度，右边看 Top-K 检索和向量长什么样。

## 自检

```bash
uv run python main.py
```

27 项，全离线：余弦相似度的数学性质（含手算校验）、L2 归一化、分词、
跨进程哈希确定性、Top-K 排序、边界情况，以及**两条"局限实证"断言**。

## 两种后端

| provider | 语义能力 | 依赖 | 适用 |
| --- | --- | --- | --- |
| `local`（默认） | ❌ 只反映字面重叠 | 无 | 跑通数学与管线、零成本反复实验 |
| `api` | ✅ 真语义 | 需要 key | 真实 RAG |

切换到真语义只要三行配置（任何 OpenAI 兼容 `/embeddings` 都行）：

```bash
EMBEDDING_PROVIDER=api
EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_API_KEY=sk-xxx
```

> ⚠️ **DeepSeek 没有 embedding 接口**，所以 `DEEPSEEK_API_KEY` 在这里用不上。
> 国内可选用硅基流动（bge-m3）、阿里百炼（text-embedding-v3）、智谱（embedding-3）。

## 实测到的局限（这是本单元的重点）

| 对比 | local 后端 | 真语义（预期） |
| --- | --- | --- |
| 「汽车」vs「轿车」 | **0.000** | 0.8+ |
| 「API」vs「FastAPI」 | **0.000** | 0.4+ |
| 「汽车」vs「汽车」 | 1.000 | 1.000 |

不是它们无关，而是本地哈希向量只做精确 token 匹配：
「汽车」和「轿车」没有一个共同的 bigram；`FastAPI` 被当成整词 `fastapi`，和 `api` 无关。

**结论：向量能不能表达语义，取决于用什么模型算，不取决于"用了向量"。**

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 当前后端、维度、是否具备语义能力 |
| POST | `/embed` | 文本 → 向量（返回前 8 维 + 各向量模长） |
| POST | `/similarity` | 两句话的余弦相似度，附点积/模长/欧氏距离（可对照公式手算） |
| POST | `/search` | Top-K 检索，默认从内置 12 条语料里找 |

## 核心代码

```python
def cosine_similarity(a, b):
    na = sqrt(sum(x*x for x in a))
    nb = sqrt(sum(x*x for x in b))
    return sum(x*y for x, y in zip(a, b)) / (na * nb)
```

就这一行公式。真正难的是"用什么算 a 和 b"。

## 验收清单

- [ ] `uv run python main.py` → 27 通过 / 0 失败
- [ ] `/similarity` 里两句相同的话 cosine = 1
- [ ] 手算校验：`(1,0)` vs `(1,1)` 返回 ≈ 0.7071
- [ ] `/embed` 返回的向量模长为 1（已归一化）
- [ ] `/search` 搜「哪个 Python 框架适合写 API」能召回 FastAPI/Django 那条
- [ ] 亲眼看到「汽车」和「轿车」相似度为 0，能解释原因
- [ ] （可选）配上真 embedding key，看同义词分数变成 0.8+

## 下一单元

Unit 16 把这套向量搬进 PostgreSQL（pgvector），建 HNSW 索引，
把暴力检索换成近似最近邻 —— 那时才真正需要"向量数据库"。
