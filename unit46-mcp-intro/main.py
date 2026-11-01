"""Unit 46 · MCP 是什么 —— 把协议拆开看，它只有 JSON-RPC 2.0 + 一次握手。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8046
    打开 http://127.0.0.1:8046/

自检（离线，不需要任何 SDK）：
    uv run python main.py
"""

from __future__ import annotations

import json
import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PROTOCOL_VERSION,
    SERVER_NOT_INITIALIZED,
    SUPPORTED_VERSIONS,
    MiniServer,
    RpcError,
    build_demo_server,
    client_capabilities,
    is_notification,
    make_error,
    make_notification,
    make_request,
    make_result,
    match_response,
    negotiate,
    run_handshake_script,
    server_capabilities,
    validate_frame,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 46 · MCP 是什么", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

SERVER: MiniServer = build_demo_server()


class FrameIn(BaseModel):
    frame: dict = Field(description="一整帧 JSON-RPC 对象")


def examples() -> dict:
    """页面上那几个「点一下就填好」的样例。顺序就是真实客户端的顺序。"""
    return {
        "initialize": {
            "label": "① initialize（握手）",
            "frame": make_request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": client_capabilities(),
                "clientInfo": {"name": "unit46-demo", "version": "0.1.0"},
            }, 1),
            "note": "没这一步，后面任何请求都会被 -32002 拒绝",
        },
        "initialized": {
            "label": "② notifications/initialized（通知）",
            "frame": make_notification("notifications/initialized"),
            "note": "通知没有 id，服务器不回任何东西 —— 页面上你会看到 response 是 null",
        },
        "tools_list": {
            "label": "③ tools/list",
            "frame": make_request("tools/list", {}, 2),
            "note": "返回的每一项都带 inputSchema，和 Unit 32 的 JSON Schema 是同一个东西",
        },
        "tools_call": {
            "label": "④ tools/call",
            "frame": make_request("tools/call", {"name": "add", "arguments": {"a": 2, "b": 3}}, 3),
            "note": "结果在 result.content[0].text 里，是字符串 —— 这点和 OpenAI 的 tool 消息一样",
        },
        "resources_read": {
            "label": "⑤ resources/read",
            "frame": make_request("resources/read", {"uri": "doc://handbook/leave"}, 4),
            "note": "resource 是「被动读取的数据」，不是「会被执行的动作」",
        },
        "unknown_method": {
            "label": "⑥ 未声明的方法",
            "frame": make_request("sampling/createMessage", {}, 5),
            "note": "服务器没声明 sampling 能力，所以它对这个方法一无所知 → -32601",
        },
        "bad_frame": {
            "label": "⑦ 非法帧",
            "frame": {"jsonrpc": "1.0", "method": "tools/list", "id": 6},
            "note": "jsonrpc 不是 2.0，直接 -32600",
        },
    }


@app.get("/health", summary="服务器身份、能力表、工具数、支持的协议版本")
def health() -> dict:
    return {
        "ok": True,
        "server": SERVER.name,
        "version": SERVER.version,
        "initialized": SERVER.initialized,
        "negotiated_version": SERVER.negotiated_version,
        "protocol_version": PROTOCOL_VERSION,
        "supported_versions": list(SUPPORTED_VERSIONS),
        "capabilities": SERVER.capabilities,
        "tools": sorted(SERVER.tools),
        "resources": sorted(SERVER.resources),
        "prompts": sorted(SERVER.prompts),
        "calls": SERVER.calls,
    }


@app.get("/api/examples", summary="七种可点的帧样例")
def get_examples() -> dict:
    return {"items": examples(), "order": list(examples())}


@app.post("/rpc", summary="把一帧喂给服务器，返回它的响应（通知返回 null）")
def rpc(req: FrameIn) -> dict:
    frame = req.frame
    problems = validate_frame(frame)
    if problems:
        return {
            "request": frame,
            "response": make_error(frame.get("id"), INVALID_REQUEST, "帧格式非法", {"problems": problems}),
            "kind": "invalid",
        }
    response = SERVER.handle(frame)
    return {
        "request": frame,
        "response": response,
        "kind": "notification" if is_notification(frame) else "request",
    }


