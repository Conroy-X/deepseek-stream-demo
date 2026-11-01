# Unit 31 笔记 · 第一个 Tool Call

> 模型没有执行任何函数。它只输出了一段「我想调 get_weather，参数是 {"city":"上海"}」的 JSON，真正干活的是你的代码。

## 本次做了什么

- `tools.py`：两个工具（`get_weather` / `calculate`）、严格的 `validate_args()`、执行返回结构化 `ToolResult`、以及一个**不用 `eval`** 的安全计算器。
- `main.py`：`OfflineModel`（规则版模型，输出标准 OpenAI 格式）+ `run_once()` 四步循环 + 4 个接口 + 30 条离线自检。
- `static/index.html`：trace 可视化（assistant → tool → assistant）、工具 schema 展示、几个一键测试。

## 一、一次工具调用的真实形状

```
用户: 北京今天天气怎么样
  ↓
assistant: content=null, tool_calls=[{id:"call_1", function:{name:"get_weather", arguments:"{\"city\":\"北京\"}"}}]
  ↓  ← 这一行是你写的代码，不是模型
tool: role="tool", tool_call_id="call_1", content="{\"city\":\"北京\",\"text\":\"晴\",\"temp_c\":9}"
  ↓
assistant: "北京今天晴，9°C，湿度 35%。"      ← rounds=2，结束
```

三个必须记住的字段：`tool_calls[].id`（回填时要对上）、`function.name`、`function.arguments`（**是字符串，不是对象**）。
`arguments` 是 JSON 字符串这件事坑了很多人：它可能是空串、可能是被截断的 JSON、可能是 `{"city":"上海"} 多余的废话`。
所以第一步永远是 `json.loads` 加 `try`，而不是直接当 dict 用。

实测：这条链路 `rounds=2`（第一轮决定调工具，第二轮出自然语言答案）。
不是 3 也不是 1 —— 「调用一轮 + 回答一轮」是最常见的形状。

## 二、四步循环，一步都不能省

```python
messages.append(model_response)              # 1. 把 assistant(tool_calls) 原样追加回去
result = execute(name, args)                 # 2. 自己执行
messages.append({"role": "tool",             # 3. 回填，tool_call_id 必须对应
                 "tool_call_id": tc.id, "content": json.dumps(result)})
continue                                     # 4. 再问一次模型
```

四条实测出来的规矩：

1. **assistant 消息必须原样追加**，包括 `tool_calls` 字段。只追加 `content`（往往是 `null`）会导致模型
   无法把后面的 tool 消息和请求对应起来，很多实现直接 400。
2. **`tool_call_id` 必须对上**。对不上就是 `Invalid parameter: messages with role 'tool' must be a response to a preceding message with 'tool_calls'`。
3. **`content` 必须是字符串**。把 dict 直接塞进去，SDK 会序列化成 `{'city': '北京'}`（单引号），下游解析必炸。
4. **必须有轮次上限**。自检里模拟了一个「永远要求调工具」的模型，`MAX_ROUNDS=3` 到顶就停，
   返回一句「已达到工具调用轮次上限」。没有这个上限，一次请求能把你的账单刷到天亮。

## 三、校验是必须的，因为参数来自模型

`validate_args()` 是**严格模式**：未定义的参数一律拒绝，不做「猜测性兼容」。

实测被拒的四种：

| 输入 | 结果 |
| --- | --- |
| `{}` | 缺必填参数 `city` |
| `{"city": 123}` | 类型不对 |
| `{"city": "杭州"}` | 枚举外（只允许 上海/北京/广州/深圳） |
| `{"city":"上海","unit":"K"}` | 未定义参数 `unit`，严格模式拒绝 |

为什么要严格到「多余参数也拒绝」：宽松模式会把 `{"city":"上海","admin":true}` 悄悄放过去，
而工具实现里某个 `**kwargs` 可能正好吃下 `admin`。这类漏洞不会报错，只会某天变成事故。

## 四、计算器：`eval` 是这一章最容易写出的 RCE

```python
_ALLOWED_OPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.USub, ast.UAdd)
```

用 `ast.parse(expr, mode="eval")` 走一遍语法树，只允许数字和四则运算节点，其他一律拒绝。
`eval("__import__('os').system('rm -rf /')")` 这种就不用说了，连 `sum([1,2])` 都不该过。

