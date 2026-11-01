# Unit 33 笔记 · 结果是一条消息，不是一个返回值

> 你在 `execute()` 里拿到了一个 dict，但模型永远看不到它 —— 它只能看到你写进 `messages` 的那条字符串。

## 本次做了什么

- `messages.py`：`append_tool_results()`（一次调用一条，id 必须对上，缺一条直接抛错）、`validate_messages()`（能抓 8 类问题，每条带 index）、`to_content()`（截断信封）、`count_tokens()`、统一的 `ok_result`/`error_result`。
- `main.py`：两个工具（天气 + 返回 40 条订单的大结果）、会并行要两个工具的 `OfflineModel`、6 个接口、59 条自检。
- `static/index.html`：messages 数组可视化、`回填` 按钮控制数组生长、五种坏数组的对照表。

## 一、一次调用在数组里占三条消息

```
{role:"user",      content:"上海和北京的天气"}
{role:"assistant", content:null, tool_calls:[{id:"call_a",...},{id:"call_b",...}]}
{role:"tool",      tool_call_id:"call_a", content:"{\"ok\":true,\"data\":{...}}"}
{role:"tool",      tool_call_id:"call_b", content:"{\"ok\":true,\"data\":{...}}"}
{role:"assistant", content:"上海多云18°C；北京晴9°C。"}
```

实测这条两工具链路一共 **6 条消息**（含 system），估算 **109 token**，其中 tool 消息 **36 token**。
两个细节：`content:null` 是合法的（模型只说话不发内容时就是 null，但 `tool_calls` 必须在）；
两条 tool 消息**顺序无所谓**，真模型按 id 匹配 —— 自检里专门把顺序颠倒跑了一次，照样通过。

## 二、`content` 必须是字符串，`str(dict)` 是陷阱

```python
str({"city": "上海"})              # "{'city': '上海'}"   ← 单引号，json.loads 抛 JSONDecodeError
json.dumps({"city": "上海"}, ensure_ascii=False)   # '{"city": "上海"}'
```

自检里两条断言就是干这个的：`json.loads(str(payload))` 必须失败，`json.loads(to_content(payload))` 必须成功。
`ensure_ascii=False` 也别忘 —— 默认会把中文转成 `\u4e0a\u6d77`，token 数直接涨 3 倍。

`to_content()` 还顺手处理了「工具直接返回一段文本」的情况（比如 grep 结果），不是所有结果都该被 JSON 包一层。

## 三、`validate_messages()`：本章最值钱的东西

回填逻辑写一次就对了，但线上 400 全在「某条没回填」上。校验器检查四件事，返回带 index 的问题列表：

| code | 触发条件 |
| --- | --- |
| `missing_tool_result` | 声明了 `tool_calls` 但没有对应的 tool 消息（index 指向那条 assistant） |
| `orphan_tool` / `duplicate_tool_result` | tool 消息前面找不到声明它的 assistant / 同一个 id 被回填两次 |
| `content_not_str` / `empty_content` | content 不是字符串 / 是空串 |
| `name_mismatch` | tool 消息的 `name` 和 `tool_calls` 里的函数名不一致 |
| `tool_calls_bad_shape` | id 缺失、`type != function`、`arguments` 是对象而不是字符串 |
| `unknown_role` | role 不在 system/user/assistant/tool 里 |

实现上有一个关键状态：`pending`（call_id → 声明它的下标）。遇到 assistant 就登记，遇到 tool 就消账，
遇到**任何非 tool 消息时检查 pending 是否清空** —— 这一条能抓出「tool 消息漏在中间」的情况，
也是官方报错信息里永远不会告诉你的部分（它只说「有一处不匹配」）。

id 写错时会同时报两条（孤儿 + 缺失），不是重复报错 —— 因为一个 id 错了，必然有两个位置对不上。

## 四、截断：切片会把 JSON 切成废纸

40 条订单的原始 content 是 **6798 字符 ≈ 2250 token**。直接 `content[:1200]` 会得到半个 JSON，
`json.loads` 抛错，模型读到的是「一串看不懂的字符」—— 它不会说「数据不完整」，它会开始编。

