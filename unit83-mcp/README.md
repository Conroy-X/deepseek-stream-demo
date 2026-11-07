# Unit 83 · MCP 集成：把 MCP 工具挂进 Research Agent

> 目标：**从「理解 MCP 协议」到「用 MCP 造出一个能干活的 Agent」**——工具发现、调用、留痕、组装。

---

## 一、三层结构

```
MCP Server          MCP Client              Research Agent
──────────          ──────────              ──────────────
tools/list    ←──   connect() 发现工具
tools/call    ←──   invoke() 调用 + 留痕  ←──  research(question)
```

| 层 | 职责 |
| --- | --- |
| **Server** | 注册工具（名字 + 描述 + JSON Schema）、参数校验、执行 |
| **Client** | 连接时发现工具、调用前检查是否发现过、每次调用留痕 |
| **Agent** | 编排：检索 → 抓取 → 汇总，产出带结构的报告 |

## 二、三个容易漏的工程细节

### 1. 参数校验在 Server 侧，不信任 Client

```python
required = tool.schema.get("required", [])
missing = [r for r in required if r not in args]
if missing:
    return {"ok": False, "error": f"缺少必填参数 {missing}"}
```

Client 可能传错，模型更可能传错——校验必须落在执行前。

### 2. 工具异常不能炸掉整个调用

```python
try:
    return {"ok": True, "result": tool.fn(**args)}
except Exception as exc:
    return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
```

工具抛异常是常态（网络、参数、权限），要转成结构化错误让 Agent 决定重试还是换路。

### 3. 每次调用都留痕（成功和失败都记）

```python
self.trace.append({"tool": name, "args": args, "ok": result["ok"]})
```

没有 trace，Agent 出错时你只知道「失败了」，不知道「哪个工具、什么参数、为什么」。

## 三、怎么跑

```bash
cd ~/ai-journey/unit83-mcp
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8083
curl "http://127.0.0.1:8083/tools"                    # 工具发现
curl -G --data-urlencode "q=年假政策" "http://127.0.0.1:8083/research"
```

## 四、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] `tools/list` 返回 2 个工具且带 schema
- [ ] 缺必填参数 / 未知工具 / 工具内部异常都返回结构化错误
- [ ] Client 未 connect 时拒绝调用
- [ ] Agent 完成「检索 → 抓取 → 报告」，trace 记录 2 次调用

## 五、面试追问

**Q: MCP 和直接写函数调用有什么区别？**
→ MCP 是**协议**：工具由 server 声明 schema，client 运行时发现。
好处是工具可跨进程/跨语言复用，换个 host（Claude Desktop / IDE / 自己的 Agent）
不用改代码。直接函数调用是硬编码的。

**Q: 工具返回错误，Agent 该怎么办？**
→ 三类处理：可重试（网络超时）→ 退避重试；参数错 → 修正参数或放弃；
权限错 → 上报给用户。关键是错误要**结构化**（带 error 类型），否则 Agent 只能瞎猜。

**Q: 为什么要限制 Agent 能用的工具？**
→ 最小权限（Unit 69）。工具越多，模型选错的概率越大，攻击面也越大。
生产里按角色下发不同的工具集。

---

## 六、资料

- [MCP 官方文档](https://modelcontextprotocol.io/)
- [LangChain MCP](https://docs.langchain.com/oss/python/langchain/mcp)
- [Unit 46–59 MCP 协议详解](../../unit46-mcp-intro/)