实测被拒：`import os`、`__import__("os")`、`abs(-1)`（函数调用）、`x + 1`（变量名）、`9**9**9`（超大指数，会卡死 CPU）。

最后一条特别值得记：即使语法合法、运算也"安全"，`9**9**9` 依然能让进程挂住几十秒。
所以除了白名单，还要有**数值上限**（本单元卡了指数和结果大小）。这是「安全检查 ≠ 语法检查」的典型例子。

## 五、工具报错是一条消息，不是异常

「杭州今天天气」的实测结果：

```
answer: 工具报错：参数 city 只能取 ['上海', '北京', '广州', '深圳']，收到 '杭州'。
rounds: 2
```

链路没有断：校验失败 → `ToolResult(ok=False, error=...)` → 作为 `role=tool` 的内容回填 → 模型据此组织人话。

反过来做（把异常抛给用户 / 500）会有两个后果：用户看到的是「服务器错误」而不是「这个城市我查不了」；
模型也没有机会换个说法或者换个参数重试。**错误是对话的一部分**，这是 Tool Calling 和普通函数调用的最大区别。

## 六、离线怎么测

`OfflineModel` 是规则版：识别城市名、识别算术表达式，返回标准 OpenAI 格式的 `tool_calls`。

这样做的理由：真实模型的输出不稳定，而本章要验证的是**循环、校验、回填、上限**这些确定性逻辑。
`chat_api()` 保留了一个真实的 OpenAI 兼容实现（httpx 直连），配了 `OPENAI_BASE_URL` / `OPENAI_API_KEY` 就能切过去，
但自检永远跑离线那一套 —— 否则 CI 会随机红。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 说清「谁在执行」 | 模型只产出 JSON，执行在 `execute()`；trace 里能看见两类消息 |
| 四步循环写对 | assistant(tool_calls) → tool(tool_call_id) → 再问；自检 8 条 |
| 参数校验严格 | 缺参/类型/枚举/多余参数四类都拒；有断言 |
| 计算器不 RCE | ast 白名单 + 数值上限，5 条拒绝用例 |
| 工具错误可对话 | 「杭州」实测回一句人话，`rounds=2` |
| 有轮次上限 | `MAX_ROUNDS=3`，死循环模型触顶即停 |
| 离线可自检 | 30 条断言，0 失败 |

## 一句话总结

**Tool Calling 的可靠性不在模型聪不聪明，而在你的代码敢不敢假设「模型给的 JSON 一定是坏的」。**

## 面试追问

**Q：为什么不让模型直接执行工具（比如给它一个 Python 解释器）？**
A：因为权限一旦交出去就收不回来。模型能执行代码，就意味着它能读环境变量、发网络请求、删文件，
而它的输入里混着不可信的文档和用户文本（见 Unit 29 的提示注入）。正确的边界是：
模型只能**提议**调用哪个工具、用什么参数；能不能调、参数合不合法、结果要不要脱敏，全在你的代码里判。

**Q：工具结果要不要做摘要再回填？**
A：看大小。Tool 消息会进上下文，一个返回 5000 字的工具（比如搜索结果）会挤掉后面的历史。
常见做法是：小结果原样回填（保真，模型能引用具体数字），大结果先截断或摘要，并在 content 里注明「已截断」。
本单元的结果都极小，所以原样回填 —— 但要知道这是个需要显式决策的点，不是默认安全。

**Q：并行工具调用怎么处理？**
A：模型一次可能返回多个 `tool_calls`。此时要**并行执行**，然后按 `tool_call_id` 逐个回填**多条** tool 消息
（顺序无所谓，但每条都要有对应的 id）。串行执行不是错，只是慢；漏回填其中一条就必然报错。
本单元只演示单次调用，但 `run_once` 的循环结构已经能直接扩展成 for 循环。

**Q：`arguments` 为什么是字符串而不是对象？**
A：因为它是模型**流式生成**出来的 token 序列，天然是文本。有些 SDK 会替你做 `json.loads` 并抛错，
有些（尤其流式场景）会把半截 JSON 给你。工程上的稳妥做法是：永远自己解析，失败时把原始字符串
（截断后）作为错误信息回填给模型 —— 模型经常能自己修正成合法 JSON。
