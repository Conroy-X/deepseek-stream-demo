# Unit 49 笔记 · 第一个 Server

> 手写了三章协议，这一章装 SDK。本来以为会很轻松 —— 结果先被自己的调试代码骗了一次，
> 得出一个完全错误的结论。这条弯路比正路值得记。

## 本次做了什么

- 写 `server.py`：`MCPServer`，4 个工具（`greet` / `add` / `safe_div` / `boom`）、1 个资源、1 个提示词。
- 写 `client.py`：`MCPClient` 封装，把 `stdio_client` + `ClientSession` 包成可复用的上下文管理器，
  并把返回统一成 `ToolResult`。后面 Unit 50–54 都抄这份结构。
- 写 `main.py`：7 个接口 + **61 条离线自检**（0 失败，连跑两次一致，耗时 9.7 秒 —— 起了 8 次真子进程）。
- 写 `static/index.html`：客户端控制台，参数表单按 `input_schema` 自动生成。

## 一、1.x 与 2.x 的差异，先背下来

| 1.x（教程里到处都是） | 2.x 实际 |
| --- | --- |
| `from mcp.server.fastmcp import FastMCP` | `from mcp.server.mcpserver import MCPServer` |
| `mcp = FastMCP("x")` | `server = MCPServer("x", version="0.1.0")` |
| `mcp.run()` | `server.run("stdio")` |
| `result.isError` | `result.is_error` |
| `init.serverInfo` | `init.server_info` |
| `tool.inputSchema` | `tool.input_schema` |

**属性名改蛇形，但线上报文仍是驼峰。** pydantic 在中间翻译。
所以你在日志里看到 `isError`，在 Python 里写 `is_error` —— 两边都对，别以为哪边错了。

这一条我踩过：`init.serverInfo` 报 `AttributeError: 'InitializeResult' object has no attribute 'serverInfo'.
Did you mean: 'server_info'?` —— 好在 pydantic 的报错很贴心地给了正确名字。

## 二、那次错误的探测：我被自己的 print 骗了

第一次探测时我写了这么个 server：

```python
@server.tool(name="add")
def add(a: int, b: int) -> int:
    print("DEBUG add called", a, b, flush=True)   # ← 灾难
    return a + b
```

然后观察到：调不存在的工具时客户端**抛异常**，参数不对也**抛异常**。
我把这个结论写进了事实文档：「客户端抛错而不是返回 is_error，Agent 必须自己接住」。

后来在 Unit 49 里写成正式断言，测试直接红了 —— 实际返回的是 `is_error=True`。

**错因**：stdio 传输里 **stdout 就是 JSON-RPC 通道**。
`print` 往流里插了一行 `DEBUG add called 2 3`，客户端分帧后 `json.loads` 失败，
于是后续调用莫名其妙地抛异常。我观测到的不是 SDK 的行为，是**我自己调试代码的副作用**。

这条教训比任何 API 细节都值钱：

- **服务端脚本里永远不要 `print` 到 stdout**，日志一律 `sys.stderr`。
- 当你观测到「SDK 行为诡异」时，先怀疑自己往协议通道里写了东西。

## 三、真实的两类失败：一个信息太少，一个噪音太多

干净重测后的实测结果：

```
add{"a":1,"b":2}   → is_error=False  text='3.0'
nope{}             → is_error=True   text='Unknown tool: nope'
add{"a":"x","b":1} → is_error=True   text='Error executing tool add: 1 validation error for
                                        addArguments\na\n  Input should be a valid number,
                                        unable to parse string as a number [type=float_parsing, ...]'
add{"b":1}         → is_error=True   text='... Field required [type=missing, ...]'
boom{}             → is_error=True   text='Error executing tool boom'
```

**五种情况全都不抛异常。** 那问题在哪？在两个方向的失衡：

| 失败类型 | 返回文本 | 毛病 |
| --- | --- | --- |
| 工具内部异常 | `Error executing tool boom` | **信息太少**。真实原因只在服务端 stderr，模型无从下手 |
| 参数校验失败 | `... 1 validation error for addArguments ... errors.pydantic.dev/...` | **噪音太多**。`addArguments` 是 SDK 生成的内部类名，后面还跟着 pydantic 文档链接 |

所以两类都得自己接管，而且方向相反：

- **工具异常** → 在工具内部 `try/except`，把可读原因当**正常返回值**送出（`is_error=False`）。
  实测 `safe_div(1, 0)` 返回 `"错误：除数为 0，请换一个非零的除数"` 且 `is_error=False`。
  代价是协议层认为调用成功了 —— 这个取舍要在设计时明确。
