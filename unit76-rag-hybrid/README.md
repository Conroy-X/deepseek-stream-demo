# Unit 76 · RAG 优化：混合检索

> 目标：**理解单一检索的盲区，学会用 RRF 融合 BM25（字面）和向量（语义）两条路。**

---

## 一、单一检索的盲区

| 检索 | 强 | 弱 |
| --- | --- | --- |
| BM25（字面） | 精确关键词：型号、法条号 | 换种说法就找不到 |
| 向量（语义） | 同义改写、模糊语义 | 精确关键词可能漏 |

## 二、混合检索 + RRF 融合

```
query ─┬─ BM25 排序 ──────┐
       │                  ├─ RRF 融合 → 最终排序
       └─ 向量排序 ───────┘
```

**RRF（Reciprocal Rank Fusion）**：`score(doc) = Σ 1/(k + rank)`

- 不依赖绝对分数（BM25 和余弦的分数不可直接比）
- 只要排名，更稳
- k 通常取 60

## 三、怎么跑

```bash
cd ~/ai-journey/unit76-rag-hybrid
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8076
curl -G --data-urlencode "q=USB-C 充电" "http://127.0.0.1:8076/search"
curl -G --data-urlencode "q=拍照" "http://127.0.0.1:8076/search"
```

## 四、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 能说两种检索各自的盲区
- [ ] 能解释 RRF 为什么比加权分数稳
- [ ] 能跑通「精确词」和「语义词」两类查询

## 五、面试追问

**Q: 为什么不直接加权两个分数？**
→ BM25 和余弦分数分布不同（量纲不同），直接加权要归一化，麻烦且脆；
RRF 只用排名，天然可比。

**Q: 混合检索怎么评测？**
→ 用 Unit 70 的评测集：精确词查询验证 BM25 贡献，改写查询验证向量贡献，
两者都要过。

**Q: 混合检索的成本？**
→ 两条索引 + 两次查询 + 融合，检索延迟和存储都更高。小规模无所谓，
大规模要考虑用单一向量 + 关键词增强（如把关键词塞进向量输入）。

---

## 六、资料

- [RRF 论文](https://plg.uwaterloo.ca/~gvcormac/cormack2009amazon.pdf)
- [BM25 维基](https://en.wikipedia.org/wiki/Okapi_BM25)
