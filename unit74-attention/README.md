# Unit 74 · Attention：Q / K / V

> 目标：**理解注意力三步——算相关性、归一化成权重、加权求和——以及为什么要除 sqrt(d_k)。**

---

## 一、Q / K / V 是什么

把每个词映射成三个向量：

| 向量 | 全称 | 含义 | 类比 |
| --- | --- | --- | --- |
| Q | Query | 我要找什么 | 搜索词 |
| K | Key | 我有什么 | 文章标题 |
| V | Value | 我实际给什么 | 文章内容 |

## 二、注意力三步

```
scores = Q · K^T / sqrt(d_k)   # 1. 相关性（点积）
weights = softmax(scores)      # 2. 归一化成权重（和为 1）
output = weights · V           # 3. 加权求和
```

## 三、为什么除以 sqrt(d_k)

点积的值随维度 d_k 增大而增大。d_k 很大时，softmax 输入过大 →
梯度饱和（接近 0/1，梯度几乎为 0）→ 训练不动。

除以 `sqrt(d_k)` 把点积的方差拉回 1，梯度健康。这是「缩放点积注意力」
名字里「缩放」的来历。

## 四、因果 mask

生成下一个词时，模型**只能看已经生成的词，不能看未来**。

实现：把未来位置的分数设成 `-inf` → softmax 后权重 = 0。

```
我  爱  北京  天安门
我  ✓  ✗  ✗  ✗      ← 生成「我」时只看自己
爱  ✓  ✓  ✗  ✗
北京 ✓  ✓  ✓  ✗
天安门 ✓ ✓ ✓  ✓
```

## 五、KV Cache（推理优化，计划要求）

生成第 N 个 token 时，前 N-1 个 token 的 K/V **不会变**——重复计算它们纯属浪费。
KV Cache 就是把每层已算过的 K、V 存下来，下一个 token 只算新的那个：

| | 无 KV Cache | 有 KV Cache |
| --- | --- | --- |
| 每步计算量 | O(n²)（重算全部历史） | O(n)（只算新 token） |
| 显存 | 低 | 高（要存 K/V） |

代价是显存，收益是**生成速度数量级提升**。KV Cache 是大模型推理的必选项。

## 五、怎么跑

```bash
cd ~/ai-journey/unit74-attention
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8074
curl "http://127.0.0.1:8074/softmax?values=1,2,3"
curl "http://127.0.0.1:8074/attention"
```

## 六、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 能复述注意力三步
- [ ] 能解释 Q/K/V 各自是什么
- [ ] 能解释「为什么除以 sqrt(d_k)」
- [ ] 能解释因果 mask 的作用

## 七、面试追问

**Q: 多头注意力是什么？**
→ 把 Q/K/V 切成 h 份分别算注意力再拼起来。不同「头」关注不同的关系
（语法关系、指代关系、位置关系……），比单头表达力强。

**Q: 注意力复杂度是多少？**
→ 序列长度 n 时，Q·K^T 是 O(n²)。长序列是瓶颈，所以有
FlashAttention、稀疏注意力、线性注意力等优化。

**Q: softmax 为什么要减最大值？**
→ 数值稳定：exp(1000) 会溢出，减 max 后最大项变成 exp(0)=1，不会溢出。

---

## 八、资料

- [Attention Is All You Need](https://arxiv.org/abs/1706.03762)
- [The Illustrated Transformer](https://jalammar.github.io/illustrated-transformer/)
- [FlashAttention](https://arxiv.org/abs/2205.14135)
