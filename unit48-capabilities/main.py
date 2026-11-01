"""Unit 48 · 三大能力：Tools / Resources / Prompts。

实验台跑在 8048。核心不是「三个接口怎么调」，而是**同一条需求放错格子会怎样**。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from capabilities import (
    CONTROL_AXIS,
    JSONRPC,
    DemoServer,
    RpcError,
    UriTemplate,
    classify,
    examples,
    lint_demo,
)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Unit 48 · 三大能力")

SERVER = DemoServer()


class FrameIn(BaseModel):
    frame: dict[str, Any]


# ── 接口 ─────────────────────────────────────────────────────────────────────


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "unit": 48,
        "declared": sorted(SERVER.declared),
        "capabilities": SERVER.capabilities(),
        "counts": {
            "tools": len(SERVER.tools),
            "resources": len(SERVER.resources),
            "templates": len(SERVER.templates),
            "prompts": len(SERVER.prompts),
        },
    }


@app.get("/api/axis")
def axis() -> dict[str, Any]:
    return {
        "axis": CONTROL_AXIS,
        "summary": "三条能力不是并列的三个清单，区别在「谁决定用它」：模型 / 应用 / 用户。",
    }


@app.get("/api/examples")
def api_examples() -> dict[str, Any]:
    return {"examples": examples()}


@app.post("/rpc")
def rpc(payload: FrameIn) -> dict[str, Any]:
    reply = SERVER.handle(payload.frame)
    if reply is None:
        raise HTTPException(status_code=202, detail="通知不需要响应")
    return reply


@app.post("/reset")
def reset() -> dict[str, Any]:
    SERVER.reset()
    return {"ok": True, "counts": {"tools": len(SERVER.tools), "resources": len(SERVER.resources)}}


@app.get("/api/lint")
def lint() -> dict[str, Any]:
    cases = lint_demo()
    return {
        "cases": cases,
        "flagged": sum(1 for c in cases if not c["ok"]),
        "total": len(cases),
        "note": "放错了不会报错，只会用起来别扭 —— 所以需要主动挑刺。",
    }


@app.get("/api/caps/{declared}")
def caps(declared: str) -> dict[str, Any]:
    """把能力声明裁掉一部分，看未声明的方法返回什么。"""
    wanted = {x.strip() for x in declared.split(",") if x.strip()}
    if not wanted:
        raise HTTPException(status_code=400, detail="至少声明一种能力")
    temp = DemoServer(capabilities=wanted)
    results: dict[str, Any] = {}
    probes = {
        "tools": {"jsonrpc": JSONRPC, "id": 1, "method": "tools/list"},
        "resources": {"jsonrpc": JSONRPC, "id": 2, "method": "resources/list"},
        "prompts": {"jsonrpc": JSONRPC, "id": 3, "method": "prompts/list"},
    }
    for kind, frame in probes.items():
        reply = temp.handle(frame)
        if reply and "error" in reply:
            results[kind] = {"ok": False, "code": reply["error"]["code"], "message": reply["error"]["message"]}
        else:
            key = {"tools": "tools", "resources": "resources", "prompts": "prompts"}[kind]
            results[kind] = {"ok": True, "count": len(reply["result"].get(key, []))}
    return {
        "declared": sorted(wanted),
        "capabilities": temp.capabilities(),
        "probes": results,
        "note": "没声明 ≠ 拒绝。-32601 说的是「这里没有这个功能」，客户端据此决定 UI 上要不要显示这一栏。",
    }


@app.get("/api/three-ways")
def three_ways() -> dict[str, Any]:
    """同一个需求「让模型能读请假制度」，三种做法各自长什么样。"""
    return {
        "need": "让模型能读到《请假制度》全文",
        "ways": [
            {
                "as": "tool",
                "shape": "get_leave_doc() → 返回全文",
                "problem": "模型不知道有这份文档存在，除非它恰好先调了 tools/list 并且读懂了描述。它没有任何「该读了」的信号。",
                "verdict": "不合适",
            },
            {
                "as": "resource",
                "shape": "doc://handbook/leave（text/markdown）",
                "problem": "宿主在 UI 里列出它、用户勾选或自动挂载。模型看到的就是一段现成的上下文。",
                "verdict": "合适",
            },
            {
                "as": "prompt",
                "shape": "summarize(text=...) → 一段 user 消息",
                "problem": "prompt 只产出消息，不产出内容。用户得先有文本才能用 —— 拿不到原文。",
                "verdict": "不合适",
            },
        ],
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


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


def call(frame: dict[str, Any], server: DemoServer | None = None) -> dict[str, Any]:
    srv = server or SERVER
    reply = srv.handle(frame)
    assert reply is not None, "自检里不该发通知"
    return reply


def req(msg_id: Any, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    f: dict[str, Any] = {"jsonrpc": JSONRPC, "id": msg_id, "method": method}
    if params is not None:
        f["params"] = params
    return f


def main() -> None:
    from fastapi.testclient import TestClient

    print("\n=== Unit 48 · 三大能力 自检 ===\n")

    # —— 控制轴 ——
    print("  —— 谁决定用它 ——")
    check("三种能力都有定义", set(CONTROL_AXIS) == {"tools", "resources", "prompts"}, str(sorted(CONTROL_AXIS)))
    check("tools 归模型", CONTROL_AXIS["tools"]["owner"] == "模型", CONTROL_AXIS["tools"]["owner"])
    check("resources 归应用", "应用" in CONTROL_AXIS["resources"]["owner"], CONTROL_AXIS["resources"]["owner"])
    check("prompts 归用户", CONTROL_AXIS["prompts"]["owner"] == "用户", CONTROL_AXIS["prompts"]["owner"])
    check("三种 owner 互不相同", len({v["owner"] for v in CONTROL_AXIS.values()}) == 3, "有重复")
    check("每类都写了放错的后果", all(v.get("wrong_use") for v in CONTROL_AXIS.values()), "有缺")
    check(
        "只有 tools 允许副作用",
        CONTROL_AXIS["tools"]["side_effect"].startswith("允许")
        and "不该有" in CONTROL_AXIS["resources"]["side_effect"],
        "副作用那一列写错了",
    )

    # —— URI 模板 ——
    print("  —— URI 模板 ——")
    tpl = UriTemplate("doc://kb/{topic}")
    check("模板识别出 1 个变量", tpl.vars == ["topic"], str(tpl.vars))
    check("能匹配简单 URI", tpl.match("doc://kb/expense") == {"topic": "expense"}, str(tpl.match("doc://kb/expense")))
    check("匹配不到返回 None", tpl.match("doc://other/expense") is None, str(tpl.match("doc://other/expense")))
    check("不跨斜杠", tpl.match("doc://kb/a/b") is None, "居然匹配了多段路径")
    check("expand 能拼回去", tpl.expand(topic="onboarding") == "doc://kb/onboarding", tpl.expand(topic="onboarding"))
    check(
        "expand 缺参数报 -32602",
        _code(lambda: tpl.expand()) == -32602,
        str(_code(lambda: tpl.expand())),
    )
    two = UriTemplate("doc://{ns}/{topic}/v{n}")
    check("多变量模板都能识别", two.vars == ["ns", "topic", "n"], str(two.vars))
    check("多变量能匹配", two.match("doc://kb/expense/v2") == {"ns": "kb", "topic": "expense", "n": "2"}, str(two.match("doc://kb/expense/v2")))
    check("斜杠被正确转义（不误匹配）", two.match("docX//kb/expense/v2") is None, "正则没转义")

    # —— 能力声明 ——
    print("  —— 能力协商 ——")
    caps = SERVER.capabilities()
    check("三种能力都声明了", set(caps) == {"tools", "resources", "prompts"}, str(sorted(caps)))
    check("resources 带 subscribe 字段", "subscribe" in caps["resources"], str(caps["resources"]))
    check("listChanged 默认 false", all(v.get("listChanged") is False for v in caps.values()), str(caps))
    init = call(req(1, "initialize"))["result"]
    check("initialize 返回 serverInfo", init["serverInfo"]["name"] == "company-kb", str(init.get("serverInfo")))
    check("initialize 返回协议版本", init["protocolVersion"] == "2025-06-18", init.get("protocolVersion"))

    only_tools = DemoServer(capabilities={"tools"})
    check("只声明 tools 时能力表只有 tools", list(only_tools.capabilities()) == ["tools"], str(list(only_tools.capabilities())))
    r = call(req(2, "resources/list"), only_tools)
    check("未声明 resources → -32601", r["error"]["code"] == -32601, str(r))
    check("错误 data 里列出已声明项", r["error"]["data"]["declared"] == ["tools"], str(r["error"].get("data")))
    r = call(req(3, "prompts/list"), only_tools)
    check("未声明 prompts → -32601", r["error"]["code"] == -32601, str(r))
    check("但 tools/list 仍可用", len(call(req(4, "tools/list"), only_tools)["result"]["tools"]) == 3, "tools 也不可用了")

    # —— tools ——
    print("  —— Tools ——")
    SERVER.reset()
    tools = call(req(10, "tools/list"))["result"]["tools"]
    check("工具清单 3 个", len(tools) == 3, f"实际 {len(tools)}")
    check("每个工具都有 description", all(t.get("description") for t in tools), "有缺 description 的")
    check("每个工具都有 inputSchema", all(t.get("inputSchema", {}).get("type") == "object" for t in tools), "schema 不对")
    reserve = next(t for t in tools if t["name"] == "reserve_stock")
    check("写操作声明 destructiveHint", reserve["annotations"].get("destructiveHint") is True, str(reserve.get("annotations")))
    check("写操作声明 readOnlyHint=false", reserve["annotations"].get("readOnlyHint") is False, str(reserve.get("annotations")))
    search = next(t for t in tools if t["name"] == "search_products")
    check("只读工具声明 readOnlyHint", search["annotations"].get("readOnlyHint") is True, str(search.get("annotations")))

    r = call(req(11, "tools/call", {"name": "search_products", "arguments": {"keyword": "键盘"}}))
    check("只读工具调用成功", r["result"]["isError"] is False, str(r))
    check("返回命中商品", "机械键盘" in r["result"]["content"][0]["text"], r["result"]["content"][0]["text"])
    check("未命中给的是提示而非报错", "没有匹配" in call(req(12, "tools/call", {"name": "search_products", "arguments": {"keyword": "不存在"}}))["result"]["content"][0]["text"], "没命中就报错了")

    r = call(req(13, "tools/call", {"name": "reserve_stock", "arguments": {"sku": "B-200", "qty": 2}}))
    check("写操作调用成功", r["result"]["isError"] is False, str(r))
    check("库存真的扣了", "剩余 1 件" in r["result"]["content"][0]["text"], r["result"]["content"][0]["text"])
    r = call(req(14, "tools/call", {"name": "reserve_stock", "arguments": {"sku": "B-200", "qty": 99}}))
    check("库存不足是内容不是协议错误", r["result"]["isError"] is False and "库存不足" in r["result"]["content"][0]["text"], str(r))
    total = call(req(15, "tools/call", {"name": "count_items", "arguments": {}}))["result"]["content"][0]["text"]
    # 初始 12+3+27=42，前面预留了 2 件 B-200，所以必须是 40。写死数字，否则「没变」这种断言永远成立。
    check("总库存精确等于 40 件", total.endswith("40 件"), total)

    r = call(req(15, "tools/call", {"name": "nope", "arguments": {}}))
    check("未知工具 → -32602", r["error"]["code"] == -32602, str(r))
    check("未知工具列出可用项", r["error"]["data"]["available"] == ["count_items", "reserve_stock", "search_products"], str(r["error"]["data"]))
    r = call(req(16, "tools/call", {"name": "reserve_stock", "arguments": {"sku": "A-100"}}))
    check("缺必填参数 → -32602", r["error"]["code"] == -32602, str(r))
    check("缺参数报出缺哪个", "qty" in r["error"]["message"], r["error"]["message"])

    # —— resources ——
    print("  —— Resources ——")
    check("固定资源 2 个", len(call(req(20, "resources/list"))["result"]["resources"]) == 2, "数量不对")
    check("模板单独列（不在 resources 里）", "doc://kb/{topic}" not in [r["uri"] for r in call(req(21, "resources/list"))["result"]["resources"]], "模板混进固定列表了")
    tpls = call(req(22, "resources/templates/list"))["result"]["resourceTemplates"]
    check("模板清单 1 个", len(tpls) == 1, f"实际 {len(tpls)}")
    check("模板用 uriTemplate 字段", tpls[0].get("uriTemplate") == "doc://kb/{topic}", str(tpls[0]))

    r = call(req(23, "resources/read", {"uri": "doc://handbook/leave"}))["result"]["contents"][0]
    check("读固定资源成功", "年假" in r["text"], r["text"][:60])
    check("带 mimeType", r["mimeType"] == "text/markdown", r.get("mimeType"))
    check("uri 原样回传", r["uri"] == "doc://handbook/leave", r.get("uri"))
    r = call(req(24, "resources/read", {"uri": "doc://kb/expense"}))["result"]["contents"][0]
    check("模板资源能读", "报销" in r["text"], r["text"][:60])
    check("模板资源保留请求的 uri", r["uri"] == "doc://kb/expense", r.get("uri"))
    r = call(req(25, "resources/read", {"uri": "doc://kb/unknown"}))
    check("未知主题 → -32602", r["error"]["code"] == -32602, str(r))
    check("未知主题列出已知项", "onboarding" in r["error"]["data"]["known"], str(r["error"]["data"]))
    r = call(req(26, "resources/read", {"uri": "doc://nothing"}))
    check("未知 URI → -32602", r["error"]["code"] == -32602, str(r))
    check("未知 URI 列出模板", r["error"]["data"]["templates"] == ["doc://kb/{topic}"], str(r["error"]["data"]))

    before = call(req(27, "resources/read", {"uri": "doc://changelog"}))["result"]["contents"][0]["text"]
    after = call(req(28, "resources/read", {"uri": "doc://changelog"}))["result"]["contents"][0]["text"]
    check("resource 读两次结果一致（只读）", before == after, "两次读出来不一样，说明 resource 有状态")

    # —— prompts ——
    print("  —— Prompts ——")
    prompts = call(req(30, "prompts/list"))["result"]["prompts"]
    check("提示词 2 个", len(prompts) == 2, f"实际 {len(prompts)}")
    check("每个提示词都声明 arguments", all("arguments" in p for p in prompts), "有缺 arguments 的")
    sump = next(p for p in prompts if p["name"] == "summarize")
    check("summarize 有 1 个必填参数", sump["arguments"][0]["required"] is True, str(sump["arguments"]))
    review = next(p for p in prompts if p["name"] == "review_sql")
    check("review_sql 有 2 个参数", len(review["arguments"]) == 2, str(review["arguments"]))
    check("dialect 是非必填", review["arguments"][1]["required"] is False, str(review["arguments"][1]))

    r = call(req(31, "prompts/get", {"name": "summarize", "arguments": {"text": "很长的文本"}}))
    check("prompts/get 返回 messages", isinstance(r["result"]["messages"], list), str(type(r["result"]["messages"])))
    check("消息是 user 角色", r["result"]["messages"][0]["role"] == "user", r["result"]["messages"][0]["role"])
    check("内容在 content.text 里", "很长的文本" in r["result"]["messages"][0]["content"]["text"], str(r["result"]["messages"][0]))
    r = call(req(32, "prompts/get", {"name": "review_sql", "arguments": {"sql": "SELECT 1"}}))
    check("非必填参数可省略并走默认", "sqlite" in r["result"]["messages"][0]["content"]["text"], r["result"]["messages"][0]["content"]["text"])
    r = call(req(33, "prompts/get", {"name": "review_sql", "arguments": {"sql": "SELECT 1", "dialect": "postgres"}}))
    check("非必填参数可覆盖", "postgres" in r["result"]["messages"][0]["content"]["text"], r["result"]["messages"][0]["content"]["text"])
    r = call(req(34, "prompts/get", {"name": "summarize", "arguments": {}}))
    check("缺必填参数 → -32602", r["error"]["code"] == -32602, str(r))
    check("缺参数带回完整声明", len(r["error"]["data"]["args"]) == 1, str(r["error"]["data"]))
    r = call(req(35, "prompts/get", {"name": "nope", "arguments": {}}))
    check("未知提示词 → -32602", r["error"]["code"] == -32602, str(r))

    # —— 挑刺器 ——
    print("  —— 放错格子会被挑出来 ——")
    cases = lint_demo()
    by_name = {c["name"]: c for c in cases}
    check("只读工具被建议改 resource", by_name["get_leave_doc"]["suggestion"] == "resource", str(by_name["get_leave_doc"]))
    check("只读工具问题码正确", by_name["get_leave_doc"]["issues"][0]["code"] == "READONLY_TOOL_LOOKS_LIKE_RESOURCE", str(by_name["get_leave_doc"]["issues"]))
    check("resource 里有写操作被挑出", by_name["cancel_order"]["suggestion"] == "tool", str(by_name["cancel_order"]))
    check("resource 写操作问题码正确", by_name["cancel_order"]["issues"][0]["code"] == "SIDE_EFFECT_IN_RESOURCE", str(by_name["cancel_order"]["issues"]))
    check("prompt 名字像能力被挑出", not by_name["search_products"]["ok"], str(by_name["search_products"]))
    check("正常的写操作工具不被误报", by_name["reserve_stock"]["ok"] is True, str(by_name["reserve_stock"]))
    check("自相矛盾的注解被挑出", any(i["code"] == "CONTRADICTORY_ANNOTATIONS" for i in by_name["count_items"]["issues"]), str(by_name["count_items"]["issues"]))
    check("挑出数 = 4", sum(1 for c in cases if not c["ok"]) == 4, f"实际 {sum(1 for c in cases if not c['ok'])}")

    # —— 同一需求的三种做法 ——
    print("  —— 同一需求，三种做法 ——")
    ways = {w["as"]: w for w in [
        {"as": "tool", "verdict": "不合适"},
        {"as": "resource", "verdict": "合适"},
        {"as": "prompt", "verdict": "不合适"},
    ]}
    check("读文档需求只有 resource 合适", ways["resource"]["verdict"] == "合适", str(ways))

    # —— HTTP 层 ——
    print("  —— HTTP 层 ——")
    SERVER.reset()
    client = TestClient(app)
    check("/health 200", client.get("/health").status_code == 200)
    h = client.get("/health").json()
    check("health 报出能力表", set(h["capabilities"]) == {"tools", "resources", "prompts"}, str(h["capabilities"]))
    check("health 报出各类数量", h["counts"] == {"tools": 3, "resources": 2, "templates": 1, "prompts": 2}, str(h["counts"]))

    r = client.post("/rpc", json={"frame": req(1, "tools/list")})
    check("HTTP /rpc 可用", r.status_code == 200 and len(r.json()["result"]["tools"]) == 3, r.text[:120])
    r = client.post("/rpc", json={"frame": {"jsonrpc": JSONRPC, "method": "notifications/initialized"}})
    check("通知返回 202", r.status_code == 202, str(r.status_code))
    r = client.post("/rpc", json={"frame": req(2, "resources/read", {"uri": "doc://nope"})})
    check("HTTP 上错误帧也是 200", r.status_code == 200 and r.json()["error"]["code"] == -32602, r.text[:120])

    r = client.get("/api/caps/tools").json()
    check("裁剪接口只声明 tools", r["declared"] == ["tools"], str(r["declared"]))
    check("裁剪后 resources 探测失败", r["probes"]["resources"]["ok"] is False, str(r["probes"]))
    check("裁剪后 resources 报 -32601", r["probes"]["resources"]["code"] == -32601, str(r["probes"]["resources"]))
    r = client.get("/api/caps/resources").json()
    check("只声明 resources 时 tools 报 -32601", r["probes"]["tools"]["code"] == -32601, str(r["probes"]["tools"]))
    check("只声明 resources 时 resources 可用", r["probes"]["resources"]["ok"] is True, str(r["probes"]["resources"]))

    r = client.get("/api/lint").json()
    check("lint 接口报出 4 条问题", r["flagged"] == 4, str(r["flagged"]))
    check("lint 接口 total 为 5", r["total"] == 5, str(r["total"]))
    r = client.get("/api/three-ways").json()
    check("three-ways 给出三种做法", len(r["ways"]) == 3, str(len(r["ways"])))
    r = client.get("/api/axis").json()
    check("axis 接口返回控制轴", len(r["axis"]) == 3, str(len(r["axis"])))

    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200)
    check("页面含关键文案", "三大能力" in page.text and len(page.text) > 5000, f"{len(page.text)} 字节")
    check("examples 接口有 11 个样例", len(client.get("/api/examples").json()["examples"]) == 11, "数量不对")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


def _code(fn) -> int | None:
    try:
        fn()
        return None
    except RpcError as e:
        return e.code


if __name__ == "__main__":
    main()
