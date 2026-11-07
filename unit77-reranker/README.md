# Unit 77 · Reranker：检索后重排

> 目标：**理解「先快后准」两段式检索——初检快，重排准。**

---

## 一、为什么需要重排

| 阶段 | 模型 | 特点 |
| --- | --- | --- |
| 初检 | BM25 / 向量（双编码器） | 快，但排序粗糙 |
| **重排** | 交叉编码器 | 慢，但排序准 |

流程：初检 Top-50 → reranker 精排 → 取 Top-10 进 prompt。
**只对 Top-K 用重排**，所以慢一点也能接受。

## 二、双编码器 vs 交叉编码器

| | 双编码器 | 交叉编码器 |
| --- | --- | --- |
| 做法 | query 和 doc 各自编码再算相似度 | query+doc 拼一起输入模型打分 |
| 速度 | 快（可预计算 doc 向量） | 慢（每对都要算） |
| 精度 | 较低 | 高（能捕捉词序、交互） |

## 三、怎么跑

```bash
cd ~/ai-journey/unit77-reranker
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8077
curl -G --data-urlencode "q=苹果手机 USB-C 充电" "http://127.0.0.1:8077/search"
```

## 四、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 能说两段式检索为什么「先快后准」
- [ ] 能说双编码器和交叉编码器的区别
- [ ] 能说 reranker 只对 Top-K 用

## 五、面试追问

**Q: 重排会引入延迟，怎么权衡？**
→ 只对 Top-K（如 50）重排；用更小的重排模型；缓存常见 query 的结果。
本质是「用一点延迟换明显更好的排序」。

**Q: 重排怎么评测？**
→ 对比重排前后的 Recall@K / MRR（Unit 19 的指标），看有没有提升。

**Q: 混合检索（Unit 76）和重排（Unit 77）什么关系？**
→ 正交：混合检索是「初检阶段」融合字面+语义；重排是「后处理阶段」精排。
两个可以同时用：混合初检 Top-50 → 重排 Top-10。

---

## 六、资料

- [Cross-Encoders（Sentence-Transformers）](https://www.sbert.net/examples/applications/cross-encoder/README.html)
- [Rerank 模型（Cohere）](https://docs.cohere.com/docs/rerank-overview)
