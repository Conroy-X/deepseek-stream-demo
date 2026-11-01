"""Unit 47 · 传输层：同一批帧，两根不同的管子。

Unit 46 把帧的形状定死了。这一章只回答一个问题：
**这些帧怎么从 A 进程走到 B 进程？**

MCP 定了两种传输，它们对「帧」的要求完全一样，对「边界」的要求完全不一样：

  stdio  —— 一根字节流，没有消息边界。你必须自己解决「一帧到哪里结束」。
            解法是换行分隔：一行一帧。所以你的 JSON 里绝对不能有裸换行。
            代价：零头部开销，但一个连接只能服务一个客户端，且没有原生双向推送之外的通道。
  HTTP   —— 每次请求自带边界（Content-Length / chunked）。分帧问题消失了，
            但每一帧都要背上请求头和响应头，而且服务端要另想办法把「主动通知」推回来
            （这就是 SSE 那一半的由来）。

这三种死法在本文件里都有断言：
  1. 用 json.dumps(indent=2) 生成帧 → 分帧器把一帧切成七八个残片；
  2. 服务器读到 stdin EOF 却不退出 → 僵尸进程；
  3. HTTP 上不带 session id → 每次请求都是一个全新的陌生人。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

# ── 协议常量（与 Unit 46 保持一致） ───────────────────────────────────────────

JSONRPC = "2.0"
PROTOCOL_VERSION = "2025-06-18"
SESSION_HEADER = "Mcp-Session-Id"

#: stdio 传输的帧分隔符。规范只认换行。
FRAME_SEP = b"\n"


class TransportError(Exception):
    """传输层故障：管子断了、帧切不出来、会话丢了。和 RpcError 不是一回事。"""


# ── 分帧：stdio 上唯一真正难的地方 ────────────────────────────────────────────


class FrameCodec:
    """把帧 ↔ 字节流互转。stdio 传输的全部复杂度都在这个类里。"""

    @staticmethod
    def encode(frame: dict[str, Any]) -> bytes:
        """一帧 = 一行紧凑 JSON + 换行。

        `separators=(",", ":")` 不是审美问题：默认的 `", "` 和 `": "` 会让每一帧
        多出几个字节，在高频调用下是实打实的带宽。`ensure_ascii=False` 让中文
        保持原样 —— 转义成 \\uXXXX 会让一条中文报错膨胀三倍。
        """
        return json.dumps(
            frame, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8") + FRAME_SEP

    @staticmethod
    def encode_pretty(frame: dict[str, Any]) -> bytes:
        """反面教材：人类友好，协议致命。缩进里全是换行。"""
        return json.dumps(frame, ensure_ascii=False, indent=2).encode("utf-8") + FRAME_SEP

    @staticmethod
    def feed(buffer: bytearray, chunk: bytes) -> list[dict[str, Any]]:
        """喂一段字节，吐出所有「已经完整」的帧，剩下的留在 buffer 里。

        这是流式解析的全部秘密：你不能假设一次 read() 正好读到一帧的结尾。
        可能读到半帧（要留着等下一段），也可能一次读到三帧（要全部切出来）。
        """
        buffer.extend(chunk)
        frames: list[dict[str, Any]] = []
        while True:
            idx = buffer.find(FRAME_SEP)
            if idx < 0:
                break
            line = bytes(buffer[:idx])
            del buffer[: idx + 1]
            if not line.strip():
                continue  # 空行不是帧，是填充。别把它当解析失败。
            try:
                frames.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise TransportError(f"分帧成功但 JSON 非法：{e}；原始行={line[:80]!r}") from e
        return frames

    @staticmethod
    def pending(buffer: bytearray) -> bytes:
        """还没凑成整帧的残留字节。EOF 时如果这里非空，说明对面写了个半帧就断了。"""
        return bytes(buffer)


# ── 一根单向管道 ─────────────────────────────────────────────────────────────


class MemoryPipe:
    """单向字节管道。用来在单进程里精确复现 stdio 的行为。

    为什么不用真的 subprocess：真的子进程没法稳定复现「读到半帧」「读到 EOF」
    这些边界，而这些边界正是本单元要教的东西。
    """

    def __init__(self, name: str = "pipe") -> None:
        self.name = name
        self._buf = bytearray()
        self._closed = False
        self.write_calls = 0

    def write(self, data: bytes) -> int:
        if self._closed:
            raise TransportError(f"往已关闭的管道 {self.name} 写数据")
        self._buf.extend(data)
        self.write_calls += 1
        return len(data)

    def read(self, n: int = -1) -> bytes:
        """模拟一次 read()。n=-1 表示「有多少拿多少」。"""
        if not self._buf:
            return b""
        if n < 0 or n >= len(self._buf):
            out = bytes(self._buf)
            self._buf.clear()
            return out
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def close(self) -> None:
        """关闭后：read() 返回空并让调用方看到 EOF。"""
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def buffered(self) -> int:
        return len(self._buf)


@dataclass
class PipePair:
    """一对反向管道，凑成一个双向连接。"""

    to_server: MemoryPipe = field(default_factory=lambda: MemoryPipe("client→server"))
    to_client: MemoryPipe = field(default_factory=lambda: MemoryPipe("server→client"))


# ── stdio 传输 ───────────────────────────────────────────────────────────────


class StdioTransport:
    """一个方向读、一个方向写。这就是 stdio 传输的全部。"""

    def __init__(self, pair: PipePair) -> None:
        self.pair = pair
        self._rx = bytearray()
        self.sent_frames = 0
        self.sent_bytes = 0

    def send(self, frame: dict[str, Any]) -> int:
        raw = FrameCodec.encode(frame)
        self.pair.to_server.write(raw)
        self.sent_frames += 1
        self.sent_bytes += len(raw)
        return len(raw)

    def recv(self, max_chunk: int = -1) -> list[dict[str, Any]]:
        chunk = self.pair.to_client.read(max_chunk)
        if not chunk:
            return []
        return FrameCodec.feed(self._rx, chunk)

    def recv_bytes(self, raw: bytes) -> list[dict[str, Any]]:
        """直接喂原始字节 —— 自检里用来复现「半帧」「连帧」，不用绕管道。"""
        return FrameCodec.feed(self._rx, raw)

    @property
    def pending(self) -> bytes:
        return FrameCodec.pending(self._rx)


def serve_stdio(
    pair: PipePair,
    handler: Callable[[dict[str, Any]], dict[str, Any] | None],
    max_loops: int = 100,
) -> dict[str, Any]:
    """跑一个 stdio 服务端主循环，返回它为什么退出。

    最容易被忽略的一行是 `if reader.closed and not chunk: break`。
    stdin 读到 EOF 的意思是「客户端走了」。这时候必须退出，
    否则宿主每重启一次就留下一个僵尸进程 —— 它永远在等一个再也写不进来的管道。
    """
    reader = pair.to_server
    writer = pair.to_client
    buf = bytearray()
    stats = {"frames_in": 0, "frames_out": 0, "exit_reason": "", "loops": 0}

    for _ in range(max_loops):
        stats["loops"] += 1
        chunk = reader.read()
        if not chunk:
            if reader.closed:
                stats["exit_reason"] = "eof"
                leftover = FrameCodec.pending(buf)
                if leftover:
                    stats["exit_reason"] = "eof_with_partial_frame"
                    stats["leftover"] = leftover.decode("utf-8", "replace")
                return stats
            stats["exit_reason"] = "would_block"
            return stats

        for frame in FrameCodec.feed(buf, chunk):
            stats["frames_in"] += 1
            response = handler(frame)
            if response is not None:  # 通知不回
                writer.write(FrameCodec.encode(response))
                stats["frames_out"] += 1

    stats["exit_reason"] = "max_loops"
    return stats


# ── HTTP 传输：分帧问题消失，头部开销上桌 ─────────────────────────────────────

#: 一次 POST 请求的固定头部（不含 body）。数字来自真实抓包量级的近似：
#: 请求行 + Host + Content-Type + Content-Length + 一个自定义 session 头。
HTTP_REQUEST_HEADER_TEMPLATE = (
    "POST /rpc HTTP/1.1\r\n"
    "Host: 127.0.0.1:8047\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: {length}\r\n"
    "Mcp-Session-Id: {session}\r\n"
    "\r\n"
)

HTTP_RESPONSE_HEADER_TEMPLATE = (
    "HTTP/1.1 200 OK\r\n"
    "Content-Type: application/json\r\n"
    "Content-Length: {length}\r\n"
    "\r\n"
)


def http_request_wire(body: bytes, session: str = "-") -> int:
    """这一帧在 HTTP 上真正要发出去多少字节（头 + 体）。"""
    head = HTTP_REQUEST_HEADER_TEMPLATE.format(length=len(body), session=session)
    return len(head.encode("utf-8")) + len(body)


def http_response_wire(body: bytes) -> int:
    return len(HTTP_RESPONSE_HEADER_TEMPLATE.format(length=len(body)).encode("utf-8")) + len(body)


def compare_wire_cost(frames: Iterable[dict[str, Any]], session: str = "s-1") -> dict[str, Any]:
    """同一批帧，stdio 花多少字节，HTTP 花多少字节。"""

    def rows(transport: str) -> list[dict[str, Any]]:
        out = []
        for f in frames:
            body = FrameCodec.encode(f)
            if transport == "stdio":
                cost = len(body)
            else:
                cost = http_request_wire(body, session) + http_response_wire(b'{"jsonrpc":"2.0"}')
            out.append({"method": f.get("method") or "response", "body": len(body), "wire": cost})
        return out

    stdio_rows = rows("stdio")
    http_rows = rows("http")
    stdio_total = sum(r["wire"] for r in stdio_rows)
    http_total = sum(r["wire"] for r in http_rows)
    return {
        "stdio": stdio_rows,
        "http": http_rows,
        "stdio_total": stdio_total,
        "http_total": http_total,
        "ratio": round(http_total / stdio_total, 2) if stdio_total else 0.0,
        "extra_bytes": http_total - stdio_total,
    }


class SessionTable:
    """HTTP 传输必须自己补回 stdio 免费得到的东西：连接身份。

    stdio 上「一条连接」= 一个进程 = 一份状态，天然一对一。
    HTTP 上每个请求都是新的 TCP 连接（可能），服务器只能靠 session id
    把请求串回同一个逻辑会话。丢了它，每次请求都是一个陌生人。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._seq = 0
        self.rejected = 0

    def create(self, client_info: str = "") -> str:
        self._seq += 1
        sid = f"s-{self._seq:04d}"
        self._sessions[sid] = {"client": client_info, "initialized": False, "requests": 0}
        return sid

    def touch(self, sid: str | None) -> dict[str, Any]:
        """取会话；不存在就记账并抛错（对应 HTTP 400/404）。"""
        if not sid or sid not in self._sessions:
            self.rejected += 1
            raise TransportError(
                f"未知或缺失 {SESSION_HEADER}：{sid!r} —— 每个请求都会变成新会话，状态全丢"
            )
        sess = self._sessions[sid]
        sess["requests"] += 1
        return sess

    def mark_initialized(self, sid: str) -> None:
        self.touch(sid)["initialized"] = True

    def drop(self, sid: str) -> None:
        self._sessions.pop(sid, None)

    @property
    def active(self) -> int:
        return len(self._sessions)


