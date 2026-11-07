# Unit 87 · 面试：LLM + RAG（30 题）

> 目标：**30 道高频题全部能用「自己的话 + 要点」讲出来。** 计划要求自问自答 30 题，录音复盘。

---

## 一、题库结构

| 板块 | 题号 | 覆盖 |
| --- | --- | --- |
| Token / 采样 | 1–6 | token、BPE、上下文窗口、temperature、top-p、成本 |
| Embedding / 检索 | 7–12 | embedding、余弦、向量索引、Recall/MRR、混合检索、重排 |
| RAG 流程 | 13–20 | 全流程、切块、阈值、引用、幻觉、长上下文、多跳、查询改写 |
| 评测 | 21–26 | 双侧拒答、忠实度、金标准、LLM-judge、防退化、消融 |
| 工程与安全 | 27–30 | 注入、输出泄露、缓存、流式 |

## 二、打分规则

每题 3 个要点，回答覆盖几个得几分：

```
得分 = 命中要点数 / 要点总数
低于 0.6  → 不及格，要重讲
```

**这是给自己用的**：录音时先说一遍，然后对着要点数一遍命中几个。

## 三、怎么跑

```bash
cd ~/ai-journey/unit87-interview-llm
uv sync
uv run python main.py            # 离线自检（验证题库完整）

uv run uvicorn main:app --port 8087
curl "http://127.0.0.1:8087/bank"                  # 全部 30 题
curl "http://127.0.0.1:8087/bank?topic=rag"        # 只看 RAG 类
curl "http://127.0.0.1:8087/quiz/check"            # 题库自检
```

## 四、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 30 题全部能不看答案讲出至少 2 个要点
- [ ] 弱项（得分 < 0.6）的题按单元回去补（RAG → Unit 20/76/77/78，评测 → Unit 70）
- [ ] 录音复盘一遍，找出「讲不清」的题

## 五、面试追问

**Q: 面试官问「你怎么防止 RAG 胡说」？**
→ 三句话：阈值前置拒答（分数不够不生成）、答案逐字引用（可校验）、
评测集覆盖误收/误拒（可度量）。

**Q: 问「你的 RAG 效果怎么样」？**
→ 必须给数字：Recall@K、答案准确率、误收率；还要说清评测集规模和来源。
没有数字等于没做过。

---

## 六、资料

- [面试题库 interview-questions.md](../docs/interview-questions.md)
- [Unit 20 基础 RAG](../unit20-basic-rag/)
- [Unit 70 评测框架](../unit70-evaluation/)