# Unit 4 · Prompt 工程 —— 用数据证明"换个说法，效果真的不一样"

> 今日必须完成：一个能 **A/B 对比不同 Prompt 效果** 的实验台。
> 预计投入：必做 60 分钟 + 选做 30 分钟。


> **关于运行方式**：这些脚本依赖 venv 里的包。
> 先 `source .venv/bin/activate`，或直接用 `.venv/bin/python xxx.py`。
> 用系统 `python3` 会报 `ModuleNotFoundError`。

---

## 一、今日目标（对齐 90 天计划）

| 时段 | 任务 | 对应文件 |
| --- | --- | --- |
| A 10min | 读 OpenAI 的「Message roles」与「Few-shot」两节 | 见下方资料 |
| B 60min | ① 把 5 个 Prompt 模板参数化 ② 同一问题跑多个模板并对比 | `prompts.py`、`experiments.py` |
| C 10min | 笔记：写出「System Prompt 的 4 个作用」 | `notes.md`、`../docs/prompt-notes.md` |
| D 30min | 选做：代码审查助手（few-shot 固定输出格式） | `code_reviewer.py` |

**核心心法**：Prompt 工程最容易被做成"玄学"。今天要把它变成**可测量的工程**——
每个变体先写假设，再跑数据，用「合规率 / 准确率 / token 成本」说话。

---

## 二、必读资料