# ── Host / Client / Server 三层到底谁负责什么 ────────────────────────────────


@dataclass(frozen=True)
class Layer:
    name: str
    owns: tuple[str, ...]
    does_not_own: tuple[str, ...]
    note: str


TOPOLOGY: tuple[Layer, ...] = (
    Layer(
        name="Host 宿主",
        owns=("模型", "对话循环", "用户界面", "决定要不要调工具"),
        does_not_own=("协议细节", "工具实现"),
        note="Claude Desktop / Cursor / 你自己写的 Agent 都是 Host。它是唯一有「意图」的一层。",
    ),
    Layer(
        name="Client 协议客户端",
        owns=("与一个 Server 的一对一连接", "握手", "帧的收发与匹配", "能力缓存"),
        does_not_own=("选哪个工具", "工具业务逻辑"),
        note="Host 里通常住着 N 个 Client，每个绑一个 Server。它只翻译，不做决定。",
    ),
    Layer(
        name="Server 工具服务",
        owns=("工具实现", "资源读取", "提示词模板", "自己的能力声明"),
        does_not_own=("模型", "对话历史", "用户是谁"),
        note="一个不知道自己被谁调用的进程。它只回答「你有什么」和「执行这个」。",
    ),
)


# ── 一个能跑的最小 server，供 main.py 的 HTTP 端复用 ─────────────────────────


