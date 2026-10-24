# Unit 10 · Context 管理：滑动窗口 vs 摘要压缩

> 目标：**画得出你的上下文预算表，说得出每种策略的代价**。
> 这是 RAG、Agent 之前最后一个「纯 LLM」单元，也是面试最常追问的地方。

---

## 一、问题：多轮对话为什么会撑爆

LLM 无状态，每轮都要重发完整历史：

| 轮次 | 本次输入 token（实测估算） |
| --- | --- |
| 1 | 155 |
| 5 | 747 |
| 10 | 1487 |

10 轮累计约 **8,210** tokens，而每一轮只是 100 字左右的内容。

**后果**：
1. **超上下文窗口** → 直接报 `context length exceeded`
2. **变贵** → 输入 token 按轮次累加
3. **变慢** → 输入越长，首字延迟越高
4. **变傻** → 长上下文中间位置的信息利用率下降（lost in the middle）

---

## 二、三种策略

### 1. `none`：不处理（基准对照）

用来**观察劣化**。跑长对话时看输入 token 怎么涨、第几轮开始报错。

### 2. `sliding`：滑动窗口

```
保留 = [System] + 最近 N 条
```

- ✅ 实现简单，行为可预测
- ❌ **硬丢**旧消息，模型彻底失忆
- 关键保护：`min_keep=2` —— 哪怕超预算，也至少保留最近 2 条

### 3. `summarize`：摘要压缩

```
保留 = [System] + [旧对话摘要] + 最近 N 条
```

- ✅ 不完全失忆，关键信息还在
- ❌ 每次触发多一次模型调用（**额外成本 + 延迟**）
- ❌ 摘要本身会丢细节、可能引入错误
- 保护：`keep_recent=4` —— 最近几轮原样保留（细节最重要）

### 实测对比（同一份 20 条历史，预算 600）

```
none       最终 1487 tokens · 丢弃  0 条 · 省  0.0%
sliding    最终  599 tokens · 丢弃 12 条 · 省 59.7%
summarize  最终  329 tokens · 丢弃 16 条 · 省 77.9%
```

---

## 三、上下文预算表

面试必问「你的 token 怎么分配」。下面是一份可直接背的答案：

```
上下文预算表（deepseek-flash 示例，窗口 64k）
------------------------------------------
上下文窗口总量         64,000  100.0%
预留输出               4,096    6.4%   ← 必须留，否则回答被截断
System Prompt            300    0.5%
用户本轮输入             200    0.3%
检索片段(RAG)              0    0.0%   ← Unit 15+ 才用
可用于历史            59,404   92.8%
```

**核心原则**：先扣掉「预留输出」，再给历史。反过来会经常出现答案被截断。

---

## 四、怎么跑

```bash
cd ~/ai-journey/unit10-context
uv sync                         # 建 .venv + 按 uv.lock 装依赖（幂等）
cp .env.example .env && chmod 600 .env

uv run python test_context.py   # 纯逻辑测试：31 项，不联网
uv run python main.py           # 后端自检：21 项（含 summarize 路径回归）
uv run uvicorn main:app --port 8000
```

前端：

```bash
uv run python -m http.server 3000 -d static
```

打开 **http://127.0.0.1:3000/**

---

## 五、文件结构

| 文件 | 内容 |
| --- | --- |
| `context.py` | **核心**：`trim_messages` / 两种策略 / 预算表 / token 估算 |
| `test_context.py` | 纯逻辑测试，31 项，不需要 API |
| `main.py` | 后端：把策略接进流式接口 + `/compare` 对比接口 |
| `static/index.html` | 前端：预算表 + 三策略对比 + 实时裁剪可视化 |

### 关键 API

```python
from context import trim_messages, Budget

result = trim_messages(
    messages,
    max_tokens=2000,
    strategy="sliding",     # none | sliding | summarize
)
print(result.report())
# 策略: sliding
# 原始: 1487 tokens
# 最终: 599 tokens（省 888，59.7%）
# 丢弃: 12 条
```

---

## 六、token 估算的准确性

`context.py` 用的是估算公式，**不是精确计数**：

```python
cjk   = 中日韩字符数 / 1.5
other = 其它字符数 / 4
```

误差来源：
- 精确计数需要 `tiktoken`，要联网下词表（本机 GitHub 不通）
- 不同模型分词器不同（DeepSeek / GPT 用的词表不一样）
- 每种消息还有固定的格式开销（这里按 4 token/条估算）

**实践建议**：
- 做预算分配时，误差 ±20% 完全够用（留够余量就行）
- 要精确对账时，用 API 返回的 `usage.prompt_tokens` 校准

---

## 七、前端能看到的三个东西

1. **预算表** —— `/budget` 接口返回
2. **三策略对比表** —— `/compare` 接口，本地模拟 12 轮历史，不花钱
3. **实时裁剪可视化** —— 每条消息处理后显示彩色条：
   - 紫色 = System
   - 蓝色 = 保留的历史
   - 灰色 = 被丢弃

---

## 八、完成标准

- [ ] `uv run python test_context.py` → 31 通过 / 0 失败
- [ ] `uv run python main.py` → 21 通过 / 0 失败
- [ ] 浏览器跑「三策略对比」，能看出三者的省 token 比例差异
- [ ] 真实对话里依次试 `none` / `sliding` / `summarize`，观察回答质量变化
- [ ] **能画出自己的上下文预算表**（System / 历史 / 检索 / 输出 各占多少）
- [ ] 能解释：为什么 `summarize` 省得更多但代价更大

---

## 九、面试追问

**Q: 你的 RAG 系统上下文怎么分配？**
> 64k 窗口里，预留 4k 给输出，System 约 300，检索片段给 8k（约 Top-5 每段 1.5k），
> 剩余 50k+ 给对话历史。实际会按场景调：抽取类任务多给检索，闲聊多给历史。

**Q: 摘要压缩什么时候触发？**
> 我按「历史 token 超过预算」触发，而不是按轮数。因为每条消息长度差异很大，
> 按轮数会误判。触发后保留最近 4 条原样，更早的交由模型压缩。

**Q: 为什么摘要用 keep_recent=4 而不是全部摘要？**
> 最近几轮的细节对当前回答最关键，压成摘要会丢指代关系
> （比如「它」「那个方案」指的是什么）。摘要只适合处理「已经结束的话题」。

---

## 十、资料

- [OpenAI Tokenizer 在线工具](https://platform.openai.com/tokenizer)
- [tiktoken 仓库](https://github.com/openai/tiktoken)
- [DeepSeek Token 用量计算](https://api-docs.deepseek.com/zh-cn/quick_start/token_usage)
- [Lost in the Middle 论文](https://arxiv.org/abs/2307.03172) —— 长上下文为什么变傻
- [LangChain 消息裁剪（概念参考）](https://python.langchain.com/docs/how_to/trim_messages/)
- [Anthropic: Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)