@app.post("/reset", summary="重置服务器状态（回到未握手）")
def reset() -> dict:
    global SERVER
    SERVER = build_demo_server()
    return {"ok": True, "initialized": SERVER.initialized}


@app.get("/api/flow", summary="跑一遍完整握手脚本，逐步返回请求与响应")
def flow() -> dict:
    server = build_demo_server()
    script = run_handshake_script(server)
    return {
        "steps": script,
        "final_initialized": server.initialized,
        "negotiated_version": server.negotiated_version,
        "calls": server.calls,
    }


@app.get("/api/log", summary="服务器收到的帧（按时间）")
def log() -> dict:
    return {"count": len(SERVER.log), "items": SERVER.log}


@app.get("/", include_in_schema=False)
def index():
    if os.path.exists(STATIC_INDEX):
        return FileResponse(STATIC_INDEX)
    return {"hint": "static/index.html 不存在，看 /docs"}


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 46 · MCP 协议层自检（离线，手写 JSON-RPC）\n")

    print("  —— 三种帧：请求 / 响应 / 通知 ——")
    req = make_request("tools/list", {}, 1)
    check("请求帧合法", validate_frame(req) == [], str(validate_frame(req)))
    check("请求帧带 id", req["id"] == 1)
    note = make_notification("notifications/initialized")
    check("通知没有 id", "id" not in note)
    check("is_notification 认得出通知", is_notification(note) is True)
    check("is_notification 不会把请求当通知", is_notification(req) is False)
    check("响应帧合法", validate_frame(make_result(1, {"tools": []})) == [])
    check("错误帧合法", validate_frame(make_error(1, INVALID_PARAMS, "坏参数")) == [])

    check("jsonrpc 必须是 2.0", any("jsonrpc" in p for p in validate_frame(
        {"jsonrpc": "1.0", "method": "x", "id": 1})))
    check("method 和 result 不能共存", any("请求帧" in p for p in validate_frame(
        {"jsonrpc": "2.0", "method": "x", "id": 1, "result": {}})))
    check("result 和 error 只能有一个", any("只能有一个" in p for p in validate_frame(
        {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {"code": 1, "message": ""}})))
    check("响应必须有 id", any("必须有 id" in p for p in validate_frame(
        {"jsonrpc": "2.0", "result": {}})))
    check("error.code 必须是整数", any("code" in p for p in validate_frame(
        {"jsonrpc": "2.0", "id": 1, "error": {"code": "1", "message": "x"}})))
    check("非对象帧被拒", validate_frame([1, 2]) != [])

    print("\n  —— 响应匹配：id 类型也要一致 ——")
    check("同 id 同类型能匹配", match_response(make_request("x", {}, 1), make_result(1, {})))
    check("1 与 \"1\" 不匹配（类型不同）",
          match_response(make_request("x", {}, 1), make_result("1", {})) is False)
    check("缺 result/error 的帧不算响应",
          match_response(make_request("x", {}, 1), {"jsonrpc": "2.0", "id": 1}) is False)

    print("\n  —— 能力协商 ——")
    caps = server_capabilities(tools=True, resources=False, prompts=False)
    check("只声明给的那一项", list(caps) == ["tools"], str(list(caps)))
    check("tools 带 listChanged 字段", "listChanged" in caps["tools"])
    cc = client_capabilities(roots=True, sampling=True)
    check("客户端能力方向相反（roots/sampling）", set(cc) == {"roots", "sampling"}, str(set(cc)))
    check("支持的版本原样协商", negotiate(PROTOCOL_VERSION) == PROTOCOL_VERSION)
    try:
        negotiate("1999-01-01")
        check("不支持的版本被拒", False)
    except RpcError as e:
        check("不支持的版本被拒（-32602）", e.code == INVALID_PARAMS)
        check("拒绝时列出支持的版本", "supported" in e.data)

    print("\n  —— 生命周期：没握手什么都不能调 ——")
    s = build_demo_server()
    resp = s.handle(make_request("tools/list", {}, 9))
    check("未握手时 tools/list → -32002", resp["error"]["code"] == SERVER_NOT_INITIALIZED, str(resp))
    check("未握手时 ping 仍可用（规范要求随时可探活）",
          "result" in s.handle(make_request("ping", {}, 10)))

    resp = s.handle(make_request("initialize", {"capabilities": {}}, 11))
    check("initialize 缺 protocolVersion → -32602", resp["error"]["code"] == INVALID_PARAMS)

    resp = s.handle(make_request("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": client_capabilities(),
        "clientInfo": {"name": "test", "version": "1"},
    }, 12))
    check("initialize 返回 protocolVersion", resp["result"]["protocolVersion"] == PROTOCOL_VERSION)
    check("initialize 返回 capabilities", "tools" in resp["result"]["capabilities"])
    check("initialize 返回 serverInfo", resp["result"]["serverInfo"]["name"] == "demo-mcp")
    check("initialize 不会把人标成已初始化",
          s.initialized is False, "握手要等 initialized 通知")

    s.handle(make_notification("notifications/initialized"))
    check("收到 initialized 通知后才算就绪", s.initialized is True)
    check("通知不回任何东西", s.handle(make_notification("notifications/initialized")) is None)

    print("\n  —— 工具：列表、调用、失败 ——")
    resp = s.handle(make_request("tools/list", {}, 20))
    tools = resp["result"]["tools"]
    check("列出 3 个工具", len(tools) == 3, str([t["name"] for t in tools]))
    check("工具带 inputSchema", all("inputSchema" in t for t in tools))
    check("schema 里禁了额外参数",
          tools[0]["inputSchema"].get("additionalProperties") is False,
          json.dumps(tools[0]["inputSchema"], ensure_ascii=False))

    resp = s.handle(make_request("tools/call", {"name": "add", "arguments": {"a": 2, "b": 3}}, 21))
    check("add 2+3 = 5",
          json.loads(resp["result"]["content"][0]["text"])["sum"] == 5, str(resp))
    check("成功时 isError=False", resp["result"]["isError"] is False)
    check("结果在 content[0].text 里（字符串）",
          isinstance(resp["result"]["content"][0]["text"], str))

    resp = s.handle(make_request("tools/call", {"name": "nope", "arguments": {}}, 22))
    check("未知工具 → -32602", resp["error"]["code"] == INVALID_PARAMS, str(resp))
    check("拒绝时把可用工具列出来", len(resp["error"]["data"]["available"]) == 3)

    resp = s.handle(make_request("tools/call", {"name": "add", "arguments": {"a": "x", "b": 1}}, 23))
    check("工具内部出错不是协议错误（返回 result）", "result" in resp, str(resp))
    check("工具出错用 isError=True 表达", resp["result"]["isError"] is True)
    check("错误文本能让模型看懂", "数字" in resp["result"]["content"][0]["text"],
          resp["result"]["content"][0]["text"])

    print("\n  —— 资源与提示词 ——")
    resp = s.handle(make_request("resources/list", {}, 30))
    check("列出 1 个 resource", len(resp["result"]["resources"]) == 1)
    resp = s.handle(make_request("resources/read", {"uri": "doc://handbook/leave"}, 31))
    check("读到 resource 正文", "年假" in resp["result"]["contents"][0]["text"])
    resp = s.handle(make_request("resources/read", {"uri": "doc://nope"}, 32))
    check("读不存在的 resource → -32602", resp["error"]["code"] == INVALID_PARAMS)
    resp = s.handle(make_request("prompts/list", {}, 33))
    check("列出 1 个 prompt", len(resp["result"]["prompts"]) == 1)
    resp = s.handle(make_request("prompts/get", {"name": "summarize"}, 34))
    check("取到 prompt 定义", resp["result"]["name"] == "summarize")

    print("\n  —— 能力门禁与错误码 ——")
    bare = build_demo_server()
    bare.capabilities = {"tools": {"listChanged": False}}   # 故意只声明 tools
    bare.handle(make_request("initialize", {"protocolVersion": PROTOCOL_VERSION}, 40))
    bare.handle(make_notification("notifications/initialized"))
    check("未声明 resources 时 resources/list → -32601",
          bare.handle(make_request("resources/list", {}, 41))["error"]["code"] == METHOD_NOT_FOUND)
    check("未声明 prompts 时 prompts/get → -32601",
          bare.handle(make_request("prompts/get", {"name": "x"}, 42))["error"]["code"] == METHOD_NOT_FOUND)
    check("未知方法 → -32601",
          bare.handle(make_request("sampling/createMessage", {}, 43))["error"]["code"] == METHOD_NOT_FOUND)
    resp = bare.handle({"jsonrpc": "2.0", "method": "tools/list", "id": 44, "result": {}})
    check("非法帧 → -32600", resp["error"]["code"] == INVALID_REQUEST, str(resp))

    print("\n  —— 完整流程：四步 ——")
    fresh = build_demo_server()
    script = run_handshake_script(fresh)
    check("脚本四步", len(script) == 4, str(len(script)))
    check("第一步是 initialize", script[0]["request"]["method"] == "initialize")
    check("第二步是通知且无响应", script[1]["response"] is None)
    check("第三步拿到工具列表", len(script[2]["response"]["result"]["tools"]) == 3)
    check("第四步拿到计算结果",
          json.loads(script[3]["response"]["result"]["content"][0]["text"])["sum"] == 5)
    check("每帧都合法", all(validate_frame(s["request"]) == [] for s in script))
    check("每个响应都回给了对应的请求",
          all(match_response(s["request"], s["response"])
              for s in script if s["response"] is not None))
    check("脚本跑完服务器状态是已初始化", fresh.initialized is True)
    check("计数器记录了 1 次真实工具调用", fresh.calls == 1, str(fresh.calls))

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    h = r.json()
    check("GET /health 返回服务器身份", r.status_code == 200 and h["server"] == "demo-mcp")
    check("/health 暴露能力表", set(h["capabilities"]) == {"tools", "resources", "prompts"})
    check("/health 暴露支持的协议版本", len(h["supported_versions"]) >= 2)

    client.post("/reset")
    r = client.post("/rpc", json={"frame": make_request("tools/list", {}, 1)})
    check("HTTP 未握手 → -32002", r.json()["response"]["error"]["code"] == SERVER_NOT_INITIALIZED)

    r = client.post("/rpc", json={"frame": make_request("initialize", {
        "protocolVersion": PROTOCOL_VERSION}, 2)})
    check("HTTP initialize 成功", "result" in r.json()["response"])
    r = client.post("/rpc", json={"frame": make_notification("notifications/initialized")})
    check("HTTP 发送知返回 response=None", r.json()["response"] is None)
    check("通知的 kind 是 notification", r.json()["kind"] == "notification")

    r = client.post("/rpc", json={"frame": make_request("tools/call",
                                                       {"name": "echo", "arguments": {"text": "hi"}}, 3)})
    check("HTTP 调工具成功",
          json.loads(r.json()["response"]["result"]["content"][0]["text"])["echo"] == "hi")

    r = client.post("/rpc", json={"frame": {"jsonrpc": "1.0", "method": "x", "id": 4}})
    check("HTTP 非法帧 → -32600", r.json()["response"]["error"]["code"] == INVALID_REQUEST)
    check("非法帧的 kind 是 invalid", r.json()["kind"] == "invalid")

    r = client.get("/api/flow")
    check("GET /api/flow 返回四步", len(r.json()["steps"]) == 4)
    check("flow 跑完是已初始化", r.json()["final_initialized"] is True)

    r = client.get("/api/examples")
    check("样例有 7 个", len(r.json()["items"]) == 7)
    check("样例都带 note 解释", all(v["note"] for v in r.json()["items"].values()))

    r = client.post("/reset")
    check("reset 回到未握手", r.json()["initialized"] is False)

    r = client.get("/")
    check("GET / 返回页面", r.status_code == 200 and "MCP" in r.text)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
