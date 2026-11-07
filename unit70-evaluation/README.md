# Unit 70 · Evaluation：离线评测

> 目标：**评测三要素——金标准、离线可复现、指标可解释。** 没有金标准的评测不叫评测。

---

## 一、评测什么

| 对象 | 指标 | 本单元实现 |
| --- | --- | --- |
| RAG 答案质量 | 关键词覆盖率 / 幻觉清零 / 引用 | `score_keywords` / `judge_answer` |
| RAG 拒答 | 双侧混淆矩阵（误收 + 误拒） | `evaluate_refusal` |
| Agent | 成功率 / 工具正确性 / 步数冗余 | `AgentEval` |

## 二、拒答为什么必须看「双侧」

只看「该拒的拒了」不够，还要看「不该拒的误拒了」：

| 指标 | 含义 | 危害 |
| --- | --- | --- |
| **误收率** false accept | 该拒的答了 | 最危险：模型编答案 |
| **误拒率** false refuse | 不该拒的拒了 | 用户体验差：会答的题也说不会 |

Unit 19 阈值标定就是在调这两个率的平衡点。

## 三、LLM-as-judge 为什么要 rubric

用模型打分（judge），必须给评分标准（rubric），否则：

- 同一个答案，judge 两次给分可能不同（不可复现）
- 不同 judge 模型给分不可比

本单元用**确定性规则模拟 judge**，把评分逻辑先跑通——真实 judge 上线前，
它的评分逻辑也要先能被规则验证（meta-eval）。

## 四、怎么跑

```bash
cd ~/ai-journey/unit70-evaluation
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8070
curl "http://127.0.0.1:8070/eval/rag"
curl "http://127.0.0.1:8070/eval/agent"
curl -G --data-urlencode "pred=退款原路退回3-5个工作日[1]" "http://127.0.0.1:8070/eval/judge"
```

## 五、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 能算拒答混淆矩阵并解释误收/误拒
- [ ] 能说 Agent 评测的三个指标
- [ ] 能解释「judge 为什么要 rubric」
- [ ] 能说「评测三要素」是什么

## 六、面试追问

**Q: 没有金标准怎么做评测？**
→ 小数据集先人工标金标准（100-200 条）；或用 LLM 生成候选 + 人工抽检。
金标准是评测的地基，绕不过。

**Q: LLM-as-judge 可靠吗？**
→ 有一定偏差（位置偏差、长度偏差、自我偏好）。要 meta-eval：
拿人工评分做基准，看 judge 和人工的一致性；关键指标要抽检。

**Q: 线上怎么持续评测？**
→ 离线评测集（每次改 prompt/模型跑一遍）+ 线上采样人工抽检 + 用户反馈。
离线评测保证「不退化」，线上抽检发现「离线没覆盖的问题」。

**Q: Agent 评测最难在哪？**
→ 成功标准难定义（什么叫「完成任务」）、环境不可复现、
长任务中间态难判定。所以本单元从工具正确性和步数入手，逐步逼近。

---

## 七、资料

- [RAGAS（RAG 评测框架）](https://docs.ragas.io/en/stable/)
- [OpenAI Evals](https://github.com/openai/evals)
- [LLM-as-judge 论文](https://arxiv.org/abs/2306.05685)
- [混淆矩阵](https://en.wikipedia.org/wiki/Confusion_matrix)
