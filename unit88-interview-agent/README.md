# Unit 88 · 面试：Agent + MCP（30 题）

> 目标：**30 道 Agent/MCP 高频题讲得清。** 计划要求重点考工具设计、状态机、错误恢复。

---

## 一、题库结构

| 板块 | 题号 | 覆盖 |
| --- | --- | --- |
| Agent 基础 | 1–8 | Agent 本质、Function Calling 流转、死循环、ReAct、工具设计、选错工具、状态、终止条件 |
| 工具与权限 | 9–14 | 权限、幂等、结果过大、错误处理、沙箱、审计 |
| MCP | 15–22 | 解决什么、三层架构、三原语、和 REST 区别、生命周期、传输、安全、多 server |
| 编排与记忆 | 23–27 | LangGraph 核心、子图、并行合并、记忆分类、HITL |
| 优化与评测 | 28–30 | 成本、延迟、Agent 评测 |

## 二、三个必须能画出来的东西

### 1. Function Calling 消息流转

```
user → assistant(tool_calls) → tool(result) → assistant(answer)
```

### 2. MCP 三层

```
Host（宿主：Claude Desktop / IDE / 你的 Agent）
  └─ Client（连接）──stdio/SSE──> Server（提供 Tools/Resources/Prompts）
```

### 3. Agent 循环的终止条件

目标达成 / 步数预算耗尽 / 不可恢复错误——三者任一即停。

## 三、怎么跑

```bash
cd ~/ai-journey/unit88-interview-agent
uv sync
uv run python main.py

uv run uvicorn main:app --port 8088
curl "http://127.0.0.1:8088/bank"                # 30 题
curl "http://127.0.0.1:8088/bank?topic=mcp"      # 只看 MCP
```

## 四、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 能默画 Function Calling 消息流转
- [ ] 能说清 MCP 三层和三原语
- [ ] 能说 Agent 的三个终止条件
- [ ] 弱项回去补（Agent → Unit 31–36，MCP → Unit 46–59，LangGraph → Unit 37–40/82）

## 五、面试追问

**Q: 「你的 Agent 怎么防止跑飞？」**
→ 三重保险：步数预算（硬上限）、工具去重（同参数不重复调）、
终止条件明确（达成/耗尽/不可恢复）。

**Q: 「MCP 相比直接调 API 优势在哪？」**
→ 自描述（schema 运行时发现）+ 跨 host 复用 + 面向模型设计。
代价是多一层协议和连接管理。

---

## 六、资料

- [MCP 官方文档](https://modelcontextprotocol.io/)
- [Unit 46–59 MCP 协议](../unit46-mcp-intro/)
- [Unit 82 LangGraph 优化](../unit82-langgraph/)