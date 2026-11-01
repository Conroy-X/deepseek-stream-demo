# Unit 55 笔记 · 数据库 Agent 的最小闭环

> 一个能用的 NL2SQL Agent 只有四环。难点不在「让模型写 SQL」，
> 而在**它写错的时候你怎么知道、又怎么让它知道**。

## 本次做了什么

- `db.py`：建了 `data/shop.db`，四张表 117 行（12 客户 / 15 产品 / 30 订单 / 60 明细），
  账期锚点写死 `2026-11-01`，`reset_db()` 每次自检前重建。
- `server.py`：MCP 服务端，三个工具 + 一个 `sql://schema/full` resource。
- `client.py`：`stdio_client` + `ClientSession` 同步封装，提供 `call_many()` 批调用。
- `agent.py`：`SchemaMap`（schema 注入）、`OfflineModel`（规则模型，产出标准 OpenAI message）、
  `Agent.ask()`（工具循环）、`chat_api()`（真 DeepSeek，被 `DEEPSEEK_API_KEY` 挡着）。
- `main.py`：7 个接口 + 63 条断言，连跑两次 0 失败。
- `static/index.html`：四环链路可视化 + 提问 + 结果表 + 裸 SQL 执行。

## 技术要点

### 1. 一次 stdio 往返 0.7 秒，但同连接内只要 1–3 毫秒

实测：`call_many()` 里 16 次工具调用共 701 ms / 845 ms（两次）。
如果每次 `call_tool` 都新起会话，同样 16 次要 11 秒左右。**结论**：会话是资源，不是请求。

### 2. schema 注入 = 5 次工具调用

`list_tables` 1 次 + `describe_table` 4 次。这些信息本来可以 `SELECT * FROM sqlite_master` 一把拿，
但走工具的好处是**服务端可以决定给什么**：第 56 章会演示只给相关表（字符数从 1200 降到 300 量级）。
### 3. 期望值必须来自另一条路

三个数字：7596 / 4798 / 2394，加上总营收 20477。
它们是用**纯 Python 遍历 `ORDER_ITEMS` 累加**算出来的，和 SQL 路径完全独立。
如果用「先跑 SQL 看结果、再把结果写进断言」，这个自检永远绿，也永远没验到东西。

### 4. 只读连接拦得住写，拦不住 PRAGMA

```
DROP TABLE customers   → OperationalError: attempt to write a readonly database
INSERT ...             → 同上
SELECT 1; DROP TABLE x → ProgrammingError: You can only execute one statement at a time.
PRAGMA table_info(...) → 放行
```

第三条不是安全防线，只是 Python 驱动的顺手行为（`execute` 只吃一条语句）。
第四条说明：**连接权限解决「能不能写」，不解决「能读多少、读什么」** —— 那是第 57 章的事。

### 5. 工具内 `raise` 会让模型变瞎

`mcp 2.3.0` 实测：工具抛 `RuntimeError("故意炸")`，客户端拿到 `is_error=True, text="Error executing tool boom"`，
完整 traceback 只在服务端 stderr。所以三个工具全部 `try/except` 后返回 `{"ok": false, "error": "..."}`，
`is_error=False`。代价是协议层看不到失败 —— 用 `ok` 字段和结构化日志补（第 59 章）。

### 6. 与 `docs/mcp-sdk-2x-notes.md` 不一致的一处（重要）

事实卡第 5 节「坑二」说：调用不存在的工具、参数不对，客户端会**抛 `ToolError`**。在 `mcp 2.3.0` 上实测**不抛**：

```
call_tool("nope", {})        → is_error=True, text="Unknown tool: nope"
call_tool("add", {"a": 1})   → is_error=True, text="Error executing tool add: 1 validation error ... b Field required"
```

也就是说，2.3.0 把这两类失败也收敛成了 `is_error=True` 的**正常返回**。
好消息是 Agent 主循环不用额外 `try/except ToolError`；坏消息是参数校验文本（含 pydantic 字段路径）
会原样透给模型，对外做产品时要自己截断。

### 7. `OfflineModel` 的 message 形状

```python
{"role":"assistant","content":"我来查一下：SELECT ...",
 "tool_calls":[{"id":"call_1","type":"function",
                "function":{"name":"run_readonly_sql","arguments":"{\"sql\": \"...\"}"}}]}
```

`arguments` 是**字符串**（真实 API 的行为），写成 dict 在 DeepSeek/OpenAI 上直接 400。

## 完成标准对照

| 要求 | 做到没有 | 证据 |
| --- | --- | --- |
| 四张表，字段贴近真实 | ✅ | `db.py` 有金额/日期/状态/地区，117 行 |
| `list_tables` / `describe_table` / `run_readonly_sql` | ✅ | `server.py`，自检断言工具名集合 |
| 只读 URI 连接 | ✅ | `ro_connect()` 用 `mode=ro`，DROP/INSERT 都报 readonly |
| 自然语言 → SQL → 执行 → 人话 | ✅ | `Agent.ask()` 2 轮，答案含 7596 / 4798 / 2394 |
| 数字与直接查库一致 | ✅ | 期望值来自纯 Python 聚合，硬写进断言 |
| 每环都有断言 | ✅ | 握手 7 / schema 10 / 执行 14 / 只读 6 / 截断 3 / Agent 14 / HTTP 9 = 63 |
| 离线、无 key | ✅ | 自检 0 网络；`chat_api()` 无 key 直接 raise |

## 一句话总结

**NL2SQL 的成败不在生成，而在「schema 给得准不准」和「错了以后模型能不能看见原因」。**

## 面试追问

**Q：为什么不让模型直接连数据库，非要套一层 MCP？**
A：三个理由。一是**权限收口**：凭据留在服务端，模型只看到三个语义化工具，拿不到 DSN；
二是**可替换**：换 Postgres 只改 `server.py`，Agent 一行不动；三是**可观测**：每次调用都过一次
工具边界，日志、超时、限流都能挂在这条线上。反过来，本地脚本查一次 CSV 就套 MCP 是纯开销。

**Q：schema 放系统提示词里，表多了怎么办？**
A：会撑爆。30 张表 × 8 列的全量 DDL 约 3–4k token，100 张表就上万，而且大部分列与问题无关。
做法是两步检索：先按关键词挑 3–5 张候选表，再把它们的列塞进提示词。第 56 章实测差 4 倍字符数。

**Q：模型生成的 SQL 有 90% 是对的，剩下 10% 怎么办？**
A：分两层。第一层是**执行前**：只读连接 + 语句白名单 + 强制 LIMIT（第 57 章），
错 SQL 最多查不出来，不会毁数据。第二层是**执行后**：把 SQLite 的原始报错回灌给模型让它改，
最多 3 次（第 58 章）。关键是第二层必须能看见**原始错误文本**，所以工具不能 `raise`。

**Q：怎么证明这个 Agent 的回答是对的？**
A：不能靠「看起来对」。本单元把期望值从另一条路径算出来（纯 Python 聚合）写死进断言；
生产里则是**留痕**：每次回答把 SQL、行数、耗时、trace_id 一起落库，抽样人工核对 SQL 而不是核对答案。
答案可以读起来很顺，SQL 不会骗人。
