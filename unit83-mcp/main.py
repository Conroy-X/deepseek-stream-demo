"""Unit 83 · MCP 整合：工具注册表 + 调用往返。

核心认知：Unit 46-59 学了 MCP 的协议细节，本单元做整合——
一个最小 MCP server 的工具注册表 + 客户端调用往返。
关键：tools/list（发现能力）和 tools/call（执行）两个原语。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class McpServer:
    tools: dict = field(default_factory=dict)  # name -> {"description":..., "fn":...}

    def register(self, name: str, description: str, fn) -> None:
        self.tools[name] = {"description": description, "fn": fn}

    def list_tools(self) -> list[dict]:
        return [{"name": n, "description": t["description"]} for n, t in self.tools.items()]

    def call(self, name: str, args: dict) -> dict:
        if name not in self.tools:
            return {"error": f"未知工具 {name}"}
        try:
            return {"result": self.tools[name]["fn"](**args)}
        except TypeError as e:
            return {"error": f"参数错误: {e}"}


@dataclass
class McpClient:
    server: McpServer
    _known: list = field(default_factory=list)

    def connect(self) -> None:
        self._known = self.server.list_tools()

    def invoke(self, name: str, args: dict) -> dict:
        if name not in [t["name"] for t in self._known]:
            return {"error": "未连接的未知工具"}
        return self.server.call(name, args)


def run_selfcheck() -> int:
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 83 · MCP 整合 自检\n")
    srv = McpServer()
    srv.register("add", "两数相加", lambda a, b: a + b)
    srv.register("echo", "回显", lambda text: text)

    tools = srv.list_tools()
    check("tools/list 返回两个", len(tools) == 2)
    check("工具描述正确", any(t["name"] == "add" for t in tools))

    r = srv.call("add", {"a": 1, "b": 2})
    check("tools/call 执行成功", r.get("result") == 3, str(r))
    r2 = srv.call("nope", {})
    check("未知工具报错", "error" in r2)
    r3 = srv.call("add", {"a": 1})
    check("缺参报错", "error" in r3)

    # 客户端：先 connect 发现能力，再调用
    cli = McpClient(srv)
    cli.connect()
    r4 = cli.invoke("add", {"a": 10, "b": 5})
    check("客户端调用成功", r4.get("result") == 15)
    r5 = cli.invoke("hidden", {})
    check("未连接工具被拒", "error" in r5)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(run_selfcheck())
