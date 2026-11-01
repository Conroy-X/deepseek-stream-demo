# Unit 53 笔记 · Agent + MCP

> 写到一半我让 Agent 死循环了 —— 它每轮都重发 tool_calls，从不给最终答案。
> 教学价值很高：模型必须知道「tool_result 回来了，该收尾了」。

## 本次做了什么

- 写 `server.py`：MCPServer，两个工具（list_inventory / search_orders）。
- 写 `agent.py`：MCPClient + `mcp_tool_to_openai()` + OfflineModel + AgentLoop。
- 写 `main.py`：4 接口 + 52 自检，0 失败，连跑两次一致。

## 一、MCP tool ↔ OpenAI function tool 字段对照

| 字段 | MCP | OpenAI |
| --- | --- | --- |
| 工具名 | `tool.name` | `function.name` |
| 描述 | `tool.description` | `function.description` |
| 参数 schema | `tool.input_schema` | `function.parameters` |
| `type` | 顶层 `"object"` | `parameters.type` |
| `properties` | 顶层 | `parameters.properties` |
| `required` | 顶层 | `parameters.required` |

**唯一差别在最外层壳**：MCP 是扁平的 `{name, description, inputSchema}`，
OpenAI 是 `{type: "function", function: {name, description, parameters}}`。

properties / required 完全一致，所以转换是无损的。这就是为什么 Agent 代码可以原封不动接 MCP server：
你的 function-calling 调度逻辑不用改，只要换工具来源。

## 二、那次死循环

第一版 OfflineModel.respond() 只看最后一条 user 消息，根据关键词发 tool_calls。
问题是：tool_result 回填后，最后一条是 tool 角色，但模型还在找 user 消息 ——
于是它又看到原问题，又发一次 tool_calls，无限循环到 max_steps。

修复：respond() 先看 messages[-1].role，如果是 tool，强制给最终答案：

```python
if messages[-1]["role"] == "tool":
    return {"role": "assistant", "content": f"根据查询结果：{messages[-1]['content']}"}
```

这条规则对应真实 LLM 的行为：模型看到 tool message 时，应该把它解读为「工具跑完了，现在该总结」。
你的规则模型必须模仿这个状态机，否则就死循环。

## 三、arguments 是字符串，不是 dict

真实 OpenAI API 的 `tool_calls[0].function.arguments` 是 **JSON 字符串**，不是 dict：

```python
"tool_calls": [{
    "id": "call_1",
    "type": "function",
    "function": {
        "name": "list_inventory",
        "arguments": "{\"sku\": \"A-100\"}"   # ← 字符串
    }
}]
```

为什么是字符串：OpenAI API 在生成 tool_calls 时用的是 token-level 文本生成，
模型逐字符产出 JSON 文本，不可能直接产出 dict。客户端必须自己 `json.loads()`。

很多教程把它写成 dict，在某些客户端会崩。本单元用 `json.dumps(args, ensure_ascii=False)` 生成。

## 四、tool message 必须带 tool_call_id

```python
self.messages.append({
    "role": "tool",
    "tool_call_id": call["id"],
    "name": name,
    "content": result.text,
})
```

OpenAI 规范要求 tool message 必须有 `tool_call_id` 与触发它的 tool_call 配对。
缺了，下次 API 调用会被拒（"tool message without matching tool_call"）。

## 五、Agent Loop 的 4 步

```
1. user: "查 A-100 库存"
2. assistant: tool_calls=[{name: list_inventory, arguments: '{"sku":"A-100'}'}]
3. tool: tool_call_id=call_1, content="A-100 机械键盘 库存 12 件 单价 ¥499"
4. assistant: "根据查询结果：A-100 机械键盘 库存 12 件 单价 ¥499"
```

4 步、2 次模型调用。本单元的 OfflineModel 就按这个走。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 徒手转换 schema | `mcp_tool_to_openai()` 函数 + 7 条字段断言 |
| 解释 arguments 为何是字符串 | notes 一节 + 自检断言 `isinstance(arguments, str)` |
| 两轮内拿到答案 | 4 步 / 2 次模型调用，断言过 |
| 离线可自检 | 52 条断言，0 失败，连跑两次一致 |

## 一句话总结

**MCP 和 OpenAI function-calling 是同一个 schema 的两种壳 —— 转换无损，死循环靠状态机救。**

## 面试追问

**Q：为什么 OpenAI 的 arguments 是字符串而不是 dict？**
A：因为模型是 token-level 文本生成器，它逐字符产出 JSON 文本，没法直接产出 dict 对象。
客户端拿到字符串后自己 `json.loads`。这也意味着你写工具时，参数解析失败的处理必须做：
模型可能产出非法 JSON，你得接住并给可读错误。

**Q：Agent 怎么避免死循环？**
A：两个机制。一是 max_steps 硬熔断（本单元是 5）。二是状态机：
模型看到 tool message 时必须切换到「总结」状态，不能再发 tool_calls。
真实 LLM 通过训练学到了这个，规则模型得自己写。