所以用信封：

```python
{"truncated": true, "original_chars": 6798, "preview": "<前 1200 字符>"}
```

实测 **6798 字符 → 1447 字符，2250 token → 455 token，降 80%**，且 `json.loads` 依然成功。
模型看到 `truncated:true` 会说「结果太大，只看了前 1200 字符」，而不是硬编一个答案 —— 这条也写成了断言。

信封里的 `truncated` 是本地标记，**发请求前要用 `strip_local_fields()` 剔掉**：某些 SDK 对消息里的未知字段严格校验。

## 五、token 估算：够用就行，别装精确

估算公式：`cjk + (其余字符数 + 3) // 4`。中文 1 字 ≈ 1 token、ASCII 4 字符 ≈ 1 token，这是经验值（真实 tokenizer 会差 10~20%）。
它的用途不是算钱，是**相对比较**：换一种截断策略、加一条 tool 消息，看看涨了多少。
要精确就必须调 `tiktoken` 之类，但那是另一章的事；这里的 109 / 524 这些数字都只是量级参考。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 三条消息的形状写对 | `assistant(tool_calls)` → `tool(tool_call_id)` → `assistant`，自检 5 条 |
| `content` 是字符串 | `str(dict)` 与 `json.dumps` 的对错各一条断言；中文不转义 |
| 多调用回填多条 | 两工具 turn 实测 6 条消息、2 条 tool、顺序任意 |
| 缺一条能被指出 | `missing_tool_result` + index 指向 assistant，另加 4 类问题 |
| 截断生效 | 6798→1447 字符、2250→455 token，信封仍是合法 JSON |
| 错误结果形状统一 | `{"ok":false,"error":{"type","message"}}` 一条断言 |
| 五种坏数组全被抓 | `/broken` 五个场景，校验器都非空 |

## 一句话总结

**回填的本质不是「把结果放回去」，而是「让数组保持可被模型校验的形状」—— 长度对、id 对、类型对，一个都不能少。**

## 面试追问

**Q：为什么 `tool` 消息要单独带 `name`？id 不是已经唯一了吗？**
A：`name` 是冗余的（官方文档里也可选），但它在两个地方有用：日志里不查 `tool_calls` 就能看出调了哪个工具；以及并行调用时人能看懂哪条对应哪个。代价是它必须和 `tool_calls` 里的函数名一致 —— 所以校验器加了 `name_mismatch`，不一致时会报出来，而不是让模型自己猜。

**Q：结果太大，为什么不直接让模型去查数据库？**
A：那等于把数据库权限交给模型（见 Unit 31 的追问）。正确边界是：工具决定查什么、查多少，模型只负责提需求和读结果。所以截断策略必须在**工具侧**做（`limit` 参数、分页），`content` 截断只是最后一道保险 —— 它能让请求不炸，但模型拿不到完整数据时给出的答案质量一定会掉，这一点没法用工程手段绕过去。

**Q：`validate_messages()` 在生产环境怎么用？**
A：两个位置。一是在**发请求前**（`assert` 或显式检查，非法就别发，直接记日志 + 返回降级答案）；二是在**测试里**对一批真实轨迹做回归 —— 把线上收集的 messages 数组存下来，每次改回填逻辑都跑一遍。第二种更值钱，因为数组形状的 bug 往往只在「模型一次返回 3 个工具」这种真实长尾里出现，手写用例想不全。

**Q：多轮 Agent 里，历史里的 tool 消息要不要定期清理？**
A：要。tool 消息是上下文里最占地方的部分（实测一条大结果 455 token，一次 5 轮就是 2000+）。
常见做法是：保留最近 N 轮的完整 tool 结果，更早的替换成一句话摘要（「调用了 search_orders，命中 40 条」），
并在**替换时保持 id 对应关系不变** —— 这里的风险是：你删了某条 tool 消息但留着声明它的 assistant，
数组立刻非法。所以清理逻辑本身也要过一遍 `validate_messages()`。