| 资料 | 为什么读它 |
| --- | --- |
| [OpenAI Prompt Engineering 指南](https://platform.openai.com/docs/guides/prompt-engineering) | 官方最系统的六条策略骨架 |
| [Anthropic Prompt Engineering 概览](https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering/overview) | 写得最细、最工程化的免费教程 |
| [Prompt Engineering Guide（中文）](https://www.promptingguide.ai/zh) | 概念最全，支持中文，当字典查 |
| [Learn Prompting](https://learnprompting.org/docs/intro) | 从零到进阶的渐进式教程 |
| [DeepSeek 思考模式文档](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode) | 什么任务值得开思考模式（Unit 4 涉及成本，很关键） |
| [DeepSeek 模型与价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing) | 算清楚你今天花了多少 |

---

## 三、快速开始

```bash
# 1) 进目录 + 建环境（复用 Unit 3 的 Python 3.13.5）
cd ~/ai-journey/unit04-prompt
uv venv --python /usr/bin/python3 && source .venv/bin/activate
uv pip install -r requirements.txt

# 2) 复用 Unit 3 的 Key（不用重新申请）
cp ../unit03-cli-chat/.env .env

# 3) 离线自检：不花钱，先确认代码逻辑没问题
python selfcheck.py

# 4) 先看 Prompt 长什么样（dry-run，不调用 API，不花钱）
python experiments.py variants --dry-run

# 5) 正式跑（省钱模式：只用 2 条用例）
python experiments.py variants --cases 2
```

---

## 四、四个实验（B 段的核心动作）

| 命令 | 验证什么 | 必做/选做 |
| --- | --- | --- |
| `python experiments.py variants --cases 2` | 从"裸问"逐步加角色→约束→格式→示例，**每一步带来多少提升** | 必做 |
| `python experiments.py fewshot --cases 2` | 0 / 1 / 3 个示例的性价比（合规率 vs 输入 token） | 必做 |
| `python experiments.py system --cases 2` | 同一份五段式，放 System 和放 User 有没有差别 | 必做 |
| `python experiments.py temperature --cases 1 --repeat 3` | temperature 升高后输出是否漂移、合规率是否掉 | 选做 |
| `python code_reviewer.py` | few-shot 锁定输出结构（能解析出几条结构化问题） | 选做 |

**7 个变体（`prompts.py`）的设计逻辑**：

```
v0 裸问 → v1 +角色 → v2 +约束 → v3 +输出格式 → v4 +3示例 → v5 五段式全上
                                                          └→ v6 同样内容放进 User（对照组）
```

跑完把汇总表里的「合规率 / 准确率 / 平均输出 token」抄进 `notes.md` —— 这三个数字就是 Unit 4 的产出。

---

## 五、⚠️ 实测情报（2026-10-14，跑之前先看，能省你的钱和时间）

这些是上手前实测出来的，直接决定你怎么设参数：

| 事实 | 后果 | 你该怎么做 |
| --- | --- | --- |
| `deepseek-flash` 是**推理模型**，推理 token 和正文共用 `max_tokens` | 实测一个"只输出一个词"的分类任务，输出 57~70 token 里 **90% 以上是推理 token** | `max_tokens` 至少给 **300**；别用 Unit 3 那种 1024 的直觉去设小值 |
| 输出 token 大头是推理 | 你"看起来很短"的任务其实也在花钱 | 每次实验前看预估成本，`--cases 2` 就是为省钱设计的 |
| **未知参数会被静默忽略** | 传 `effort="low"` / `reasoning_effort="low"` **不报错也不生效**（传非法值 `"nonsense"` 同样不报错），推理 token 没变化 | 判断参数是否生效**必须看 `usage` 数据**，不要以为"传了就生效" |
| `finish_reason` 是可靠信号 | `length` = 被截断（`llm.py` 已自动告警） | 看到 ⚠️ 就调大 `max_tokens` |
| 单次调用约 ¥0.0004 | 一个实验（7 变体 × 2 用例）约 ¥0.006 | 今天全套跑完预计 ¥0.02 以内 |

> 第 3 条是今天最值钱的工程习惯：**"参数传了就生效"是错觉，要用 outputs 的数据反推。**
> Unit 13 做限流/重试时会再用到这条。

---

## 六、文件说明

| 文件 | 作用 |
| --- | --- |
| `prompts.py` | **Prompt 模板库**：五段式构造函数 + 7 个变体 + 合规判定/归一化 |
| `experiments.py` | **实验台**：4 个实验 + 结果表格 + 自动存 `logs/` 的 JSON |
| `code_reviewer.py` | 选做：few-shot 代码审查助手（输出可被正则解析 → 衔接 Unit 5） |
| `llm.py` | LLM 调用封装（沿用 Unit 3，新增截断告警 / trace_id / `extra_body` 透传） |
| `selfcheck.py` | 离线自检：不联网验证模板拼装、变体完整性、合规判定、成本计算 |
| `notes.md` | 今日笔记（含实验数据表，别凭印象填） |
| `../docs/prompt-notes.md` | Prompt 工程的长期知识沉淀（面试可答版） |

---

## 七、完成标准 ✅

- [ ] 能说清 **System Prompt 和 User Prompt 的本质区别**（不是"一个在前一个在后"）
- [ ] 能写出「角色 + 任务 + 约束 + 输出格式 + 示例」五段式 Prompt（`five_part_text()` 就是标准答案）
- [ ] 4 个实验至少跑完 3 个，数据抄进 `notes.md`
- [ ] 实验结果沉淀进 `../docs/prompt-notes.md`
- [ ] `notes.md` 写完并 `git commit`

---

## 八、常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| 输出是空的 `''` | `max_tokens` 太小，全被推理 token 吃掉了 → 调到 300 以上 |
| 看到 `⚠️ 回答被 max_tokens 截断了` | 同上。分类任务的合规率会因此虚低，先调大再下结论 |
| `❌ API Key 无效或已过期（401）` | 先重试一次再判断——Unit 3 验收实测过：同一 Key 会因服务端抖动瞬时 401 |
| 合规率全是 0% | 检查变体是否真的带上了输出格式段；`--dry-run` 打印 System Prompt 确认 |
| `uv: command not found` | `export PATH="$HOME/.local/bin:$PATH"` |
| 想复用 Unit 3 的 venv | 不建议跨目录用；`uv venv` 建本目录的更快更干净 |

---

## 九、和 Unit 5 的衔接

- `code_reviewer.py` 里用正则解析模型输出 → Unit 5 会换成 **Pydantic 校验**，把"能解析"升级成"能保证"
- 今天测出的「输出格式段一去掉，合规率就掉」→ 正是 Unit 5 要说的「**必须显式要求模型输出 JSON**」
- 今天的 `temperature=0` 结论 → Unit 5 抽取任务固定用 0
