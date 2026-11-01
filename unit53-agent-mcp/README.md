# Unit 53 · Agent + MCP

> Agent 当 MCP Client。MCP 的 `input_schema` 可以直接当 OpenAI function-calling 的 `parameters` 用 ——
> 两套协议的字段结构完全一致，这就是 Agent 代码不用改就能接 MCP 的关键。

**完成标准**：能徒手把 MCP tool dict 转成 OpenAI function tool；
能解释 `tool_calls[].function.arguments` 为什么是字符串而不是 dict；
能让 Agent 在两轮内拿到答案（user → tool_call → tool_result → final）。

## 快速开始

```bash
cd ~/ai-journey/unit53-agent-mcp
uv sync
uv run uvicorn main:app --port 8053
```

打开 http://127.0.0.1:8053/ 。两块：左 MCP↔OpenAI schema 对照，右 Agent Loop 演示。

## 自检

```bash
uv run python main.py
# 结果：52 通过 / 0 失败
```

四组：schema 转换、工具清单（真子进程）、Agent Loop 端到端、HTTP 层。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `server.py` | MCPServer：list_inventory / search_orders 两个工具 |
| `agent.py` | MCPClient + `mcp_tool_to_openai()` + OfflineModel + AgentLoop |
| `main.py` | 4 接口 + 52 自检 |
| `static/index.html` | schema 对照 + Agent Loop 步进 |

## 实测

| 项目 | 结果 |
| --- | --- |
| MCP tool → OpenAI 转换 | properties / required 字段无损，外层加 function 壳 |
| Agent 「A-100 库存」 | 4 步（user + assistant_call + tool + assistant_final），模型调 2 次 |
| Agent 「c-200 已发货订单」 | search_orders 返回 o-003 |
| Agent 「c-100 所有订单」 | status 默认空字符串 |
| Agent 「未知 SKU X-999」 | 工具返回中文提示「没有这个 SKU」 |
| Agent 「今天天气怎么样」 | 不调工具，模型直接给最终答案 |
| `tool_calls[0].function.arguments` | 是 **字符串**（`json.dumps` 出来的），不是 dict |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 工具名 |
| GET | `/api/tools` | 起子进程拉工具 + 转 OpenAI 形态，对照返回 |
| POST | `/api/ask` | 端到端 Agent Loop |
| GET | `/` | 页面 |

## 常见坑

1. **Agent 死循环**。模型看到 tool_result 后还在发 tool_calls —— 因为 respond() 只看 user 消息，
   忽略了 tool 角色。修复：判断最后一条是 tool 时强制给最终答案。
2. **`arguments` 当 dict 传**。真实 OpenAI API 的 `tool_calls[].function.arguments` 是**字符串**。
   写成 dict 会在某些客户端直接崩，而且模型模仿时也会出错。
3. **tool message 缺 `tool_call_id`**。OpenAI 规范要求 tool 消息必须带 `tool_call_id` 与之配对。
   缺了，下次请求会被 API 拒（"tool message without matching tool_call"）。
4. **多 tool_calls 处理顺序**。一个 assistant 消息可以带多个 tool_calls，
   必须每个都生成对应的 tool message 再喂回，不能漏。
5. **schema 转换丢字段**。`type` 字段 MCP 在顶层，OpenAI 在 `parameters.type`，
   漏掉会让某些客户端把工具当 free-form。

## 验收清单

- [ ] `python main.py` 输出 `结果：52 通过 / 0 失败`
- [ ] 页面左侧 MCP 形态与右侧 OpenAI 形态字段对照正确
- [ ] 「A-100 库存」4 步结束，最终答案含库存数
- [ ] 「今天天气怎么样」不调工具直接给答案
- [ ] 能不看文档说出 arguments 为什么是字符串

## 下一单元

Unit 54 · 多 MCP Server —— 同时挂 2-3 个 server，工具名加命名空间前缀防冲突，并且当一个 server 起不来时剩下仍可用。