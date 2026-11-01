# Unit 34 笔记 · 手写一个 Agent 循环

> 没有框架，没有 `AgentExecutor`，只有 `while not done: step()`。写完之后你会发现，框架里那些概念全是从这几行里长出来的。

## 本次做了什么

- `agent.py`：`AgentRunner`（构造 → `step()` 推进一轮 → `done`）、`run_agent()` 一次性跑完、`replay_state()` 从 trace 重建 messages、`unanswered_calls()` 检查悬空的 tool_calls。
- `main.py`：两个链式工具（`get_forecast` → `build_packing_list`）、四个模型（`OfflineModel` / `ScriptedModel` / `LoopModel` / `RecordingModel`）、会话式接口、52 条自检。
- `static/index.html`：开一个会话后「走一步」，trace 时间线一格一格长出来，进度条对着 `max_steps`。

## 一、循环的骨架只有这几行

```python
while not self.done:
    self.steps += 1
    msg = model.complete(messages)          # 1. 问模型
    if not msg.get("tool_calls"):           # 2. 没有工具调用 = 该收尾了
        messages.append({"role": "assistant", "content": msg["content"]})
        stopped = "answer" if msg["content"] else "empty"
        break
    messages.append(msg)                    # 3. 声明 + 执行 + 回填
    for call in msg["tool_calls"]:
        messages.append(tool_message(call, execute(call)))
    if steps >= max_steps:                  # 4. 保险丝
        stopped = "max_steps"; break
```

四步里最容易写错的是第 2 步的判断对象：**看 `tool_calls`，不是看 `content`**。
真模型的回复里两者经常同时出现（「我先查一下天气」+ `tool_calls`），按 content 判断会在一半切断。

## 二、四种停止原因，一个都不能少

| stopped | 触发条件 | 实测 |
| --- | --- | --- |
| `answer` | 没有 tool_calls，且 content 非空 | 北京 2 步 / 上海 3 步 |
| `empty` | 没有 tool_calls，content 也是空 | 自检里 ScriptedModel 一条空回复 |
| `max_steps` | 步数用完 | LoopModel 在 3 步触顶 |
| （工具报错） | 工具抛异常 / 参数错 / 未知工具 | 循环继续，错误变成 tool 消息 |

注意第四种不是停止原因 —— 它是**不停止的原因**。工具报错被 `_execute()` 包成
`{"ok": false, "content": "..."}`，作为 tool 消息回填，模型下一步看到错误自己决定怎么办。
这就是 Unit 31/33 一直强调的那句「错误是对话的一部分」在循环里的样子。

`empty` 单独存在的理由：它不是「回答是空字符串」，是模型摆烂。再问一次大概率还是空，
所以直接停，别把空串当答案返回给用户。

## 三、触顶时更容易踩的坑：最后一轮的工具还是得执行

第 3 步模型要调工具，你判断「到上限了」直接 break —— 此时 `messages` 的结尾是：

```
{role:"assistant", content:null, tool_calls:[{id:"call_x", ...}]}     ← 后面没有 tool 消息
```

这个数组发出去，官方报错：`messages with role 'tool' must be a response to a preceding message with 'tool_calls'`。
**先执行、再停**，是本单元唯一一个「为了实现正确性要多做一步无用功」的地方。

自检里用 `unanswered_calls(messages) == []` 钉住它：LoopModel 触顶后 trace 有 3 条 tool 事件（不是 2 条），
步数 3、模型调用 3 次、悬空 id 0 个，四个数字一起看才说明白。

## 四、trace 要能回放，不能只是「看着像」

trace 里每条 tool 事件存两样东西：`digest`（结果前 80 字符，给前端）和 `message`（完整的 tool 消息，给回放）。
`replay_state()` 只做一件事：`[system, user] + [t["message"] for t in trace]`。

自检断言 `replay_state(state) == state["messages"]` —— 一旦某步忘了记 message，这条立刻红。
为什么值钱：线上出问题时你手上只有 trace，如果 trace 里没存够信息，你复现不了那次请求。

实测一条 3 步 trace 是 5 个条目：`model(decision=call_tools)` → `tool` → `model(call_tools)` → `tool` → `model(decision=answer)`。
`decision` 字段是给人和日志看的，它把「为什么停」写在了数据里，而不是靠代码推断。

## 五、步进执行必须和「一次跑完」是同一份代码

`AgentRunner.run()` 就是 `while not done: step()`，没有第二条路径。
自检拿同一个问题跑两次 —— 一次逐步 `step()`、一次 `run()` —— 比较 trace 与 messages
（先把随机生成的 `tool_call_id` 抹平，否则必然不等）。结构一致才算真的等价。

这条看起来像洁癖，实际是页面的可信度问题：如果页面上的「走一步」是另一套演示代码，
那你看到的动画和线上的行为就是两回事，比不做可视化更糟。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 手写 `run_agent` | `AgentRunner` 20 行主体；`run()` = 循环 step |
| 终止条件看 tool_calls | content+tool_calls 同时出现的用例有断言；`decision` 记在 trace 里 |
| 四种停止原因 | answer / empty / max_steps / 工具报错继续，各有断言 |
| `max_steps` 有效 | LoopModel 3 步触顶；`max_steps=0` 直接 ValueError |
| 触顶不产生非法数组 | `unanswered_calls == []`，且最后一次工具真的执行了 |
| trace 可回放 | `replay_state(state) == messages`，一条断言 |
| 步进 = 一次跑完 | 抹平 id 后 trace 与 messages 完全一致 |
| 每步一次模型调用 | `model.calls == steps`，两个模型都验过 |

## 一句话总结

**Agent 的可靠性不取决于模型多会规划，而取决于你的循环在模型胡说八道时还能不能有尊严地停下来。**

## 面试追问

**Q：为什么不用 LangChain / LlamaIndex 的 Agent？**
A：可以用，但你要先能手写一遍。框架帮你做的事就是上面那二十行，加上一堆默认行为（重试、回调、记忆）；出问题时你需要知道它默认了什么。本单元写完之后，`max_iterations`、`handle_parsing_errors`、`early_stopping_method` 这些参数的含义就都是一眼能看懂的东西了 —— 它们全是「停止条件」的变体。

**Q：怎么判断任务是「需要多步」还是「模型在原地打转」？**
A：三个信号：同样的工具 + 同样的参数被连续调用（Unit 36 的熔断就是干这个）；步数增长但 messages 里没有新信息（工具返回和上一轮一样）；模型反复说「我再查一下」但没换参数。工程上最实用的是第一个 —— 参数完全相同就是明确的空转，不需要语义判断。

**Q：`max_steps` 应该设多大？**
A：按「最长合法路径 + 余量」设，不是拍一个 10。本单元最长合法路径是 3（查预报 → 查清单 → 回答），所以上限 6 已经是两倍余量。设太大等于没有保险丝（一次死循环能把账单刷爆），设太小会把正常任务砍断 —— 而且砍断的方式很难看：用户看到的是「达到最大步数仍未得到最终答案」，而不是「我查不到了」。

**Q：一次要调两个工具时，循环结构要改吗？**
A：不用。`for call in msg["tool_calls"]` 本来就是循环，串行执行完再回填两条即可。改的是性能不是结构：并行执行需要 async（Unit 36 会做），但「一个 assistant 消息对应 N 条 tool 消息」这个形状不变。真正要小心的是**顺序**：回填顺序必须和 `tool_calls` 一致，否则日志对不上（Unit 33 有断言）。
