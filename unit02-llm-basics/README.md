# Unit 2 · LLM 基础

> 对应原计划的 Day 2：LLM / Token / Context Window / Prompt / Temperature / API。
> 那一天只"理解概念"，所以这里不放应用代码，只放一个**把概念变成可观测现象**的实验脚本。

---

## 一、这个单元要建立的 5 个认知

| 概念 | 一句话解释 | 在本单元怎么验证 |
| --- | --- | --- |
| **Token** | 模型处理文本的最小单位，约等于「中文 1~2 字 / 英文 4 字符」 | `demo.py --offline` 实验 1 直接打印对照表 |
| **Context Window** | 一次请求能容纳的「输入 + 输出」总 token 上限 | 实验 3 看输入 token 逐轮累加 |
| **Prompt** | System（开发者规则）+ User（用户输入）的组合 | 实验 2/4 里改一句就看得出差别 |
| **Temperature** | 控制随机性，0 最确定、2 最发散 | 实验 2 同一问题跑 3 次对比 |
| **API 调用** | 请求 → 计费 → 返回，一切都按 token 算 | 实验 3/4 每次都打印 token 与费用 |

**最关键的一条**：LLM 本身**没有记忆**。所谓"对话"，是应用层每次把完整历史重新发一遍。
这直接导致了 Unit 10 要解决的上下文管理问题。

---

## 二、怎么跑

```bash
cd ~/ai-journey/unit02-llm-basics
uv sync                   # 建 .venv + 按 uv.lock 装依赖（幂等）

cp .env.example .env      # 填入 DEEPSEEK_API_KEY
chmod 600 .env

uv run python demo.py --offline  # 不花钱：只看 token 计数
uv run python demo.py            # 四个实验全跑（约 ¥0.01）
uv run python demo.py --exp 2    # 只跑 temperature
```

> 依赖和解释器都交给 uv：`uv run` 会自动用本目录的 `.venv`，不用手动 `source`。

---

## 三、四个实验各自在证明什么

### 实验 1 · Token 不等于字数

打印「文本 / 字符数 / token 数」对照表。要得出的结论：

- 中文和英文的 token 密度完全不同，**按字数估成本会算错**
- 标点、空格、代码符号都算 token
- Prompt 模板里的固定文字（角色设定、示例）**每次调用都在重复付费**

### 实验 2 · Temperature 的边界

同一个问题分别在 `0.0 / 0.7 / 1.5` 下各跑 3 次，统计出现几种不同回答。

- `0.0` 通常最稳定，但**不保证逐字相同**（浮点累加顺序、批处理、MoE 路由都会带来微差）
- 事实抽取/分类/结构化输出 → `0~0.3`
- 创意写作 → `0.8~1.2`
- `temperature` 和 `top_p` 别同时乱调

### 实验 3 · Context 的增长曲线

4 轮对话，每轮打印「本次输入 token / 累计输入 / 本次费用」。

- 输入 token 逐轮累加，总成本接近**平方级**
- 轮次多了以后变贵、变慢、还可能在中间位置丢失信息（lost in the middle）
- 这就是 Unit 10 要做截断/摘要的原因

### 实验 4 · max_tokens 与 stream

- `max_tokens=30` 时会看到 `finish_reason='length'` —— 这是**被截断**，不是答完了
- `stream=True` 让文字逐字出现，首字延迟（TTFT）大幅下降，是 Chat UI 的标配

---

## 四、本单元完成标准

- [ ] `uv run python demo.py --offline` 能打出 token 对照表
- [ ] 四个实验都跑过一遍，知道每个实验在证明什么
- [ ] 能用自己的话解释「为什么 LLM 没有记忆」
- [ ] 能说出 `temperature` / `max_tokens` / `stream` 各自影响什么
- [ ] 笔记里写下这 4 个结论（可放 `docs/unit02-notes.md`）

---

## 五、资料

- [DeepSeek Token 用量计算](https://api-docs.deepseek.com/zh-cn/quick_start/token_usage)
- [DeepSeek 模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing)
- [OpenAI Tokenizer 在线工具](https://platform.openai.com/tokenizer)（可视化看切分）
- [tiktoken 仓库](https://github.com/openai/tiktoken)
- [DeepSeek 思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode)
- [HuggingFace LLM Course 中文](https://huggingface.co/learn/llm-course/zh-CN/chapter1/1)
