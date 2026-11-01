"""Unit 49 · 第一个 Server（用 MCP Python SDK 2.x）。

前三章手写的协议层，这里全变成装饰器。但你已经在 Unit 46–48 见过那些帧了，
所以每一行装饰器你都知道它背后在发什么。

⚠️ 网上教程 90% 还在写 1.x：

    from mcp.server.fastmcp import FastMCP      # 2.x 里这个模块已删除
    mcp = FastMCP("x")

2.x 的正确写法是 `MCPServer`，从 `mcp.server.mcpserver` 导入。
和它一起变的还有一堆细节（蛇形属性名、异常脱敏、客户端抛错而不是返回 is_error），
这些坑在 `docs/mcp-sdk-2x-notes.md` 里逐条记着，本单元的自检也会逐条验一遍。

启动：
    python server.py            # stdio，供客户端拉起来
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer(
    "hello-mcp",
    version="0.1.0",
    instructions="一个用来演示的最小 server：三个工具、一个资源、一个提示词。",
)

CALLS: dict[str, int] = {}


# ── 工具 1：最普通的纯函数 ───────────────────────────────────────────────────


@server.tool(name="greet", description="按名字打招呼。参数 name 是必填的人名。")
def greet(name: str) -> str:
    CALLS["greet"] = CALLS.get("greet", 0) + 1
    return f"你好，{name}！"


# ── 工具 2：多参数 + 类型注解生成 schema ─────────────────────────────────────


@server.tool(name="add", description="两数相加。a 和 b 都是数字，返回它们的和。")
def add(a: float, b: float) -> float:
    CALLS["add"] = CALLS.get("add", 0) + 1
    return a + b


# ── 工具 3：自己接住异常 —— 让模型能读到原因 ─────────────────────────────────


@server.tool(
    name="safe_div",
    description="安全除法。除数为 0 时返回一句中文说明，不抛异常。",
)
def safe_div(a: float, b: float) -> str:
    """为什么自己接住异常：

    SDK 默认把工具异常脱敏成 `Error executing tool safe_div`，
    真实 traceback 只打到服务端 stderr。模型只看到「执行失败」四个字，
    完全不知道该改什么。所以想让它读懂原因，就得把原因当成**正常返回值**送出去。
    代价是 is_error=False —— 协议层认为这次调用成功了。
    """
    CALLS["safe_div"] = CALLS.get("safe_div", 0) + 1
    if b == 0:
        return "错误：除数为 0，请换一个非零的除数"
    return str(a / b)


# ── 工具 4：故意抛异常 —— 用来展示脱敏 ──────────────────────────────────────


@server.tool(name="boom", description="一定会失败的工具，只用来观察 SDK 怎么处理异常。")
def boom() -> str:
    CALLS["boom"] = CALLS.get("boom", 0) + 1
    raise RuntimeError("这条中文原因不会传回客户端")


# ── 资源 ────────────────────────────────────────────────────────────────────


@server.resource("doc://hello/readme", name="使用说明", mime_type="text/markdown")
def readme() -> str:
    return (
        "# hello-mcp\n\n"
        "这是 Unit 49 的演示 server。\n\n"
        "- 工具：greet / add / safe_div / boom\n"
        "- 资源：doc://hello/readme\n"
        "- 提示词：intro\n"
    )


# ── 提示词 ──────────────────────────────────────────────────────────────────


@server.prompt(name="intro", description="让模型用三句话介绍这个 server 的能力")
def intro(topic: str = "MCP") -> str:
    return f"请用三句话向一个没听过 {topic} 的同事介绍它，每句不超过 25 字。"


if __name__ == "__main__":
    server.run("stdio")