- **参数错误** → 自己做前置校验。不要让 `addArguments` 流到模型面前，
  否则它会开始模仿这个字段名。要给的是「哪个参数、期望什么类型、你给了什么」。

一句话：**SDK 默认的两个极端都不合用，中间那段得自己写。**

## 四、`call_tool()` 到底什么时候抛？

只有**传输层**故障才抛：子进程死了、管道断了、帧非法。
这是 `client.py` 里那个 `try/except` 的唯一职责 ——
不是去接 `ToolError`（业务失败根本不会抛），而是防止一次断管把整个 Agent loop 带崩。

分清这一点，`ToolResult.raised` 字段的含义就清楚了：
它标记的是「这次失败发生在客户端这一侧」，而不是「工具报错了」。

## 五、一条连接一次握手

自检里开了两个 `MCPClient`，第二个的连接拿到同样的 `server_name`，
但 server 进程里的计数器归零 —— 因为**每个客户端都是一个全新的子进程**。

这不是 bug，正是 Unit 47 讲的那条规则的落地：
server 的状态挂在**连接**上。想要共享状态，只能放进 server 进程之外的存储（文件、DB、缓存）。

顺带一个成本数字：一次完整往返（起进程 + 握手 + 调用）约 **0.7 秒**。
所以自检跑了 9.7 秒 —— 8 次子进程。生产里这意味着
**MCP server 不该每次请求都重启**，客户端要长连接复用。

## 六、`structured_content` 的一层包装

`add(2, 3.5)` 返回 `5.5`，但 `structured_content` 是 `{"result": 5.5}` ——
SDK 自动把返回值包了一层 `result`。想在多字段结构化返回里自定义键名，
就别返回裸值，返回 dict（`{"sum": 5.5, "unit": "元"}` 会被原样透出）。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 用 `MCPServer` 写出三类能力 | 4 工具 / 1 资源 / 1 提示词，能力表三类齐全 |
| 说清两类失败为何要自己接管 | 实测五组返回，断言脱敏、断言 `addArguments` 泄露 |
| 解释 `print` 为何致命 | 自己踩过并被骗，写进 notes 与事实文档 |
| 客户端能复用 | `MCPClient` 上下文管理器，`ToolResult` 统一返回 |
| 离线可自检 | 61 条断言，0 失败，连跑两次一致，无网络 |

## 一句话总结

**SDK 把协议藏起来了，但没把「模型能不能看懂错误」这件事藏掉 —— 那部分永远是你的活。**

## 面试追问

**Q：MCP server 里工具抛异常和返回 `isError` 有什么区别？**
A：对客户端来说，`call_tool()` 的返回值都是 `is_error=True`，看起来一样。
区别在**文本内容**：抛异常会被框架脱敏成 `Error executing tool <name>`，真实原因只写到 stderr；
返回文本则原样送出去。所以判断标准是：**你希望模型看到原因吗？**
希望 → 自己 `try/except` 返回文本；不希望（内部错误不想泄露）→ 让它抛。
生产里通常是两者结合：对外脱敏，对内结构化日志留全量 trace。

**Q：为什么 MCP 的 stdio 传输不能用 `print` 调试？**
A：因为 stdio 传输把 **stdin/stdout 当作协议通道**，不是终端。
`print` 会往 JSON-RPC 帧流里插入非 JSON 文本，客户端分帧后解析失败，
表现为随机的调用异常、结果错位、连接断开 —— 而且错误位置离真正原因很远。
日志必须走 stderr（stderr 是独立通道，客户端只透传不解析）。

**Q：多个客户端连同一个 server，状态共享吗？**
A：不共享。stdio 传输下每个客户端拉起一个**独立子进程**，进程内存互不可见。
我实测过：第二个客户端调用后，server 里的计数器从 0 开始。
要共享状态必须外置（文件 / SQLite / Redis）。这也是为什么 HTTP 传输的 server
要显式设计 session 存储 —— 它的连接本来就是共享的。

**Q：2.x 把 `FastMCP` 改名为 `MCPServer`，背后有什么变化？**
A：名字改动背后是职责重新划分。1.x 的 `FastMCP` 把「协议服务器」和「装饰器糖」
揉在一个类里；2.x 的 `MCPServer` 底层仍然是一个标准的低层 `Server`（`_lowlevel_server`），
`MCPServer` 只是它上面的一层便利封装，同时把「传输」拆成了显式参数
（`run("stdio")` / `run("sse")` / `run("streamable-http")`）。
所以现在可以 mount 多个 server 到同一个 FastAPI 应用（`streamable_http_app()`），
这在 1.x 里很难做。
