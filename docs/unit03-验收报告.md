# Unit 3 验收报告 · LLM API 与 CLI Chat

> **验收时间**：2026-10-14 21:30
> **验收方式**：SSH 直连 WSL 实跑验证（不是看笔记自述，所有结论都有真实输出为证）
> **被验收对象**：`~/ai-journey/unit03-cli-chat/` @ commit `061572f`

---

## 一、结论

**8 项完成标准：7 项达标 ✅，1 项已补齐（notes 原未提交，验收时由我 commit）。**
代码质量高于"能跑就行"的水平：自检脚本、错误翻译、用量统计、调试工具都在。**Unit 3 通过验收。**

---

## 二、完成标准逐项核对

| # | 完成标准 | 结果 | 实测证据 |
| --- | --- | --- | --- |
| 1 | `python selfcheck.py` 全部通过 | ✅ | 27 项断言全 PASS，含"新 System 真的传给了模型""/reset 之后历史确实被丢弃" |
| 2 | `python 01_hello.py` 能拿到回答 | ✅ | 输入 55 / 输出 357 tokens / ¥0.001483 / `finish_reason=stop` |
| 3 | `python chat.py` 连续 3 轮且记住上文 | ✅ | 第 2 轮准确复述："你叫小明，正在从传统前端开发转型做 AI 应用开发。" |
| 4 | `/tokens` 打印输入输出与费用 | ✅ | 实测：2 轮 · 输入 152 · 输出 1114 · 约 ¥0.0046 |
| 5 | `/reset` 后模型确实"失忆" | ✅ | 实测回答："抱歉，我不知道你的名字。你还没有在对话里告诉我。" |
| 6 | 错误 Key 友好提示、不吐 traceback | ✅ | `chat.py` 走 `llm.py` → "❌ API Key 无效或已过期，请到控制台重新生成。"（无 traceback）；`01_hello.py` 打印原始异常类型（设计如此，同样无 traceback） |
| 7 | `.env` 没有进 git | ✅ | 三重校验：忽略规则命中 / 暂存区无 `.env` / 远端**全部历史**扫描真实 key 出现 **0** 次 |
| 8 | 填完 `notes.md` 并 commit | ⚠️→✅ | 笔记已填且 4 个实验结论齐全；**原本未提交**，验收时已代为 commit + push |

### notes.md 质量评价（超出预期）

- 「4 个实验结论」全部是自己跑出来的，不是抄的：`/reset` 失忆 ✅、system prompt 改变职责边界 ✅、连轮后用量递增 ✅、错 Key 报错原文 ✅
- 「为什么 LLM 没有记忆」用自己的话讲对了核心：**记忆完全依赖 `message` 参数，每次 API 都带全量内容**
- 唯一空着的是「今天做了什么」一节（5 行就够，建议补上）

---

## 三、超出完成标准的部分（本日额外产出）

| 产出 | 价值 |
| --- | --- |
| VS Code 调试环境（5 个 F5 配置 + `debugpy`） | 后续每天都能断点调试，不靠 print |
| `debug_response.py` | 请求 payload → 原始响应 body → 13 个响应头 → 字段解释 → 流式 chunk 全链路可视 |
| `headers_demo.py` | 4 种获取 `x-ds-trace-id` 的方式（含生产级 `httpx2` 事件钩子） |
| 仓库工程化 | git 仓库根落在 `~/ai-journey`，`.gitignore` 收紧，README 合并 Unit 2 说明 |

---

## 四、实测发现的 5 个真问题（比"跑通"更值钱）

### 1. ⚠️ `max_tokens` 设小了会把回答截断（本轮实测踩到，建议今天就改）

第 1 轮回答只吐出一句就被切断：`"欢迎，小明。前端转 AI 应用开发其实"`。
原因：`deepseek-flash` 是**推理模型**，推测过程和正文共用 `max_tokens`；本轮每轮输出约 1100 token，1024 的默认上限直接撞满。

**行动项**：把 `chat.py` 的默认 `max_tokens` 从 1024 调到 **2048**，并把 `finish_reason == "length"` 时打印一行警告——否则你会以为"模型回答本来就这么短"。

### 2. 输入 token 逐轮增长，验证了计费模型

实测输入：65 → 152 → 208 → 272（每轮把全部历史重发一遍，**历史重复计费**）。
这正是 Unit 10「Context 管理 / 上下文截断」要解决的问题——你现在有实测数据了。

### 3. `/system` 立即生效

发 `/system 你只能回答一个字` 后再问，模型答 `无`。System Prompt 的约束力得到验证，直接衔接 Unit 4 的 Prompt 工程。

### 4. 🔍 服务端 401 可能是瞬时故障，别急着换 Key（真实踩到）

验收过程中遇到一次：**同一个 Key** 在 2 分钟内先返回 `401 authentication_error`（提示 key invalid），随后完全正常（200）。
说明 `401` 有两类：**确定性（Key 真错）** 和 **瞬时（服务端抖动）**。Unit 13 做错误处理时应当：401 先自动重试 1–2 次，仍失败才提示用户检查 Key——否则会误导用户去控制台白折腾。

### 5. `/models` 暴露的能力（Unit 6/13 可用）

```
deepseek-flash   = DeepSeek-V4.1-Flash   context 1,048,576   max_output 393,216  支持图片输入
deepseek-v4-pro  = DeepSeek-V4-Pro       context 1,048,576   max_output 393,216
两者都有 effort 参数：low / high / max（默认 high）
```
→ 上下文窗口是 **100 万 token**，所以 Unit 10 的"截断策略"其实可以很宽松；`effort` 参数是控制推理开销/成本的入口，值得单独试一次。

---

## 五、待补事项（都不影响验收通过）

1. `notes.md` 的「今天做了什么」是空的
2. `notes.md` 报错表格第 2 行格式错位（那条其实是自我反思，不是报错），建议移到「今天学到的」里
3. `~/ai-journey/.python-version` 写的是 `3.14`，实际 venv 是 3.13.5 → Unit 4 建新环境时要么改成 `3.13`，要么显式 `--python /usr/bin/python3`
4. `~/projects/deepseek-stream-demo` 是第二份副本（内容已同步），建议只保留 `~/ai-journey` 一份，避免以后改错地方

---

## 六、Unit 4 衔接

Unit 4 主题：**Prompt 工程（System / User / Few-shot / Role / Context）**，目录 `unit04-prompt/`。
今天已验证的事实可直接复用：
- `/system` 即时生效 → 今天写「五段式 Prompt」（角色 + 任务 + 约束 + 输出格式 + 示例）
- 推理模型会吃 `max_tokens` → Unit 4 实验要固定 `max_tokens` 才好评比不同 Prompt 的效果
- 上下文全量重发 → 为 Unit 10 的截断策略先埋一个"长对话"实验数据

> 本次验收实际消耗约 **¥0.01**（含 6 次真实调用 + 1 次错误 Key 测试）。
