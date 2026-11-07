# Unit 73 · Transformer 架构

> 目标：**理解 Transformer 为什么能并行、以及 block 的四个零件（位置编码 / 注意力 / 残差 / FFN）。**

---

## 一、为什么 Transformer 取代了 RNN

| 架构 | 处理方式 | 问题 |
| --- | --- | --- |
| RNN | 一个词一个词串行 | 慢，长序列梯度消失 |
| **Transformer** | 整个序列并行 | 快，但需要显式的位置信息 |

并行带来的代价：**注意力本身不关心顺序**，所以要显式加位置编码。

## 二、Block 的四个零件

```
输入 embedding + 位置编码
        │
        ├─ 自注意力（Unit 74 展开：Q/K/V）
        │     + 残差连接（x + sublayer(x)）
        │     + LayerNorm
        ├─ FFN（两层 MLP + ReLU）
        │     + 残差连接
        │     + LayerNorm
        └─ 输出
```

重复 N 次（大模型 N = 几十到上百层）。

| 零件 | 作用 |
| --- | --- |
| 位置编码 | 告诉模型词的顺序（正弦函数，本单元演示） |
| 自注意力 | 让每个词看到其他词（Unit 74） |
| 残差连接 | 梯度直达底层，深层网络才训得动 |
| LayerNorm | 数值稳定（均值 0 方差 1） |

## 三、怎么跑

```bash
cd ~/ai-journey/unit73-transformer
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8073
curl "http://127.0.0.1:8073/positional?seq_len=4"
curl "http://127.0.0.1:8073/layernorm?values=1,3,5,7"
```

## 四、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 能说 Transformer 为什么需要位置编码
- [ ] 能说残差连接和 LayerNorm 各自的作用
- [ ] 能画出一个 block 的结构
- [ ] 知道注意力细节在 Unit 74

## 五、面试追问

**Q: 位置编码为什么用正弦？**
→ 任意位置的编码可由其他位置线性表示（有规律可外推）；
不需要学习参数（原始 Transformer 的做法）。后来很多模型也改用可学习位置编码
或 RoPE（旋转位置编码），效果更好。

**Q: 残差连接为什么有效？**
→ 没有残差时梯度要穿过很多层，会消失/爆炸；残差给了梯度一条「直达通道」，
深层网络因此能训练。

**Q: LayerNorm 和 BatchNorm 的区别？**
→ LayerNorm 对一个样本的所有维度归一化（适合 NLP 变长序列）；
BatchNorm 对一个 batch 的同一维度归一化（适合 CV 固定形状）。

---

## 六、资料

- [Attention Is All You Need](https://arxiv.org/abs/1706.03762)
- [The Illustrated Transformer](https://jalammar.github.io/illustrated-transformer/)
- [LayerNorm 论文](https://arxiv.org/abs/1607.06450)
