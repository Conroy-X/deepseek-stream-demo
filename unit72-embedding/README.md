# Unit 72 · Embedding 原理：向量就是语义坐标

> 目标：**理解 embedding 的本质——把离散的词映射成连续向量，语义相近则向量相近。**

---

## 一、embedding 是什么

模型不能直接算「词」的相似度，它把每个词映射成一个**向量**（一列数字），
然后用**余弦相似度**衡量两个向量的接近程度。

```
king   → [-1.0, 1.0, 0.0]
queen  → [ 1.0, 1.0, 0.0]
cat    → [ 0.0, -0.5, -0.5]
```

- 语义相近 → 向量方向相近 → 余弦相似度高
- 语义无关 → 向量方向差异大 → 余弦相似度低

## 二、三条核心性质（本单元都有演示）

### 1. 语义相近 → 向量相近

`nearest(king) → queen`；`nearest(cat) → dog`。

### 2. 向量可做算术：类比

```
king - man + woman ≈ queen
```

「king 减去 man」（去掉男性特征）「加上 woman」（加上女性特征）≈ queen。
这说明**向量的方向编码了语义特征**（性别、皇权、年龄……）。

### 3. 方向比长度重要

把向量拉长 10 倍，余弦相似度不变。所以相似度用余弦（看方向），
不用欧氏距离（会被长度干扰）。

## 三、和 Unit 15/19 的关系

| 单元 | 学什么 |
| --- | --- |
| Unit 15 | embedding 怎么**用**（余弦 + Top-K） |
| Unit 19 | 向量检索怎么**评测**（Recall@K） |
| **Unit 72** | embedding 本身是**什么**（语义空间 + 类比） |

三个单元连起来就是完整认知：原理 → 使用 → 评测。

## 四、怎么跑

```bash
cd ~/ai-journey/unit72-embedding
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8072
curl "http://127.0.0.1:8072/analogy?a=king&b=man&c=woman"
curl "http://127.0.0.1:8072/nearest?word=cat"
```

## 五、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 能解释「向量是语义坐标」
- [ ] 能说余弦相似度为什么比欧氏距离合适
- [ ] 能复述 king − man + woman ≈ queen 的含义
- [ ] 能说 Unit 15/19/72 三者的关系

## 六、面试追问

**Q: 向量的维度是什么意思？**
→ 维度是「语义特征的个数」的上限，具体某个维度没有人工可解释的含义
（真实 embedding 是模型学出来的，不是人设计的）。

**Q: 为什么用余弦不用欧氏距离？**
→ 余弦看方向，不受向量长度影响；embedding 的长度通常没有语义。

**Q: 真实 embedding 怎么来的？**
→ 模型训练出来的：让「上下文相近的词」向量相近（如 word2vec 的
「用周围词预测中心词」）。本单元的手工向量只是为了把性质讲清楚。

**Q: embedding 和 token 什么关系？**
→ token 是离散的（Unit 71），embedding 把每个 token 映射成连续向量，
模型真正计算的是向量。

---

## 七、资料

- [word2vec 论文](https://arxiv.org/abs/1301.3781)
- [余弦相似度](https://en.wikipedia.org/wiki/Cosine_similarity)
- [Embeddings（OpenAI 文档）](https://platform.openai.com/docs/guides/embeddings)
