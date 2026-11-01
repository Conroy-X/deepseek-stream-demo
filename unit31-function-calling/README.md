# Unit 31 · Function Calling

> 模型不执行任何东西。它只回一段 `tool_calls`，真正的活是你干的。

**完成标准**：能说清 `tool_calls → 执行 → role=tool 回填` 这条闭环，并知道每一步会在哪炸。

## 快速开始

```bash
cd ~/ai-journey/unit31-function-calling
uv sync
uv run uvicorn main:app --reload --port 8031
```

打开 <http://127.0.0.1:8031/>：问「上海今天天气怎么样」，能看到完整的三段 trace
（模型响应 → 工具执行 → 最终回答）。默认走离线规则模型，不花钱。

## 自检

```bash
uv run python main.py
```

30 项，全离线：参数校验（缺参/类型错/枚举外/多余字段/未知工具）、
计算器安全（拒绝 import / 函数调用 / 超大指数）、`tool_call_id` 对应、
失败回填、最大轮次保护、HTTP 层。

## 这个目录里有什么

| 文件 | 说明 |
| --- | --- |
| `tools.py` | 两个工具的 JSON Schema + `validate_args` + 安全执行 |
| `main.py` | `OfflineModel`（规则假模型）+ `run_once`（Agent 循环）+ 30 项自检 |
| `static/index.html` | 一次问答的完整 trace 可视化 |
| `.env.example` | 接真模型时才需要填 |

## 闭环就这四步

```python
msg = model.complete(messages)                    # 1. 模型返回 tool_calls
messages.append({"role": "assistant", "tool_calls": msg["tool_calls"]})   # 2. 先入历史
result = execute(name, args)                      # 3. 你执行
messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})  # 4. 回填
```

**第 2 步是最容易漏的**：不把 assistant 那条带 `tool_calls` 的消息放进历史，
后面 `role=tool` 的 `tool_call_id` 就找不到对应项，真模型直接 400。

## 三个必须自己拦的地方

**1. 参数校验（在 execute 之前）**

模型给的 JSON 是"生成"出来的，不是"保证"的。它可能少字段、类型错、
传个枚举外的值，甚至多塞一个字段。

```python
validate_args("get_weather", {"city": 123})          # ToolError: 应为 string
validate_args("get_weather", {"city": "杭州"})        # ToolError: 只能取 [...]
validate_args("get_weather", {"city": "上海", "x": 1}) # ToolError: 未定义参数
```

多余字段我选择**直接拒绝**而不是忽略 —— 模型多传字段，通常说明它误解了工具用途，
静默忽略会让你永远发现不了。

**2. 计算器绝不能用 eval**

```python
ast.parse(expr, mode="eval")   # 只允许 Constant / BinOp / UnaryOp
```

自检里四条攻击样本全部被拒：`__import__("os")`、`open(...)`、`2**999999`、`a+b`。
用 `eval()` 写这个工具，等于把 RCE 挂在 `/tool` 接口上。

**3. 轮次上限**

模型可能进入"一直要调工具"的循环。`max_rounds=3` 到了就停，
返回 `stopped="max_rounds"` —— 不是报错，是可观测的终止。

## 实测

| 问题 | 轮次 | 结果 |
| --- | ---: | --- |
| 上海今天天气怎么样 | 2 | `get_weather{"city":"上海"}` → 「上海今天多云，18°C，湿度 62%」 |
| (120-30)*0.8 等于多少 | 2 | `calculate{"expression":"(120-30)*0.8"}` → 72.0 |
| 杭州天气怎么样 | 2 | 工具**报错**（枚举外），错误回填后回答「工具报错：…」 |
| 你们公司叫什么名字 | 1 | 不调工具，直接回答 |
| （死循环模型） | 3 | 触顶停止，不会无限调用 |

「杭州」那一行是本单元最重要的演示：**错误不是异常，是一条消息**。
校验失败 → `execute` 返回 `ok=false` 的 ToolResult → 作为 `role=tool` 回给模型
→ 模型据此回答。整条链路不断。

## 接真模型

`main.py` 里已写好 `chat_api()`（httpx 直连 OpenAI 兼容接口），
只要 `.env` 里填了 `DEEPSEEK_API_KEY`，`/chat` 自动切过去；调用失败会降级回离线模型
并在响应里带 `fallback_reason`。

只做一轮工具调用是**故意的**：多轮规划、反思、记忆属于后面的 Agent 章节，
这一章先把"一次调用"做对。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 工具列表、当前模型、轮次上限 |
| GET | `/tools` | 完整 JSON Schema（前端也是读它渲染的） |
| POST | `/chat` | `{question}` → 答案 + trace + 轮次 |
| POST | `/tool` | 直接调一个工具，调试用 |

## 验收清单

- [ ] `uv run python main.py` → 30 通过 / 0 失败
- [ ] 页面上问「上海今天天气怎么样」，看到三段 trace
- [ ] 点「试非法城市」，回答里明确说工具报错而不是编一个温度
- [ ] 点「试算术」，`(120-30)*0.8` 得 72
- [ ] `/tool` 里传 `{"expression": "__import__('os')"}` 被拒
- [ ] 能说出 `tool_call_id` 不对会发生什么（400）

## 下一单元

Unit 32 起进入多轮 Agent：把 `run_once` 变成真正的循环，
加上规划、工具结果反思与失败重试 —— 那时"轮次上限"就从保险丝变成核心机制。
