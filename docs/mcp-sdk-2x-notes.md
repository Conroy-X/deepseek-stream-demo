# MCP Python SDK 2.x 实测事实

> 本文件里的每一条都在 `mcp 2.3.0` + Python 3.13.5 上真跑过。
> 网上大量教程还在讲 1.x 的 `FastMCP`，**照抄会直接 ImportError**。这一页就是给 Unit 49–60 用的防坑卡。

## 1. 一句话差异表

| 1.x 写法（教程里到处都是） | 2.x 实际写法 | 后果 |
| --- | --- | --- |
| `from mcp.server.fastmcp import FastMCP` | `from mcp.server.mcpserver import MCPServer` | 1.x 路径**已删除**，报 `ModuleNotFoundError` |
| `mcp = FastMCP("x")` | `server = MCPServer("x", version="0.1.0")` | 类改名 |
| `mcp.run()` | `server.run("stdio")` | `run()` 第一个参数是传输类型 |
| `mcp.run(transport="sse")` | `server.run("sse")` | 同上，位置参数 |
| `create_connected_server_and_client_session` | 无对应物；用 `stdio_client` 起子进程 | 该函数在 `mcp.shared.memory` 里**不存在** |
| `result.isError` / `init.serverInfo` | `result.is_error` / `init.server_info` | pydantic 模型改蛇形 |

`mcp.shared.memory` 里现在只有这些：`create_client_server_memory_streams`、`create_context_streams`、
`MessageStream`、`SessionMessage`、`ContextReceiveStream`、`ContextSendStream`。
我试过拿 `create_client_server_memory_streams` 手搓内存双工，**连不通**（服务端侧要 `_lowlevel_server` 私有属性），
所以本项目的统一做法是 **stdio 子进程**——这也是真实部署形态，不亏。

## 2. 服务端最小可跑骨架

```python
from mcp.server.mcpserver import MCPServer

server = MCPServer("calc", version="0.1.0")

@server.tool(name="add", description="两数相加")
def add(a: int, b: int) -> int:
    return a + b

@server.resource("doc://handbook/leave", name="请假制度", mime_type="text/markdown")
def leave_doc() -> str:
    return "# 请假制度\n\n年假 5 天。"

@server.prompt(name="summarize", description="总结文本")
def summarize(text: str) -> str:
    return f"请总结：{text}"

if __name__ == "__main__":
    server.run("stdio")          # 或 "sse" / "streamable-http"
```

- `MCPServer.__init__(name=None, title=None, description=None, instructions=None, version="", *, tools=None, debug=False, log_level="INFO", ...)`
- `server.tool(name=None, title=None, description=None, annotations=None, icons=None, meta=None, structured_output=None)` → 装饰器
- 函数签名里的类型注解**就是** `inputSchema` 的来源（pydantic 自动生成），不要手写 schema。

## 3. 客户端最小可跑骨架

```python
import anyio
from mcp import StdioServerParameters, ClientSession
from mcp.client.stdio import stdio_client

params = StdioServerParameters(
    command="/home/rik/.cache/aj/runner/bin/python",   # 必须绝对路径
    args=["/abs/path/to/server.py"],
)

async def main():
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            init = await s.initialize()
            print(init.server_info.name, init.protocol_version)   # 蛇形！
            tools = await s.list_tools()
            for t in tools.tools:
                print(t.name, t.input_schema)                     # input_schema 蛇形
            r = await s.call_tool("add", {"a": 2, "b": 3})
            print(r.is_error, r.content[0].text)                  # is_error 蛇形
anyio.run(main)
```

**同步代码里要跑异步**：`anyio.run(main)`，不要 `asyncio.run`（SDK 内部用 anyio task group，混用会炸）。

## 4. 实测输出（原始记录）

```
name: calc | proto: 2025-11-25
caps keys: ['prompts', 'resources', 'tools']
tool: add | schema: {"properties": {"a": {"title": "A", "type": "integer"},
                    "b": {"title": "B", "type": "integer"}},
                    "required": ["a", "b"], "type": "object", "title": "addArguments"}
add is_error: False | text: 5
structured: {'result': 5}
```

- **2.3.0 的默认协议版本是 `2025-11-25`**，比 `2025-06-18` 新。客户端会在 `initialize` 里协商，服务端返回它支持的版本。
- `structured_content` 会自动把返回值包一层：返回 `5` → `{'result': 5}`。
- `elapsed 0.67s`：一次完整子进程往返（起进程 + 握手 + 调用）约 0.7 秒。自检里起 3 次完全可接受。

## 5. 三个必须知道的坑

### 坑一：工具抛异常，异常信息**不会**传回模型

```python
@server.tool()
def boom() -> str:
    raise RuntimeError("故意炸")
```

客户端拿到的是：

