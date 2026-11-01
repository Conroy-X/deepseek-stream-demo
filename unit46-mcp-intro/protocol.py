"""Unit 46 · MCP 的协议层：全是 JSON-RPC 2.0，没有魔法。

MCP 经常被讲得很玄。把外壳剥掉，它只有三件事：

    1. 一种传输（stdio 或 HTTP）上跑来跑去的 JSON-RPC 2.0 帧
    2. 一次「握手」：initialize → initialized，谈好协议版本和能力
    3. 握手之后，客户端按能力表发请求：tools/list、tools/call、resources/read…

这个文件就是最小实现，**不依赖官方 SDK**，故意手写。理由：
    · 你在 SDK 里永远看不到 initialize 的报文长什么样，出了问题就抓瞎
    · 面试问「MCP 到底传的什么」，答「用了 @mcp.tool 装饰器」是不及格的

三种帧（少一种都跑不通）：

    request        {"jsonrpc":"2.0","id":1,"method":"tools/list"}          有 id，要回
    response       {"jsonrpc":"2.0","id":1,"result":{...}}                 回它
    notification   {"jsonrpc":"2.0","method":"notifications/initialized"} 没 id，不回
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

JSONRPC = "2.0"

# 协议版本是**日期字符串**，不是 1.0/2.0 那种语义化版本。
# 客户端拿自己的版本和服务器支持的版本对，对不上就断开 —— 这一点和 OpenAPI 完全不同。
PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26")

# JSON-RPC 2.0 规定的错误码。MCP 只额外加了两个（-32002 未初始化、-32001 请求被取消）。
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
SERVER_NOT_INITIALIZED = -32002

ERROR_NAMES = {
    PARSE_ERROR: "Parse error",
    INVALID_REQUEST: "Invalid Request",
    METHOD_NOT_FOUND: "Method not found",
    INVALID_PARAMS: "Invalid params",
    INTERNAL_ERROR: "Internal error",
    SERVER_NOT_INITIALIZED: "Server not initialized",
}


class RpcError(Exception):
    """服务端的业务错误。要变成 error 帧，不是 500 页面。"""

    def __init__(self, code: int, message: str, data: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {}

    def to_frame(self, msg_id) -> dict:
        err = {"code": self.code, "message": self.message}
        if self.data:
            err["data"] = self.data
        return {"jsonrpc": JSONRPC, "id": msg_id, "error": err}


# ---------------------------------------------------------------------------
# 帧构造：只有四个函数，协议层的全部「语法」就在这里
# ---------------------------------------------------------------------------
def make_request(method: str, params: dict | None = None, msg_id: int | str = 1) -> dict:
    frame = {"jsonrpc": JSONRPC, "id": msg_id, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def make_notification(method: str, params: dict | None = None) -> dict:
    """通知**没有 id**。带了 id 就不是通知，对方会回你一个响应，然后你的解析器就乱了。"""
    frame = {"jsonrpc": JSONRPC, "method": method}
    if params is not None:
        frame["params"] = params
    return frame


def make_result(msg_id, result: dict) -> dict:
    return {"jsonrpc": JSONRPC, "id": msg_id, "result": result}


def make_error(msg_id, code: int, message: str, data: dict | None = None) -> dict:
    return RpcError(code, message, data).to_frame(msg_id)


def is_notification(frame: dict) -> bool:
    return "id" not in frame


def validate_frame(frame) -> list[str]:
    """检查一帧是否合法。返回问题列表，空列表表示合法。

    这里刻意不做「宽容解析」：MCP 两端都是程序，宽容只会让错误发生在更远的地方。
    """
    problems: list[str] = []
    if not isinstance(frame, dict):
        return ["帧必须是 JSON 对象"]
    if frame.get("jsonrpc") != JSONRPC:
        problems.append(f"jsonrpc 必须是 {JSONRPC!r}，收到 {frame.get('jsonrpc')!r}")

    has_id = "id" in frame
    has_method = "method" in frame
    has_result = "result" in frame
    has_error = "error" in frame

    if has_method:
        if not isinstance(frame["method"], str) or not frame["method"]:
            problems.append("method 必须是非空字符串")
        if has_result or has_error:
            problems.append("请求帧不能同时带 result/error")
    elif has_result or has_error:
        if has_result and has_error:
            problems.append("result 和 error 只能有一个")
        if not has_id:
            problems.append("响应帧必须有 id（哪怕是 null）")
        if has_error and not isinstance(frame["error"], dict):
            problems.append("error 必须是对象")
        if isinstance(frame.get("error"), dict):
            if not isinstance(frame["error"].get("code"), int):
                problems.append("error.code 必须是整数")
            if not isinstance(frame["error"].get("message"), str):
                problems.append("error.message 必须是字符串")
    else:
        problems.append("帧里既没有 method 也没有 result/error")

    return problems


def match_response(request: dict, response: dict) -> bool:
    """响应必须回给对的请求。

    id 是 string 还是 int 由客户端决定，服务器必须原样回传 ——
    见过把 id 转成 str 的实现，结果客户端永远匹配不上，表现是「一直等」。
    """
    return (
        response.get("jsonrpc") == JSONRPC
        and "id" in response
        and type(response["id"]) is type(request.get("id"))
        and response["id"] == request.get("id")
        and ("result" in response or "error" in response)
    )


# ---------------------------------------------------------------------------
# 能力协商：握手时要谈的三件事
# ---------------------------------------------------------------------------
def server_capabilities(tools: bool = True, resources: bool = True, prompts: bool = True) -> dict:
    """服务器声明的能力。**没声明的东西，客户端就不该问。**

    注意 `listChanged`：它表示「我支持在列表变化时主动通知你」。
    声明了就必须真的会发 notifications/tools/list_changed，否则客户端会等一个永远不来的通知。
    """
    caps: dict = {}
    if tools:
        caps["tools"] = {"listChanged": False}
    if resources:
        caps["resources"] = {"subscribe": False, "listChanged": False}
    if prompts:
        caps["prompts"] = {"listChanged": False}
    return caps


def client_capabilities(roots: bool = False, sampling: bool = False) -> dict:
    """客户端也有能力，方向反过来：roots（能提供文件根）和 sampling（能让服务器借模型）。"""
    caps: dict = {}
    if roots:
        caps["roots"] = {"listChanged": False}
    if sampling:
        caps["sampling"] = {}
    return caps


def negotiate(client_version: str) -> str:
    """版本协商：客户端报一个版本，服务端要么同意，要么给一个自己支持的。

    不同意不是「降级继续」，而是直接报错断开 —— 协议不兼容时继续跑，
    出错的地方会离原因很远（典型症状：字段莫名其妙缺失）。
    """
    if client_version in SUPPORTED_VERSIONS:
        return client_version
    raise RpcError(
        INVALID_PARAMS,
        f"不支持的协议版本 {client_version}",
        {"supported": list(SUPPORTED_VERSIONS)},
    )


# ---------------------------------------------------------------------------
# 最小服务器：一个 dispatch 表 + 一个状态机
# ---------------------------------------------------------------------------
@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: object  # callable(args: dict) -> dict

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class MiniServer:
    """手写的最小 MCP 服务器。

    状态机只有两个状态：未初始化 / 已初始化。
    未初始化时**除 initialize 外的任何请求**都必须被拒（-32002）——
    这条规则很多人不知道，然后疑惑「为什么我的客户端一连上就报错」。
    """

    name: str = "mini-mcp"
    version: str = "0.1.0"
    tools: dict[str, Tool] = field(default_factory=dict)
    resources: dict[str, dict] = field(default_factory=dict)
    prompts: dict[str, dict] = field(default_factory=dict)
    capabilities: dict = field(default_factory=dict)
    initialized: bool = False
    negotiated_version: str | None = None
    log: list[dict] = field(default_factory=list)
    calls: int = 0

    def __post_init__(self) -> None:
        if not self.capabilities:
            self.capabilities = server_capabilities(
                tools=bool(self.tools), resources=bool(self.resources), prompts=bool(self.prompts)
            )

    # ---------------------------------------------------------------- 分发
    def handle(self, frame) -> dict | None:
        """处理一帧。通知返回 None（不回），请求返回响应帧。"""
        problems = validate_frame(frame)
        if problems:
            return make_error(frame.get("id") if isinstance(frame, dict) else None,
                              INVALID_REQUEST, "帧格式非法", {"problems": problems})

        method = frame.get("method")
        if method is None:  # 客户端发来的是响应（双向通道里会用到）
            self.log.append({"dir": "in", "kind": "response", "id": frame.get("id")})
            return None

        is_note = is_notification(frame)
        self.log.append({"dir": "in", "kind": "notification" if is_note else "request",
                         "method": method, "id": frame.get("id")})

        try:
            result = self._dispatch(method, frame.get("params") or {}, is_note)
        except RpcError as e:
            return None if is_note else e.to_frame(frame.get("id"))
        except Exception as e:  # noqa: BLE001
            return None if is_note else make_error(
                frame.get("id"), INTERNAL_ERROR, f"服务器内部错误：{type(e).__name__}",
                {"detail": str(e)[:200]},
            )

        if is_note:
            return None
        return make_result(frame.get("id"), result)

    def _dispatch(self, method: str, params: dict, is_note: bool) -> dict:
        # 1) 握手永远放行
        if method == "initialize":
            return self._initialize(params)
        if method == "notifications/initialized":
            self.initialized = True
            return {}
        # ping 是唯一一个握手前后都能用的方法（规范要求它随时可用，通常用来探活）
        if method == "ping":
            return {}

        # 2) 没握手就调业务方法 → -32002。这是规范写明的行为，不是我们加戏。
        if not self.initialized:
            raise RpcError(SERVER_NOT_INITIALIZED, "请先完成 initialize 握手")

        # 3) 能力门禁：没声明的能力，方法不存在
        if method.startswith("tools/"):
            if "tools" not in self.capabilities:
                raise RpcError(METHOD_NOT_FOUND, "本服务器未声明 tools 能力")
            if method == "tools/list":
                return {"tools": [t.to_dict() for t in self.tools.values()]}
            if method == "tools/call":
                return self._call_tool(params)
        if method.startswith("resources/"):
            if "resources" not in self.capabilities:
                raise RpcError(METHOD_NOT_FOUND, "本服务器未声明 resources 能力")
            if method == "resources/list":
                return {"resources": list(self.resources.values())}
            if method == "resources/read":
                uri = params.get("uri")
                if uri not in self.resources:
                    raise RpcError(INVALID_PARAMS, f"资源不存在：{uri}")
                return {"contents": [{"uri": uri, "mimeType": "text/plain",
                                      "text": self.resources[uri]["text"]}]}
        if method.startswith("prompts/"):
            if "prompts" not in self.capabilities:
                raise RpcError(METHOD_NOT_FOUND, "本服务器未声明 prompts 能力")
            if method == "prompts/list":
                return {"prompts": list(self.prompts.values())}
            if method == "prompts/get":
                name = params.get("name")
                if name not in self.prompts:
                    raise RpcError(INVALID_PARAMS, f"prompt 不存在：{name}")
                return self.prompts[name]

        raise RpcError(METHOD_NOT_FOUND, f"未知方法：{method}")

    def _initialize(self, params: dict) -> dict:
        version = params.get("protocolVersion")
        if not version:
            raise RpcError(INVALID_PARAMS, "initialize 必须带 protocolVersion")
        self.negotiated_version = negotiate(version)
        client_info = params.get("clientInfo") or {}
        self.log.append({"dir": "in", "kind": "initialize",
                         "client": client_info.get("name"), "version": version})
        # 注意：initialize 的响应里带 serverInfo 和 capabilities，
        # 之后客户端只会调用这里声明过的东西。
        return {
            "protocolVersion": self.negotiated_version,
            "capabilities": self.capabilities,
            "serverInfo": {"name": self.name, "version": self.version},
        }

    def _call_tool(self, params: dict) -> dict:
        name = params.get("name")
        args = params.get("arguments") or {}
        tool = self.tools.get(name)
        if tool is None:
            raise RpcError(INVALID_PARAMS, f"没有这个工具：{name}",
                           {"available": sorted(self.tools)})
        self.calls += 1
        try:
            result = tool.handler(args)  # type: ignore[operator]
        except Exception as e:  # noqa: BLE001
            # 工具本身失败**不是**协议错误：要包成 result + isError，让模型看见。
            # 用 error 帧的话，客户端会当成「服务器坏了」，模型永远看不到原因。
            return {"content": [{"type": "text", "text": f"工具执行失败：{type(e).__name__}: {e}"}],
                    "isError": True}
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": False}


# ---------------------------------------------------------------------------
# 演示服务器：三个 tool，一个 resource，一个 prompt
# ---------------------------------------------------------------------------
def _now_date(_: dict) -> dict:
    return {"today": time.strftime("%Y-%m-%d"), "weekday": time.strftime("%A")}


def _add(args: dict) -> dict:
    a, b = args.get("a"), args.get("b")
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        raise ValueError("a 和 b 必须是数字")
    return {"sum": a + b}


def _echo(args: dict) -> dict:
    return {"echo": args.get("text", "")}


def build_demo_server() -> MiniServer:
    server = MiniServer(name="demo-mcp", version="0.1.0")
    server.tools = {
        "today": Tool(
            name="today",
            description="返回今天的日期与星期，无参数",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            handler=_now_date,
        ),
        "add": Tool(
            name="add",
            description="两数相加",
            input_schema={
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
                "additionalProperties": False,
            },
            handler=_add,
        ),
        "echo": Tool(
            name="echo",
            description="原样返回输入文本，用于验证链路",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
                "additionalProperties": False,
            },
            handler=_echo,
        ),
    }
    server.resources = {
        "doc://handbook/leave": {"uri": "doc://handbook/leave", "name": "休假制度",
                                 "mimeType": "text/plain",
                                 "text": "年假需提前 3 个工作日在系统提交申请。"},
    }
    server.prompts = {
        "summarize": {"name": "summarize", "description": "把给定文本压成三句话",
                      "arguments": [{"name": "text", "required": True}]},
    }
    server.capabilities = server_capabilities(tools=True, resources=True, prompts=True)
    return server


# ---------------------------------------------------------------------------
# 场景：一次完整的握手 + 调用（给页面和自检共用）
# ---------------------------------------------------------------------------
def run_handshake_script(server: MiniServer) -> list[dict]:
    """按真实客户端的顺序发一遍，返回每一对「请求 → 响应」。

    这个函数是本章的骨架：如果面试让你「说说 MCP 客户端连上服务器做了什么」，
    背这个列表就够了 —— 四步，缺一步都不行。
    """
    script: list[dict] = []

    def step(label: str, frame: dict, note: str = "") -> None:
        response = server.handle(frame)
        script.append({"label": label, "request": frame, "response": response, "note": note})

    step("1 握手请求", make_request("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": client_capabilities(),
        "clientInfo": {"name": "unit46-demo", "version": "0.1.0"},
    }, 1), "客户端声明自己是谁、说什么版本、支持哪些能力")

    step("2 握手确认", make_notification("notifications/initialized"), "通知无 id，服务器不回")

    step("3 列工具", make_request("tools/list", {}, 2), "握手后才能列")
    step("4 调工具", make_request("tools/call",
                                  {"name": "add", "arguments": {"a": 2, "b": 3}}, 3), "")

    return script
