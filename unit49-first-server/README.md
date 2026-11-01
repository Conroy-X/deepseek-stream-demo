# Unit 49 · 第一个 Server

> 前三章手写了三遍协议层，这一章终于装 SDK。你会发现同一批帧变成了十几个装饰器 ——
> 但也会立刻踩到 2.x 的坑：网上的教程 90% 还在写 `FastMCP`，那是 1.x 的东西，已经删了。

**完成标准**：能用 `MCPServer` 写出一个带工具/资源/提示词的 server；
能说清「工具内部异常」和「参数校验失败」这两类失败为什么都得自己接管；
能解释为什么服务端 `print()` 会直接干掉整个连接。

## 快速开始

```bash
cd ~/ai-journey/unit49-first-server
uv sync
uv run uvicorn main:app --reload --port 8049
```

打开 http://127.0.0.1:8049/ 。页面本身就是一个 MCP 客户端 ——
每点一次「调用」都真的拉起一个 stdio 子进程、真握手、真执行，不是 mock。

只想跑 server：

```bash
uv run python server.py      # 直接挂在 stdio 上等客户端
```

> 官方 Inspector（`npx @modelcontextprotocol/inspector`）需要联网装 npm 包，
> 本单元用一个等价的网页客户端代替。两者做的事完全一样：发 `tools/list`、`tools/call`。

## 自检

```bash
uv run python main.py
# 结果：61 通过 / 0 失败
# 耗时约 9.7 秒（要起 8 次真实子进程，慢是正常的）
```

六组：握手与能力声明、工具清单、正常调用、坑一（异常脱敏）、坑二（两类失败的方向相反）、
坑三（一条连接一次握手）、HTTP 层。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `server.py` | `MCPServer`：4 个工具 + 1 个资源 + 1 个提示词 |
| `client.py` | `MCPClient` 封装（`stdio_client` + `ClientSession`），后面几章都抄这份结构 |
| `main.py` | 7 个接口 + 61 条自检 |
| `static/index.html` | 客户端控制台：连接信息、工具表单、坑演示、资源与提示词 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 协议版本（握手返回） | `2025-11-25` |
| 能力表 | `["prompts", "resources", "tools"]` 三类齐全 |
| 工具数 / 资源数 / 提示词数 | 4 / 1 / 1 |
| `add(2, 3.5)` | `text = "5.5"`，`structured_content = {"result": 5.5}` |
| `greet("rik")` | `"你好，rik！"` |
| `safe_div(1, 0)` | `is_error=False`，`text="错误：除数为 0，请换一个非零的除数"` |
| `boom()` | `is_error=True`，`text="Error executing tool boom"` —— **中文原因没传回来** |
| 调不存在的工具 | `is_error=True`，`text="Unknown tool: no_such_tool"`，**不抛异常** |
| 参数类型错 | `is_error=True`，文本含 `addArguments` 与 `errors.pydantic.dev` |
| 一次完整子进程往返 | 约 0.7 秒（起进程 + 握手 + 调用） |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | server 名、协议版本、能力表、三类数量 |
| GET | `/api/snapshot` | 一次连接拿全：serverInfo + 工具 + 资源 + 提示词 |
| POST | `/api/call` | 调工具，返回统一后的 `ToolResult` |
| GET | `/api/resource?uri=` | 读资源；未知 URI → 404 |
| POST | `/api/prompt` | 展开提示词，返回 `messages` |
| GET | `/api/pitfalls` | 四类失败的实测现场（脱敏 / 自兜 / 未知工具 / 参数错） |
| GET | `/` | 客户端控制台 |

## 常见坑

1. **照着老教程写 `from mcp.server.fastmcp import FastMCP`**。2.x 里这个模块已经删除，
   报 `ModuleNotFoundError`，而且报错信息里往往还有一句提示让你读迁移指南。正确是
   `from mcp.server.mcpserver import MCPServer`。
2. **`print()` 到 stdout**。stdio 传输里 **stdout 就是协议通道**。一句调试 `print`
   会往流里插一行非 JSON，客户端分帧后解析失败，症状千奇百怪：
   有的调用抛异常、有的结果错位、有的直接断连，而且看着像 SDK 的 bug。
   **所有日志写 `sys.stderr`。** —— 我第一版探测就是被自己这行 print 骗了，
   把一个不存在的「SDK 会抛异常」结论写进了事实文档。
3. **以为工具失败要抛异常**。恰恰相反：`call_tool()` 对「未知工具、参数缺失、类型错、
   工具内部崩」**一律不抛**，全是 `is_error=True`。会抛的是**传输层**故障（断管、子进程死）。
4. **以为参数错误会告诉你原因**。它说的是
   `1 validation error for addArguments ... errors.pydantic.dev/...` ——
   `addArguments` 是 SDK 自动生成的内部类名。这段直接喂给模型，模型会开始模仿这种字段名。
5. **以为工具异常的信息能传回模型**。不能。只剩 `Error executing tool boom`，
   真实原因只在服务端 stderr。要传原因，必须在工具内部 `try/except` 并当**正常返回值**返回。
6. **`command="python"`**。非交互 shell 里拿到的可能是系统 python（没装 mcp），
   症状是子进程一启动就退出。用 `sys.executable` 或绝对路径。

## 验收清单

- [ ] `python main.py` 输出 `结果：61 通过 / 0 失败`
- [ ] 页面上 serverInfo 显示 `hello-mcp`，协议版本 `2025-11-25`
- [ ] 工具表单是按 `input_schema` 自动生成的，`add` 有两个数字输入框
- [ ] 点「跑一遍」坑演示，四条结论都在，`boom` 那条文本是英文模板
- [ ] `safe_div` 传 `b=0`，看到中文错误提示且 `is_error = false`
- [ ] 能不看文档说出：为什么工具异常和参数错误要分别处理

## 下一单元

Unit 50 · 自定义 Tool —— 从 `add` 这类玩具换成真正有用的工具。
重点不是「怎么写装饰器」，而是 `description` 到底该怎么写给模型看 ——
它不是文档注释，它是提示词。
