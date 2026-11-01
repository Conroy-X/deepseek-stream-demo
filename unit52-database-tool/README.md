# Unit 52 · Database Tool

> 「只读」不是一个动作，是**连接的属性**。
> 词法检查拦不住的写操作，到 `sqlite3.connect("file:...?mode=ro", uri=True)` 这关 SQLite 自己就拒了。

**完成标准**：能说出词法检查与只读连接在「拦写」上的本质差别；
能解释 `SELECT 1; DROP TABLE x` 在只读连接上会怎样；
能徒手把 8 类 SQL 注入样例分到三层拦截里去。

## 快速开始

```bash
cd ~/ai-journey/unit52-database-tool
uv sync
uv run uvicorn main:app --port 8052
```

打开 http://127.0.0.1:8052/ 。最值得动手的是「ro vs rw」对照：同一句 INSERT，
只读连接报 `attempt to write a readonly database`，写连接真写成功（rollback 后行数归位）。

## 自检

```bash
uv run python main.py
# 结果：56 通过 / 0 失败
```

九组：DB 模型、工具清单、正常查询、多语句拦截、注释绕过、词法白名单、强制 LIMIT、ro vs rw、HTTP 层。
每条都起真子进程 / 真连接。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `server.py` | `MCPServer`：3 个工具（list_tables / describe_table / run_readonly_sql）+ 内部 `_try_write_on_ro/rw` |
| `client.py` | `MCPClient` 封装 |
| `main.py` | 6 接口 + 56 自检 |
| `static/index.html` | SQL 实验台 + 攻击样例表 + ro/rw 对照 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 表 | authors(4列/5行) + articles(6列/6行) |
| 工具 | 3 个：list_tables / describe_table / run_readonly_sql |
| 多语句 `SELECT 1; DROP TABLE articles` | 被多语句检查拦下 |
| 行注释 `SELECT * FROM articles -- ; DROP TABLE articles` | 剥注释后正常 SELECT，DROP 不执行 |
| PRAGMA / UPDATE / DELETE / INSERT / ATTACH / DROP / ALTER / CREATE | 8 类全部被词法白名单拦 |
| 无 LIMIT 自动补 | 100 |
| LIMIT >1000 改写 | 1000 |
| 只读连接 INSERT | 拒，错误 `attempt to write a readonly database` |
| 写连接 INSERT | 成功（rollback 后行数 6 → 7 → 6） |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | db、表清单、工具名 |
| GET | `/api/schema` | 表结构 + ro/rw 实测对照 |
| GET | `/api/attacks` | 9 个攻击样例 + 1 个合法对照 |
| POST | `/api/run` | 输入 SQL 看被哪层拦 |
| GET | `/api/mcp` | 真起子进程握手并调一次 |
| GET | `/` | 实验台页面 |

## 常见坑

1. **用 `startswith("SELECT")` 当只读闸**。`SELECT 1; DROP TABLE x` 通过词法，第二句会真执行。
   要么多语句拆开逐条检查，要么直接走只读连接 —— 两者都得做。
2. **不剥注释就检查关键词**。`SELECT 1 -- ; DROP TABLE` 字面上是 SELECT，执行后注释段会被忽略，
   不会出问题。但 `SELECT 1 --\n; DROP TABLE` 这种带换行的注释会让 SQLite 的 executescript 拆分，
   要么剥注释要么限制单语句执行。
3. **`sqlite3.complete_statement` 要分号才返回 True**。用户给的 SQL 通常不带分号 —— 用它当闸会误杀。
4. **`ro.execute()` 不允许多语句**。SQLite Python binding 自己拒，错误是 `You can only execute one statement at a time.`
   —— 这是好事，等于多了一条保险。
5. **PRAGMA 不在只读连接的限制范围**。PRAGMA 本质是元数据查询，但你不希望模型用 PRAGMA 摸底，
   所以白名单也拒它，不能只靠驱动。
6. **写连接回滚不彻底**。本单元测的是「insert 然后立刻 rollback」—— 行数确实归位，
   但 WAL 文件会写日志。生产里用只读连接才能彻底避开副作用。

## 验收清单

- [ ] `python main.py` 输出 `结果：56 通过 / 0 失败`
- [ ] 页面 ro vs rw 对照里，只读连接返回 `readonly`，写连接返回 `6 → 7 → 6`
- [ ] 攻击样例表 9 条全部被拦或正常
- [ ] /api/mcp 起一次真子进程拿到 3 个工具
- [ ] 能不看文档说出：词法检查和只读连接各自负责什么

## 下一单元

Unit 53 · Agent + MCP —— 让 Agent 当 MCP Client。
MCP 的 `input_schema` 可以直接当 OpenAI function-calling 的 `parameters` 用，这是两套协议能对接的关键。