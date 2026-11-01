# Unit 55 · MCP 数据库 Agent MVP

> 四环：schema 注入 → 生成 SQL → 只读执行 → 结果转人话。这一章把四环拧成一条能跑的线。
> 后面 56/57/58/59 每一章只加固其中一环，最后 60 收口。

**完成标准**：能说出这四环各自的输入输出；能解释为什么 `run_readonly_sql` 的失败要放进
`result` 的内容里而不是 `raise`；能徒手把 `data/shop.db` 的四张表连起来算一次区域×产品聚合。

## 快速开始

```bash
ssh wsl-debian
cd ~/ai-journey/unit55-mcp-sql-mvp
/home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --port 8055
```

打开 http://127.0.0.1:8055/ 。输入框里默认是「上月华东区销售额前 3 的产品是哪些？」，
点「提问」看四环依次点亮。下面是生成的 SQL、答案、MCP 往返耗时。

## 自检

```bash
/home/rik/.cache/aj/runner/bin/python main.py
# 结果：63 通过 / 0 失败
```

八组：握手、schema 注入、只读执行（数字硬核对）、只读连接挡写、截断、Agent 端到端、HTTP 层。
**自检真的起 stdio 子进程**（不是 mock）：16 次工具调用塞进一条连接，实测 701–845 ms 跑完，
整份自检约 10 秒。不联网、不需要 API key。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `db.py` | 建库与种子数据（12 客户 / 15 产品 / 30 订单 / 60 明细），`ro_connect()` 只读连接 |
| `server.py` | MCP 服务端：`list_tables` / `describe_table` / `run_readonly_sql` + 一个 DDL resource |
| `client.py` | `stdio_client` + `ClientSession` 同步封装，`call_many()` 一条连接跑多条 |
| `agent.py` | `SchemaMap` + `OfflineModel`（规则模型）+ `Agent.ask()` 工具循环；`chat_api()` 被环境变量挡着 |
| `main.py` | 7 个接口 + 63 条自检 |
| `static/index.html` | 实验台：四环链路、提问、结果表、裸 SQL、工具清单 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 自检断言 | 63 条，连跑两次都 0 失败 |
| 全量自检耗时 | 9.8 s（其中 HTTP 部分含 6 次真实 stdio 往返） |
| 单次 stdio 往返 | 约 0.7 s（起进程 + 握手）；同连接内后续调用约 1–3 ms |
| 16 次工具调用（一条连接） | 701 ms / 845 ms（两次） |
| 建库行数 | customers 12 / products 15 / orders 30 / order_items 60，共 117 行 |
| 华东 2026-10 已完成营收 | 20477 元 |
| 前三名 | 4K 显示器 MN-27 7596 / 人体工学椅 CH-09 4798 / 机械键盘 KB-01 2394 |
| schema 注入规模 | 5 次 MCP 调用（1 次 list_tables + 4 次 describe_table） |
| 写操作（DROP/INSERT/DELETE） | `OperationalError: attempt to write a readonly database` |
| 多语句 `SELECT 1; DROP TABLE x` | `ProgrammingError: You can only execute one statement at a time.` |
| `PRAGMA table_info(...)` | **放行** —— 只读连接不管 PRAGMA，第 57 章要显式拦 |

期望值不是「拿 SQL 查一遍再填进去」：`7596 / 4798 / 2394 / 20477` 是拿**纯 Python** 遍历
`db.ORDER_ITEMS` 累加出来的，和 SQL 路径互不依赖。种子数据改了，两边会一起变红。

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 真起一次子进程握手，返回服务名、协议版本、工具名 |
| GET | `/api/tools` | 工具清单与 `inputSchema`（从活连接读，不是常量） |
| GET | `/api/schema` | 注入用的精简 schema 文本与字符数 |
| POST | `/api/sql` | `{sql, max_rows}` → 列名、行、`elapsed_ms`、`roundtrip_ms` |
| POST | `/api/ask` | `{question, today}` → 答案 + SQL + rows + trace |
| GET | `/api/trace` | 上一次 `/api/ask` 的完整轨迹 |
| GET | `/` | 实验台页面 |

## 常见坑

1. **`command` 写成 `"python"`**。非交互 shell 里拿到的是系统 python，没有 `mcp`，子进程秒退，
   客户端只报「连接关闭」。一律用 `sys.executable` 的绝对值。
2. **每次调用起一个子进程**。0.7 秒 × 16 次 = 11 秒，自检能跑到你怀疑人生。
   `call_many()` 一条连接跑完，实测省了 90%。
3. **工具里 `raise`**。SDK 会把异常脱敏成 `Error executing tool xxx`，模型只知道「失败了」，
   不知道「哪张表不存在」。本单元所有工具都 `try/except` 后把原因写进正常返回值，
   `is_error` 保持 `False`。
4. **`arguments` 当成 dict**。OpenAI 与 MCP 的 `tool_calls[].function.arguments` 都是 **JSON 字符串**，
   `json.loads` 之后才能用。写成 dict 在真实 API 上会 400。
5. **`asyncio.run` 包 `anyio`**。SDK 内部是 anyio task group，混用会在关闭阶段报
   「Attempted to exit cancel scope in a different task」。同步入口只用 `anyio.run`。
6. **以为只读连接什么都能拦**。它能拦写，但 `PRAGMA`、`ATTACH`、大结果集它一概不管 ——
   这正是第 57 章要补四道闸的原因。
7. **期望值和自己对账**。用 SQL 生成答案、再用同一条 SQL 验证答案，永远绿 —— 也永远没验到东西。
   本单元的数字来自独立实现（纯 Python 聚合）。

## 验收清单

- [ ] `python main.py` 输出 `结果：63 通过 / 0 失败`，连跑两次一致
- [ ] 页面上提问后，四环全部点亮，SQL 与答案同时出现
- [ ] 点「问不出来的问题」，看到「无法回答」且**没有**生成 SQL
- [ ] 点「试写操作」，看到 `ok=False` 但 `is_error` 仍是 false
- [ ] 点「试多语句」，看到 `one statement at a time`
- [ ] 能不看代码画出四条表之间的关系（orders.customer_id → customers.id 等）
- [ ] 能回答：为什么 schema 要走 MCP 工具而不是直接读 `sqlite_master`

## 下一单元

Unit 56 · Schema 注入与方言适配 —— 同样是这套库，模型看到的东西换个写法，准确率就变了。
三种 schema 表示的字符数差多少、`LIMIT` 在四种方言里怎么写、`customer_id` 和 `id` 谁是谁，
都要有数字。
