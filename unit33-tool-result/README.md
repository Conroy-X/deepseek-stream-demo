# Unit 33 · Tool Result 回填

> 工具结果不是一个返回值，是一条消息 —— 而且一条不对，整个请求 400。

**完成标准**：能手写 `assistant(tool_calls) → tool(tool_call_id) → assistant` 这三条消息，并且能用一个校验器抓出「少回填一条」。

## 快速开始

```bash
cd ~/ai-journey/unit33-tool-result
uv sync
uv run uvicorn main:app --reload --port 8033
```

打开 <http://127.0.0.1:8033/>：问「上海和北京的天气」，数组先停在 assistant 的 `tool_calls` 上，
点「回填」看 tool 消息怎么长出来，旁边实时显示 token 估算与截断标记。

## 自检

```bash
uv run python main.py
```

59 项，全离线：合法/非法数组的形状、`validate_messages()` 能抓的 8 类问题、
多调用多条回填、`json.dumps` 与 `str(dict)` 的区别、截断信封、token 估算、五种坏数组、HTTP 层。

## 这个目录里有什么

| 文件 | 说明 |
| --- | --- |
| `messages.py` | `append_tool_results` / `validate_messages` / `to_content`（截断）/ `count_tokens` |
| `main.py` | 两个工具（含一个返回 40 条订单的）、`OfflineModel`（会一次要两个工具）、6 个接口、59 项自检 |
| `static/index.html` | messages 数组可视化、回填按钮、坏数组对照表 |

## 实测

一次「上海和北京的天气」（模型并行要两个工具）：

| 项 | 值 |
| --- | --- |
| messages 条数 | 6（system / user / assistant / tool / tool / assistant） |
| 估算 token | 109，其中 tool 消息 36 |
| tool 消息数 | 2，顺序与 `tool_calls` 一致 |

「查一下我的订单」返回 40 条完整订单，原始 `content` **6798 字符 ≈ 2250 token**；
截断后 **1447 字符 ≈ 455 token**，降了 **80%**，而且仍然是合法 JSON。

五种坏数组，校验器给出的结论（真实输出）：

| 坏数组 | 抓出来的 code |
| --- | --- |
| 缺一条 tool 消息 | `missing_tool_result`（index 指向声明它的那条 assistant） |
| `tool_call_id` 写错 | `orphan_tool` + `missing_tool_result`（两个都报，因为一错必有两处对不上） |
| tool 消息排在 assistant 之前 | `orphan_tool` |
| `content` 塞了 dict | `content_not_str` |
| 同一条回填两次 | `duplicate_tool_result` |

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 工具列表与 content 上限 |
| POST | `/turn` | `{question}` → 完整 messages + 问题列表 + token 估算 |
| POST | `/validate` | `{messages}` → 校验结论（能直接喂别人的数组进来） |
| GET | `/broken` | 五种坏数组各自被判定成什么 |
| POST | `/truncate` | 试一次截断，同时给出「信封」与「朴素切片」的对比 |

## 常见坑

**1. `content` 必须是字符串。** 塞 dict 进去，有些 SDK 会用 `str(dict)` 帮你转，产出
`{'city': '上海'}`（单引号）—— 那不是 JSON，下游 `json.loads` 必炸。永远自己 `json.dumps(ensure_ascii=False)`。

**2. 漏回填一条，比回填错更常见。** 模型一次返回 2 个 `tool_calls`，你只回填了 1 条，
真模型直接 400：`messages with role 'tool' must be a response to a preceding message with 'tool_calls'`。
所以 `append_tool_results()` 在缺结果时**直接抛 ValueError**，而不是静默发出去。

**3. `tool_call_id` 对不上时，报错信息不会告诉你哪条错了。** 官方报错只说「有一处不匹配」。
`validate_messages()` 会给到 `[3] orphan_tool: tool_call_id=call_x 找不到前面声明它的 assistant` —— index 和 id 都有。

**4. tool 消息的顺序无所谓，数量有所谓。** 两条 tool 消息谁先谁后都能过（按 id 匹配），
但少一条就过不去。自检里专门把顺序颠倒了一次，验证这一点。

**5. 截断不能切片。** `content[:1200]` 得到的是半个 JSON，模型拿到的是不可解析的字符串。正确做法是包信封：`{"truncated": true, "original_chars": 6798, "preview": "..."}` —— 结构完整，模型知道「这里有东西被拿掉了」，就不会拿半截数据编答案。

**6. `truncated` 这种自定义字段不能发给模型。** 它是给前端和日志看的，`strip_local_fields()` 在发请求前剔掉 —— 某些 SDK 对消息里的未知字段是严格校验的。

## 验收清单

- [ ] `uv run python main.py` → 59 通过 / 0 失败
- [ ] 页面上点「回填」前，数组断在 assistant 的 `tool_calls` 上
- [ ] 点「回填」后出现两条 tool 消息，校验通过
- [ ] 点「试超大结果」，看到「已截断」标记与 token 数下降
- [ ] 点「五种坏数组」，缺一条那条的 index 指向 assistant 而不是 tool
- [ ] 能说出 `str(dict)` 和 `json.dumps` 的区别（单引号 vs 双引号）

## 下一单元

Unit 34 把这个「一轮」包进 `while` 循环：什么时候继续、什么时候收尾、`max_steps` 怎么兜底，
以及怎么让 trace 能一步步回放。
