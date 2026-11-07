# Unit 75 · LLM 原理：下一个 token 预测

> 目标：**理解 LLM 的训练目标和三阶段训练，以及推理时的采样策略。**

---

## 一、LLM 在干什么

LLM 做的事情极其简单：**给定前面的 token，预测下一个 token。**

```
P(爱 | 我) = ?
P(北京 | 我 爱) = ?
```

训练目标：让「正确的下一个 token」概率尽量大 = 最小化交叉熵损失。

## 二、训练三阶段

| 阶段 | 数据 | 学什么 |
| --- | --- | --- |
| **Pretraining** | 海量文本 | 语言本身（下一个 token 预测） |
| **SFT** | 问答对 | 按指令回答（监督微调） |
| **RLHF** | 人类偏好 | 对齐人类价值观（奖励模型 + 强化学习） |

面试话术：**Pretraining 学会说话，SFT 学会听指令，RLHF 学会说人爱听的话。**

## 三、推理采样

| 策略 | 做法 | 特点 |
| --- | --- | --- |
| greedy | 永远选概率最高 | 确定、单调、重复 |
| temperature | 温度缩放后随机抽 | 有创造性 |
| temperature→0 | 退化为 greedy | — |
| temperature↑ | 分布更平坦 | 更随机、更可能胡扯 |

## 四、推理与对齐的进阶（计划要求）

| 概念 | 一句话 |
| --- | --- |
| **DPO** | 直接偏好优化：不用奖励模型+PPO，直接用偏好数据优化策略，比 RLHF 简单稳定 |
| **量化** | 权重从 FP16 降到 INT8/INT4，显存减半甚至 1/4，精度略降换成本 |
| **投机解码** | 小模型先猜几个 token，大模型批量验证——猜对就省一次前向 |

这三项是「让大模型跑得动、跑得起」的推理侧工程重点。

## 四、怎么跑

```bash
cd ~/ai-journey/unit75-llm-principles
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8075
curl "http://127.0.0.1:8075/predict?context=爱"
curl "http://127.0.0.1:8075/sample?context=爱&temperature=2"
curl "http://127.0.0.1:8075/loss?seq=我%20爱%20北京"
```

## 五、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 能说 LLM 的训练目标是什么
- [ ] 能说三阶段各自学什么
- [ ] 能解释 temperature 的作用
- [ ] 能说交叉熵损失越低越好

## 六、面试追问

**Q: 为什么不用 greedy 生成？**
→ greedy 容易重复（一直选最高概率的常见 token）且没有创造性；
所以用 temperature / top-p 采样增加多样性。

**Q: RLHF 为什么需要奖励模型？**
→ 「回答好不好」很难写成规则，所以先训练一个奖励模型来打分，
再用这个分数引导策略优化（PPO）。

**Q: 交叉熵和困惑度什么关系？**
→ 困惑度 perplexity = exp(交叉熵)。都是衡量「模型对文本的惊讶程度」，
越低越好。

---

## 七、资料

- [InstructGPT 论文（RLHF）](https://arxiv.org/abs/2203.02155)
- [温度采样](https://en.wikipedia.org/wiki/Softmax_function)
- [The Illustrated GPT-2](https://jalammar.github.io/illustrated-gpt2/)
