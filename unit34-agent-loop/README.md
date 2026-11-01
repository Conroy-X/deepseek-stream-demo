# Unit 34 · Agent Loop

> Agent 不是什么新东西，就是一个 while 循环 —— 加上一个足够诚实的停止条件。

**完成标准**：能手写 `run_agent()` 并说清四种停止原因（answer / empty / max_steps / 工具报错继续），trace 能一步步回放。

## 快速开始

```bash
cd ~/ai-journey/unit34-agent-loop
uv sync
uv run uvicorn main:app --reload --port 8034
```

打开 <http://127.0.0.1:8034/>：问「上海出差要带伞吗」，点「走一步」看 trace 一格一格长出来，
进度条走到 `max_steps` 就会停。

## 自检

```bash
uv run python main.py
```

52 项，全离线：四种终止条件、2 步/3 步收敛、每步只调一次模型、触顶保护、
步进执行与一次跑完等价、trace 回放、工具报错不掀翻循环、HTTP 会话。

## 这个目录里有什么

| 文件 | 说明 |
| --- | --- |
| `agent.py` | `AgentRunner`（可步进的循环）、`run_agent()`、`replay_state()`、`unanswered_calls()` |
| `main.py` | 两个工具、`OfflineModel` / `ScriptedModel` / `LoopModel` / `RecordingModel`、会话接口、52 项自检 |
| `static/index.html` | 走一步 / 一次跑完、trace 时间线、messages 展开 |

## 实测

三个问题，同一份循环代码，步数不一样：

| 问题 | steps | 模型调用 | trace 条目 | 调用链 |
| --- | ---: | ---: | ---: | --- |
| 北京出差要带伞吗 | 2 | 2 | 3 | `get_forecast` → 回答「降雨 0mm，不用带伞」 |
| 上海出差要带伞吗 | 3 | 3 | 5 | `get_forecast` → `build_packing_list` → 「要带：雨伞、防水外套」 |
| 广州出差要带伞吗 | 3 | 3 | 5 | 同上（中雨 12mm） |
| （LoopModel，max_steps=3） | 3 | 3 | 6 | 触顶，`stopped=max_steps`，悬空 tool_calls 为 0 |

`steps` 和 `模型调用次数` 恒等 —— 每一步恰好一次模型调用，这是循环最容易写错的地方（有人会在一步里问两次模型）。
晴天 3 条 trace 是 `model → tool → model`，雨天 5 条是 `model → tool → model → tool → model`。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 工具列表、默认 `max_steps` |
| GET | `/tools` | 工具声明 |
| POST | `/session` | `{question, max_steps}` → 会话 id + 初始状态（steps=0） |
| POST | `/session/{id}/step` | 走一步，返回最新状态 |
| GET | `/session/{id}` | 当前状态（trace / messages / stopped） |
| POST | `/run` | 一次跑完，直接返回终态 |

## 常见坑

**1. 终止条件不是「有 content」，是「没有 tool_calls」。** 真模型经常回
`{"content":"我先查一下天气","tool_calls":[...]}` —— 两样都有。按 content 判断会在一半就切断，
后面的 tool 消息就永远发不出去。顺序必须是：先看 `tool_calls`，空了才看 `content`。

**2. 空 content + 空 tool_calls 必须单独处理。** 这不是「回答是空字符串」，是模型摆烂了。
再问一次大概率还是空，所以直接 `stopped="empty"` 停下，不要把空串当成最终答案返回给用户。

**3. 触顶时**仍然要执行**最后一次工具调用。** 本单元最容易忽略的细节：第 3 步模型要调工具，
你直接跳过执行就停下 —— 此时 `messages` 停在「assistant 带 tool_calls，但后面没有 tool 消息」，
这个数组发出去就是 400。所以先执行、再停，`unanswered_calls()` 断言为 0。

**4. `max_steps` 是保险丝，不是逻辑。** 实测 `LoopModel`（永远要调工具）在 3 步时停下，
但真实 Agent 的「正常」步数常在 2~5。上限设 6 是为了留余量，不是让人去调大它 ——
一个需要 20 步的任务，通常是工具粒度太细，或者模型在原地打转。

**5. trace 里只存摘要不存全文，回放就会失真。** 本单元的 tool 条目同时存了 `digest`（80 字符，给前端看）
和完整 `message`（给 `replay_state()` 用）。只存 digest 的话，回放出的数组和真实请求不一致，
你就没法用 trace 复现 bug —— 那 trace 的价值只剩「看看」了。

## 验收清单

- [ ] `uv run python main.py` → 52 通过 / 0 失败
- [ ] 页面点「走一步」，第 1 步只有 model 事件，第 2 步才出现 tool 事件
- [ ] 问上海（下雨）走 3 步，问北京（晴）走 2 步
- [ ] 把 `max_steps` 填成 1，走一步就停，`stopped` 显示 `max_steps`
- [ ] 展开 messages，能看到 `assistant(tool_calls)` 后面紧跟 `role=tool`
- [ ] 能说出为什么「有 content 就停」是错的

## 下一单元

Unit 35 把工具从 2 个加到 5 个：模型一次可能返回多个 `tool_calls` 并行执行，
而「工具变多」带来的新问题不是执行，是**选错**。