```
boom is_error: True | text: Error executing tool boom
```

真实 traceback 只打到**服务端 stderr**。框架默认做脱敏——这是安全设计，但对 Agent 是灾难：
模型看到 `Error executing tool boom` 完全不知道该改什么。

**正确写法**：自己 try/except，把可读原因当成正常返回值送出去。

```python
@server.tool(name="safe_div", description="安全除法")
def safe_div(a: float, b: float) -> str:
    if b == 0:
        return "错误：除数为 0，请换一个非零除数"
    return str(a / b)
```

实测：`safe_div is_error: False | text: 错误：除数为 0` —— 用 `is_error=False` 换回了可读信息。
**取舍**：要么模型能读懂原因（自己兜），要么协议层标记失败但模型变瞎（直接抛）。Unit 50 / 58 都在讲这个取舍。

### 坑二：两类失败的方向正好相反 —— 一个信息太少，一个噪音太多

```python
await s.call_tool("add",  {"a": 1, "b": 2})     # is_error=False  text='3.0'
await s.call_tool("nope", {})                   # is_error=True   text='Unknown tool: nope'
await s.call_tool("add",  {"a": "x", "b": 1})   # is_error=True   text='Error executing tool add: 1 validation error for addArguments\na\n  Input should be a valid number, ... [type=float_parsing, ...]'
await s.call_tool("add",  {"b": 1})             # is_error=True   text='Error executing tool add: ... a\n  Field required [type=missing, ...]'
await s.call_tool("boom", {})                   # is_error=True   text='Error executing tool boom'
```

**这五种情况 `ClientSession.call_tool()` 全部不抛异常，一律返回结果对象。**
工具不存在、参数缺失、类型不对、工具内部崩了 —— 都是 `is_error=True`。

> ⚠️ 我第一版这里写的是「客户端会抛异常」，那是**错的**。
> 错因很值得记：我第一次探测时在服务端脚本里写了 `print(...)` 做调试，
> 而 **stdout 就是协议通道** —— 一行非 JSON 文本直接把流搅乱，客户端才抛的异常。
> 换句话说，我观测到的是自己调试代码的副作用，不是 SDK 的行为。

那真正的问题在哪？在两个方向的失衡：

| 失败类型 | 返回文本 | 问题 |
| --- | --- | --- |
| 工具内部异常 | `Error executing tool boom` | **信息太少**。真实原因只在服务端 stderr，模型完全不知道该改什么 |
| 参数校验失败 | `... 1 validation error for addArguments ... errors.pydantic.dev/...` | **噪音太多**。`addArguments` 是 SDK 自动生成的内部类名，后面还跟着 pydantic 文档链接 |

所以两类都得自己接管：

- **工具异常** → 自己 `try/except`，把可读原因当**正常返回值**送出去（`is_error=False`）。
- **参数错误** → 自己做前置校验，给「哪个参数、期望什么、你给了什么」的中文提示；
  不要让 pydantic 的原文流到模型面前，否则模型会开始模仿 `addArguments` 这种字段名。

一句话：**一个太少、一个太多，SDK 的默认两个都不合用。**

### 坑三：`print()` 到 stdout 会直接干掉协议流

这是上面那个错误的直接来源，也是 stdio 传输最常见的一类事故：

```python
@server.tool()
def add(a: int, b: int) -> int:
    print("debug:", a, b)     # ← 这一行会把 stdout 写脏
    return a + b
```

stdio 传输里 **stdout 就是 JSON-RPC 通道**。一句 `print` 会在流里插入一行非 JSON，
客户端分帧后 `json.loads` 失败，症状千奇百怪：有的调用抛异常、有的结果错位、
有的连接直接断。而且**看起来像是 SDK 的 bug**。

**所有日志一律写 `sys.stderr`。** 这也是 Unit 59 结构化日志那节的出发点。

### 坑四：`command` 必须绝对路径

`StdioServerParameters(command="python")` 在非交互 shell 里会拿到系统 python 而不是 venv 的。
最稳的写法是 `command=sys.executable` —— 谁启动我，就用谁的解释器。
自检脚本里则写死绝对路径。

## 6. 本项目统一约定

- 自检解释器：`/home/rik/.cache/aj/runner/bin/python`（已装 `mcp 2.3.0` / `fastapi` / `httpx` / `uvicorn`）。
- 服务端脚本放在**单元目录内**，用 `pathlib.Path(__file__).parent` 拼绝对路径，不要 `cd`。
- 子进程的 `stderr` 要接住：`stdio_client` 默认会把子进程 stderr 透到父进程，自检输出里出现服务端日志是正常的。
- 端口约定仍为 `unit N → 80NN`，但 MCP 服务端走 stdio，**不占端口**；只有配套的 FastAPI 实验台占端口。
