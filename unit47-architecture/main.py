"""Unit 47 · 架构与传输：Host / Client / Server，以及 stdio 与 HTTP 两根管子。

实验台跑在 8047。协议帧和 Unit 46 完全一样，变的只有「帧怎么走」。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from transport import (
    FRAME_SEP,
    PROTOCOL_VERSION,
    SESSION_HEADER,
    TOPOLOGY,
    FrameCodec,
    PipePair,
    SessionTable,
    StdioTransport,
    TransportError,
    build_handler,
    compare_wire_cost,
    run_stdio_session,
    serve_stdio,
)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Unit 47 · 架构与传输")

HANDLER = build_handler()
SESSIONS = SessionTable()


# ── 请求体 ───────────────────────────────────────────────────────────────────


class FrameIn(BaseModel):
    frame: dict[str, Any]


class SplitIn(BaseModel):
    text: str
    chunk_size: int = 0


# ── 接口 ─────────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "unit": 47,
        "protocol_version": PROTOCOL_VERSION,
        "transports": ["stdio", "http"],
        "frame_separator": FRAME_SEP.decode(),
        "session_header": SESSION_HEADER,
        "active_sessions": SESSIONS.active,
        "rejected_requests": SESSIONS.rejected,
    }


@app.get("/api/topology")
def topology() -> dict[str, Any]:
    return {
        "layers": [
            {
                "name": layer.name,
                "owns": list(layer.owns),
                "does_not_own": list(layer.does_not_own),
                "note": layer.note,
            }
            for layer in TOPOLOGY
        ],
        "rule": "一个 Server 一个 Client。N 个 Server 就是 N 次握手，N 份独立状态。",
    }


@app.get("/api/wire")
def wire() -> dict[str, Any]:
    frames = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    cost = compare_wire_cost(frames)
    cost["frame_bytes"] = [len(FrameCodec.encode(f)) for f in frames]
    cost["note"] = "同一个帧，stdio 只付 body；HTTP 还要付请求行 + 5 个头部 + 响应头。"
    return cost


@app.post("/api/splitter")
def splitter(payload: SplitIn) -> dict[str, Any]:
    """把一段文本按字节流喂进去，看能不能切出完整的帧。"""
    raw = payload.text.encode("utf-8")
    buffer = bytearray()
    frames: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    error: str | None = None

    if payload.chunk_size <= 0:
        chunks = [raw]
    else:
        chunks = [raw[i : i + payload.chunk_size] for i in range(0, len(raw), payload.chunk_size)]

    for i, chunk in enumerate(chunks, 1):
        try:
            got = FrameCodec.feed(buffer, chunk)
        except TransportError as e:
            error = str(e)
            steps.append({"chunk": i, "bytes": len(chunk), "frames": 0, "error": error})
            break
        frames.extend(got)
        steps.append(
            {
                "chunk": i,
                "bytes": len(chunk),
                "frames": len(got),
                "pending": len(FrameCodec.pending(buffer)),
            }
        )

    return {
        "input_bytes": len(raw),
        "chunks": len(chunks),
        "frames": frames,
        "frame_count": len(frames),
        "pending_bytes": len(FrameCodec.pending(buffer)),
        "error": error,
        "steps": steps,
    }


@app.get("/api/session-demo")
def session_demo() -> dict[str, Any]:
    """演示 HTTP 上「丢了 session id 会发生什么」。"""
    sid = SESSIONS.create("demo-client")
    SESSIONS.mark_initialized(sid)
    out: dict[str, Any] = {"created": sid, "with_id": None, "without_id": None, "rejected_before": SESSIONS.rejected}

    sess = SESSIONS.touch(sid)
    out["with_id"] = {"ok": True, "initialized": sess["initialized"], "requests": sess["requests"]}

    try:
        SESSIONS.touch(None)
        out["without_id"] = {"ok": True}
    except TransportError as e:
        out["without_id"] = {"ok": False, "error": str(e)}

    out["rejected_after"] = SESSIONS.rejected
    return out


@app.post("/rpc")
def rpc(payload: FrameIn, mcp_session_id: str | None = Header(default=None, alias=SESSION_HEADER)):
    """HTTP 传输下的一帧。

    第一步 `initialize` 不需要 session（还没有），服务器在响应头里发一个给你。
    之后的每一个请求都必须带着它 —— 否则服务器不知道你是谁。

    坑：这里**不能**用 `response: Response` 注入再加 header。注入的那个 Response
    在「函数直接 return 一个 Response 对象」时会被丢弃，头就静默消失。
    要么在返回的那个对象上设头，要么返回 dict 让 FastAPI 自己建 —— 二选一。
    """
    frame = payload.frame
    is_init = frame.get("method") == "initialize"
    out_sid = mcp_session_id or ""

    if is_init:
        out_sid = SESSIONS.create(str((frame.get("params") or {}).get("clientInfo") or "unknown"))
        SESSIONS.mark_initialized(out_sid)
    else:
        try:
            SESSIONS.touch(mcp_session_id)
        except TransportError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    reply = HANDLER(frame)
    if reply is None:  # 通知：202 + session 头，没有 body
        return Response(status_code=202, headers={SESSION_HEADER: out_sid})
    return JSONResponse(reply, headers={SESSION_HEADER: out_sid})


@app.get("/api/stdio-session")
def stdio_session() -> dict[str, Any]:
    return {"transcript": run_stdio_session()}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/pretty-bug")
def pretty_bug() -> dict[str, Any]:
    """把「人类友好」的 JSON 喂给分帧器，看它怎么死。"""
    frame = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    pretty = FrameCodec.encode_pretty(frame)
    compact = FrameCodec.encode(frame)

    buffer = bytearray()
    error: str | None = None
    try:
        FrameCodec.feed(buffer, pretty)
    except TransportError as e:
        error = str(e)

    return {
        "pretty_bytes": len(pretty),
        "compact_bytes": len(compact),
        "pretty_lines": pretty.decode("utf-8").count("\n"),
        "error": error,
        "compact_ok": len(FrameCodec.feed(bytearray(), compact)) == 1,
    }


# ── 自检 ─────────────────────────────────────────────────────────────────────

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    from fastapi.testclient import TestClient

    print("\n=== Unit 47 · 架构与传输 自检 ===\n")

    # —— 三层职责 ——
    print("  —— Host / Client / Server ——")
    check("拓扑是三层", len(TOPOLOGY) == 3, f"实际 {len(TOPOLOGY)}")
    names = [layer.name for layer in TOPOLOGY]
    check("第一层是 Host", names[0].startswith("Host"), str(names))
    check("Host 拥有模型", any("模型" in x for x in TOPOLOGY[0].owns), str(TOPOLOGY[0].owns))
    check("Client 不决定选哪个工具", any("选哪个工具" in x for x in TOPOLOGY[1].does_not_own), str(TOPOLOGY[1].does_not_own))
    check("Server 不知道用户是谁", any("用户是谁" in x for x in TOPOLOGY[2].does_not_own), str(TOPOLOGY[2].does_not_own))
    check(
        "每一层都明确写了不负责什么",
        all(layer.does_not_own for layer in TOPOLOGY),
        "有层没写 does_not_own",
    )

    # —— 分帧：stdio 唯一的难点 ——
    print("  —— 分帧（stdio 的边界问题） ——")
    one = FrameCodec.encode({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    check("一帧以换行结尾", one.endswith(b"\n"), repr(one[-3:]))
    check("紧凑编码不含裸换行", one.count(b"\n") == 1, f"换行数 {one.count(b'\n')}")
    check("紧凑编码不含空格填充", b": " not in one and b", " not in one, one[:40].decode())

    buf = bytearray()
    check("半帧切不出帧", FrameCodec.feed(buf, one[: len(one) // 2]) == [], "半帧被当成了完整帧")
    check("半帧留在 buffer 里", len(FrameCodec.pending(buf)) == len(one) // 2, f"残留 {len(FrameCodec.pending(buf))}")
    got = FrameCodec.feed(buf, one[len(one) // 2 :])
    check("补齐后切出 1 帧", len(got) == 1, f"实际 {len(got)}")
    check("切出的帧内容正确", got[0].get("method") == "ping", str(got))

    buf2 = bytearray()
    two = FrameCodec.feed(buf2, one + one)
    check("一次读到两帧要全切出来", len(two) == 2, f"实际 {len(two)}")
    check("连帧之后没有残留", len(FrameCodec.pending(buf2)) == 0, f"残留 {len(FrameCodec.pending(buf2))}")

    buf3 = bytearray()
    check("空行不算帧", FrameCodec.feed(buf3, b"\n\n\n") == [], "空行被当成帧")
    check(
        "非法 JSON 报 TransportError",
        _raises(lambda: FrameCodec.feed(bytearray(), b"{not json}\n"), TransportError),
    )

    # —— 那个「人类友好」的致命写法 ——
    print("  —— 缩进 JSON 为什么致命 ——")
    pretty = FrameCodec.encode_pretty({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    compact = FrameCodec.encode({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    check("缩进版比紧凑版大", len(pretty) > len(compact), f"{len(pretty)} vs {len(compact)}")
    # 这个帧很小，缩进只多 13 字节。真正的杀伤在换行，不在字节 —— 所以只要求「明显变大」。
    check("缩进版体积增长 ≥ 20%", len(pretty) >= len(compact) * 1.2, f"{len(pretty)} / {len(compact)} = {len(pretty)/len(compact):.2f}x")
    check("缩进版含多个换行", pretty.count(b"\n") > 3, f"换行数 {pretty.count(b'\n')}")
    check(
        "紧凑版只有一个换行（就是帧分隔符）",
        compact.count(b"\n") == 1,
        f"换行数 {compact.count(b'\n')}",
    )
    check(
        "缩进版喂进分帧器直接炸",
        _raises(lambda: FrameCodec.feed(bytearray(), pretty), TransportError),
        "缩进 JSON 竟然被切出来了",
    )
    check("紧凑版正常切出 1 帧", len(FrameCodec.feed(bytearray(), compact)) == 1)

    # —— stdio 主循环与 EOF ——
    print("  —— stdio 主循环 / EOF ——")
    pair = PipePair()
    handler = build_handler()
    transport = StdioTransport(pair)
    transport.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    stats = serve_stdio(pair, handler, max_loops=2)
    check("服务器处理了 1 帧", stats["frames_in"] == 1, str(stats))
    check("服务器回了 1 帧", stats["frames_out"] == 1, str(stats))
    replies = transport.recv()
    check("客户端收到 initialize 响应", replies[0]["result"]["protocolVersion"] == PROTOCOL_VERSION, str(replies))

    transport.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    stats = serve_stdio(pair, handler, max_loops=2)
    check("通知被接收", stats["frames_in"] == 1, str(stats))
    check("通知不产生响应", stats["frames_out"] == 0, str(stats))
    check("客户端确实没收到东西", transport.recv() == [], "通知竟然有回包")

    pair.to_server.close()
    final = serve_stdio(pair, handler, max_loops=2)
    check("stdin EOF 后服务器退出", final["exit_reason"] == "eof", final["exit_reason"])

    pair2 = PipePair()
    StdioTransport(pair2).send({"jsonrpc": "2.0", "id": 9, "method": "ping"})
    pair2.to_server.write(b'{"jsonrpc":"2.0","id":9,"met')
    pair2.to_server.close()
    partial = serve_stdio(pair2, handler, max_loops=4)
    check("半帧 + EOF 被识别为异常退出", partial["exit_reason"] == "eof_with_partial_frame", partial["exit_reason"])
    check("残留字节被报出来", "leftover" in partial and "\"met" in partial["leftover"], str(partial.get("leftover")))

    # —— HTTP：分帧问题消失，session 问题上桌 ——
    print("  —— HTTP 传输 / 会话绑定 ——")
    table = SessionTable()
    sid = table.create("client-a")
    check("新建会话拿到 id", sid == "s-0001", sid)
    check("会话初始未就绪", table.touch(sid)["initialized"] is False)
    table.mark_initialized(sid)
    check("标记后为已就绪", table.touch(sid)["initialized"] is True)
    check("同一 id 多次请求算同一个会话", table.active == 1, f"活跃 {table.active}")
    check("请求计数在累加", table.touch(sid)["requests"] >= 2, str(table.touch(sid)))
    check("缺 session id 抛 TransportError", _raises(lambda: table.touch(None), TransportError))
    check("拒绝被记账", table.rejected == 1, f"rejected={table.rejected}")
    check("未知 session id 也拒", _raises(lambda: table.touch("s-9999"), TransportError))
    table.drop(sid)
    check("drop 之后原 id 失效", _raises(lambda: table.touch(sid), TransportError))

    # —— 线缆成本实测 ——
    print("  —— 两根管子的字节成本 ——")
    frames = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": PROTOCOL_VERSION}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    cost = compare_wire_cost(frames)
    check("每帧都有 stdio 与 http 两个成本", len(cost["stdio"]) == 3 and len(cost["http"]) == 3, str(cost["stdio"]))
    check("HTTP 总字节大于 stdio", cost["http_total"] > cost["stdio_total"], f"{cost['http_total']} vs {cost['stdio_total']}")
    check("HTTP 多出来的字节超过 500", cost["extra_bytes"] > 500, f"只多了 {cost['extra_bytes']}")
    check("比值被算出来", cost["ratio"] > 1.5, f"ratio={cost['ratio']}")
    check(
        "同一帧 body 大小两侧一致",
        [r["body"] for r in cost["stdio"]] == [r["body"] for r in cost["http"]],
        "body 不一致说明比较的不是同一批帧",
    )

    # —— 完整 stdio 会话 ——
    print("  —— 完整 stdio 会话 ——")
    transcript = run_stdio_session()
    check("脚本 4 帧 + 1 条 EOF 记录", len(transcript) == 5, f"实际 {len(transcript)}")
    check("第一步是 initialize 且有响应", transcript[0]["response"]["result"]["protocolVersion"] == PROTOCOL_VERSION)
    check("第二步是通知且无响应", transcript[1]["response"] is None, str(transcript[1]))
    check("第三步 tools/list 返回 1 个工具", len(transcript[2]["response"]["result"]["tools"]) == 1)
    check("第四步 echo 回显中文", transcript[3]["response"]["result"]["content"][0]["text"] == "你好")
    check("关闭 stdin 后退出原因是 eof", transcript[4]["exit_reason"] == "eof", transcript[4]["exit_reason"])

    # —— HTTP 层 ——
    print("  —— HTTP 层 ——")
    client = TestClient(app)
    check("/health 200", client.get("/health").status_code == 200)
    h = client.get("/health").json()
    check("health 报出两种传输", h["transports"] == ["stdio", "http"], str(h["transports"]))
    check("其余接口均可达", all(client.get(p).status_code == 200 for p in ("/api/topology", "/api/wire", "/api/session-demo", "/api/stdio-session", "/api/pretty-bug")))

    init_frame = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": "test"}}
    r = client.post("/rpc", json={"frame": init_frame})
    check("initialize 不带 session 也能过", r.status_code == 200, f"{r.status_code} {r.text[:120]}")
    got_sid = r.headers.get(SESSION_HEADER)
    check("响应头里发回 session id", bool(got_sid), str(dict(r.headers)))
    check("返回了 serverInfo", r.json()["result"]["serverInfo"]["name"] == "transport-demo", r.text[:120])

    r2 = client.post("/rpc", json={"frame": {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}}, headers={SESSION_HEADER: got_sid})
    check("带 session 的后续请求成功", r2.status_code == 200 and len(r2.json()["result"]["tools"]) == 1, r2.text[:120])

    r3 = client.post("/rpc", json={"frame": {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}})
    check("不带 session 的后续请求被拒", r3.status_code == 400, f"{r3.status_code} {r3.text[:100]}")

    r4 = client.post("/rpc", json={"frame": {"jsonrpc": "2.0", "method": "notifications/initialized"}}, headers={SESSION_HEADER: got_sid})
    check("通知返回 202 而不是 200", r4.status_code == 202, str(r4.status_code))

    r5 = client.post("/rpc", json={"frame": {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "echo", "arguments": {"text": "hi"}}}}, headers={SESSION_HEADER: got_sid})
    check("HTTP 上 call_tool 正常", r5.json()["result"]["content"][0]["text"] == "hi", r5.text[:120])

    r6 = client.post("/rpc", json={"frame": {"jsonrpc": "2.0", "id": 5, "method": "nope"}}, headers={SESSION_HEADER: got_sid})
    check("未知方法回到 -32601", r6.json()["error"]["code"] == -32601, r6.text[:120])

    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200)
    check("页面含关键文案", "架构与传输" in page.text and len(page.text) > 4000, f"{len(page.text)} 字节")

    # —— 拆分演示接口 ——
    print("  —— 分帧接口 ——")
    body = FrameCodec.encode({"jsonrpc": "2.0", "id": 1, "method": "ping"}).decode()
    whole = client.post("/api/splitter", json={"text": body, "chunk_size": 0}).json()
    check("整段喂进去切出 1 帧", whole["frame_count"] == 1, str(whole))
    check("整段喂进去无残留", whole["pending_bytes"] == 0, str(whole["pending_bytes"]))
    sliced = client.post("/api/splitter", json={"text": body, "chunk_size": 7}).json()
    check("按 7 字节切片仍切出 1 帧", sliced["frame_count"] == 1, str(sliced))
    check("切片时步数大于 1", sliced["chunks"] > 1, str(sliced["chunks"]))
    check("中间步骤出现残留", any(s.get("pending", 0) > 0 for s in sliced["steps"]), str(sliced["steps"]))
    bad = client.post("/api/splitter", json={"text": "{\"a\":1}\n{\"b\":", "chunk_size": 0}).json()
    check("末尾半帧被报为残留", bad["pending_bytes"] > 0 and bad["frame_count"] == 1, str(bad))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


def _raises(fn, exc) -> bool:
    try:
        fn()
        return False
    except exc:
        return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