def build_handler() -> Callable[[dict[str, Any]], dict[str, Any] | None]:
    """极简 dispatch：够用来演示两种传输跑的是同一套帧。"""

    def ok(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC, "id": msg_id, "result": result}

    def err(msg_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": JSONRPC, "id": msg_id, "error": {"code": code, "message": message}}

    tools = [
        {
            "name": "echo",
            "description": "原样返回输入",
            "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        }
    ]

    def handler(frame: dict[str, Any]) -> dict[str, Any] | None:
        if "id" not in frame:  # 通知：不回复
            return None
        method = frame.get("method")
        msg_id = frame.get("id")
        if method == "initialize":
            return ok(
                msg_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "transport-demo", "version": "0.1.0"},
                },
            )
        if method == "tools/list":
            return ok(msg_id, {"tools": tools})
        if method == "tools/call":
            params = frame.get("params") or {}
            if params.get("name") != "echo":
                return err(msg_id, -32602, "Unknown tool")
            text = (params.get("arguments") or {}).get("text", "")
            return ok(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        return err(msg_id, -32601, f"Method not found: {method}")

    return handler


# ── 一条完整的 stdio 会话脚本（给页面用） ────────────────────────────────────


def run_stdio_session() -> list[dict[str, Any]]:
    """把四帧依次塞进管道，记录每一步的字节数。"""
    pair = PipePair()
    handler = build_handler()
    transcript: list[dict[str, Any]] = []

    script = [
        {"jsonrpc": JSONRPC, "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}},
        {"jsonrpc": JSONRPC, "method": "notifications/initialized"},
        {"jsonrpc": JSONRPC, "id": 2, "method": "tools/list"},
        {"jsonrpc": JSONRPC, "id": 3, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": "你好"}}},
    ]

    transport = StdioTransport(pair)
    for frame in script:
        sent = transport.send(frame)
        stats = serve_stdio(pair, handler, max_loops=2)
        got = transport.recv()
        transcript.append(
            {
                "sent": frame,
                "sent_bytes": sent,
                "response": got[0] if got else None,
                "server_loops": stats["loops"],
                "exit_reason": stats["exit_reason"],
            }
        )
    pair.to_server.close()
    final = serve_stdio(pair, handler, max_loops=2)
    transcript.append({"sent": None, "note": "关闭 stdin 后服务器应当退出", "exit_reason": final["exit_reason"]})
    return transcript
