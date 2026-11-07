"""Unit 32 · 参数 Schema —— 模型照 schema 填，代码照 schema 收，两边必须是同一份。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8032

自检（离线）：
    uv run python main.py

本单元只解决一件事：**schema 与校验器同源**。
消息数组怎么回填是 Unit 33 的事，循环是 Unit 34 的事，这里刻意从简。
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from schema import (
    LEGACY_SCHEMA,
    TOOL_NAMES,
    TOOL_SPECS,
    LegacyError,
    build_tool,
    execute,
    flatten_schema,
    legacy_validate,
    schema_diff,
    to_openai_tools,
    validate,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 32 · 参数 Schema", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SYSTEM = (
    "你是一个订单助手。调用工具时必须严格遵守 tools 里的 JSON Schema："
    "枚举字段只能填枚举里的值，不要翻译成中文，不要多传未定义的字段。"
)


# ---------------------------------------------------------------------------
# 离线模型：规则驱动。它「会犯错」，而且错得很真实 —— 把枚举值翻译成中文。
# ---------------------------------------------------------------------------
class OfflineModel:
    name = "offline-rules"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict]) -> dict:
        self.calls += 1

        if messages[-1]["role"] == "tool":
            return {"role": "assistant", "content": self._summarize(messages)}

        question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        name, args = self._decide(question)
        if name is None:
            return {"role": "assistant", "content": "这个问题不需要工具。"}
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
                }
            ],
        }

    def _decide(self, question: str) -> tuple[str | None, dict]:
        if "退款" in question:
            oid = re.search(r"SO-\d{6}", question)
            amount = re.search(r"(\d+(?:\.\d+)?)\s*元", question)
            return "refund_order", {
                "order_id": oid.group(0) if oid else "SO-000123",
                "amount": float(amount.group(1)) if amount else 899.0,
                "reason": "质量问题" if "质量" in question else "七天无理由",
            }
        if "会议" in question or "预约" in question:
            # 模型从自然语言里抠时间，抠不出合法格式是常态
            return "schedule_meeting", {
                "title": "周会",
                "start": "下周二下午两点半" if "下午" in question else "2026-11-03T14:30",
                "attendees": ["zhang@example.com"],
                "room": {"building": "A 座", "floor": 3},
            }
        if "订单" in question or "耳机" in question or "买" in question:
            args: dict[str, Any] = {"keyword": "耳机" if "耳机" in question else "订单"}
            # 这是本单元最重要的一幕：模型把枚举值当自然语言翻成了中文
            if "已发货" in question:
                args["status"] = "已发货"
            elif "退款" in question:
                args["status"] = "refunded"
            return "search_orders", args
        return None, {}

    def _summarize(self, messages: list[dict]) -> str:
        out = []
        for m in messages:
            if m["role"] != "tool":
                continue
            payload = json.loads(m["content"])
            if "error" in payload:
                first = payload["error"][0]
                out.append(f"工具拒绝了这次调用：字段 {first['path'] or '(整体)'} {first['msg']}")
            elif "count" in payload:
                out.append(f"共 {payload['count']} 条订单")
            else:
                out.append(f"已处理：{json.dumps(payload, ensure_ascii=False)}")
        return "；".join(out) + "。"


def chat_api(messages: list[dict]) -> dict:
    """真模型版本，接口与 OfflineModel.complete 相同。自检永远不走这条。"""
    import httpx

    base = os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("没有配置 DEEPSEEK_API_KEY / OPENAI_API_KEY")
    with httpx.Client(timeout=60) as c:
        r = c.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": os.getenv("OPENAI_MODEL", "deepseek-flash"),
                "messages": messages,
                "tools": to_openai_tools(),
                "tool_choice": "auto",
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


# ---------------------------------------------------------------------------
# 一轮问答：这里只看「校验结果」，回填细节留给 Unit 33
# ---------------------------------------------------------------------------
def run_once(question: str, model=None, max_rounds: int = 2) -> dict:
    model = model or OfflineModel()
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": question},
    ]
    trace: list[dict] = []
    for round_no in range(1, max_rounds + 1):
        msg = model.complete(messages)
        trace.append({"round": round_no, "type": "model", "response": msg})
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"answer": msg.get("content") or "", "trace": trace, "rounds": round_no, "stopped": "answer"}
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
        for call in calls:
            name = call["function"]["name"]
            raw = call["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {}
            result = execute(name, args)
            trace.append(
                {
                    "round": round_no,
                    "type": "tool",
                    "tool": name,
                    "args": args,
                    "raw_arguments": raw,
                    "ok": result["ok"],
                    "errors": result.get("errors", []),
                    "content": result["content"],
                }
            )
            messages.append({"role": "tool", "tool_call_id": call["id"], "name": name, "content": result["content"]})
    return {"answer": "达到轮次上限", "trace": trace, "rounds": max_rounds, "stopped": "max_rounds"}


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class ValidateIn(BaseModel):
    name: str = Field(min_length=1)
    arguments: str = Field(default="{}", description="模型返回的 arguments 原文（字符串）")


class ChatIn(BaseModel):
    question: str = Field(min_length=1)


@app.get("/health", summary="三个工具与当前模型")
def health() -> dict:
    return {"ok": True, "tools": list(TOOL_NAMES), "model": os.getenv("OPENAI_MODEL") or "offline-rules"}


@app.get("/tools", summary="给模型看的 tools 数组（已摊平）")
def tools() -> dict:
    return {"tools": to_openai_tools()}


@app.get("/schema/{name}", summary="同一份契约的两个视图")
def one_schema(name: str) -> dict:
    if name not in TOOL_SPECS:
        return {"error": f"未知工具 {name}", "tools": list(TOOL_NAMES)}
    model, desc, _ = TOOL_SPECS[name]
    raw = model.model_json_schema()
    return {
        "name": name,
        "description": desc,
        "pydantic": raw,
        "openai": flatten_schema(raw),
        "stats": {
            "defs": len(raw.get("$defs") or {}),
            "refs_before": json.dumps(raw).count('"$ref"'),
            "refs_after": json.dumps(flatten_schema(raw)).count('"$ref"'),
            "params_bytes": len(json.dumps(flatten_schema(raw), ensure_ascii=False)),
        },
    }


@app.post("/validate", summary="校验模型返回的 arguments，错误带路径")
def do_validate(req: ValidateIn) -> dict:
    try:
        args = json.loads(req.arguments)
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [{"path": "", "type": "json_decode_error", "msg": f"arguments 不是合法 JSON：{e.msg}"}]}
    v = validate(req.name, args)
    out = {"ok": v["ok"], "errors": v.get("errors", [])}
    if v["ok"]:
        out["normalized"] = v["args"]
        out["result"] = execute(req.name, args)["data"]
        out["note"] = "normalized 里只有模型真正传了的字段；默认值由执行函数的签名补"
    return out


@app.get("/drift", summary="手写 schema 与生成 schema 的漂移报告")
def drift() -> dict:
    generated = build_tool("search_orders")["function"]["parameters"]
    case_ok = {"keyword": "耳机", "status": "refunded"}
    try:
        legacy_validate(dict(case_ok))
        legacy_verdict = "accept"
    except LegacyError as e:
        legacy_verdict = f"reject: {e}"
    return {
        "generated": generated,
        "legacy": LEGACY_SCHEMA,
        "diffs": schema_diff(generated, LEGACY_SCHEMA),
        "case": case_ok,
        "generated_verdict": "accept" if validate("search_orders", case_ok)["ok"] else "reject",
        "legacy_verdict": legacy_verdict,
    }


@app.post("/chat", summary="一问一答，能看到 schema 在哪里拒绝了模型")
def chat(req: ChatIn) -> dict:
    if os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY"):
        try:
            return run_once(req.question, chat_api)
        except Exception as e:  # noqa: BLE001
            out = run_once(req.question)
            out["fallback_reason"] = f"{type(e).__name__}: {e}"
            return out
    return run_once(req.question)


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

    print("Unit 32 · 参数 Schema 自检（离线）\n")

    so = build_tool("search_orders")["function"]["parameters"]
    meeting = build_tool("schedule_meeting")["function"]["parameters"]
    refund = build_tool("refund_order")["function"]["parameters"]

    print("  —— 必填与默认值：schema 自己在说 ——")
    check("search_orders 的 required 只有 keyword", so["required"] == ["keyword"], str(so["required"]))
    check("limit 的默认值写进了 schema", so["properties"]["limit"].get("default") == 10)
    check("status 的默认值写进了 schema", so["properties"]["status"].get("default") == "pending")
    check("有默认值的字段不出现在 required", "limit" not in so["required"] and "status" not in so["required"])
    check("nest 模型的必填被带上（room）", meeting["required"] == ["title", "start", "attendees", "room"], str(meeting["required"]))

    print("\n  —— enum / 边界 / 严格模式 ——")
    check("status 是 enum 而不是自由字符串", so["properties"]["status"]["enum"] == ["pending", "paid", "shipped", "refunded"])
    check("refund 的 reason 是中文枚举", refund["properties"]["reason"]["enum"] == ["质量问题", "七天无理由", "发错货", "其他"])
    check("limit 的上下界写进了 schema", so["properties"]["limit"]["minimum"] == 1 and so["properties"]["limit"]["maximum"] == 50)
    check("order_id 的格式约束写进了 schema", refund["properties"]["order_id"]["pattern"] == r"^SO-\d{6}$")
    check("三个工具都带了 additionalProperties:false", all(build_tool(n)["function"]["parameters"]["additionalProperties"] is False for n in TOOL_NAMES))
    check("description 一律非空（它决定模型会不会填对）", all(build_tool(n)["function"]["description"].strip() for n in TOOL_NAMES))
    check("每个参数都有自己的 description", all("description" in v for v in so["properties"].values()))

    print("\n  —— $defs / $ref 必须摊平 ——")
    raw_meeting = TOOL_SPECS["schedule_meeting"][0].model_json_schema()
    check("原始 schema 确实产生了 $defs", bool(raw_meeting.get("$defs")), str(list(raw_meeting.get("$defs") or {})))
    check("原始 schema 里有 $ref", '"$ref"' in json.dumps(raw_meeting))
    check("摊平后没有 $ref", '"$ref"' not in json.dumps(meeting))
    check("摊平后没有 $defs", "$defs" not in meeting)
    check("$ref 的内容真的被内联了（room.floor）", meeting["properties"]["room"]["properties"]["floor"]["maximum"] == 30)
    check("内联后 room 也是严格模式", meeting["properties"]["room"]["additionalProperties"] is False)
    check('title 这个 schema 关键字被剥掉了（省 token，模型不需要）', '"title":' not in json.dumps(meeting, ensure_ascii=False))
    check("摊平不会破坏 $ref 旁边的 description", meeting["properties"]["room"].get("description") == "会议室，包含楼名和楼层两个字段。")

    print("\n  —— 校验器：错误必须能定位到字段 ——")
    v = validate("search_orders", {"status": "refunded", "limit": 5})
    check("缺必填被拒", not v["ok"] and v["errors"][0]["path"] == "keyword", str(v))
    v = validate("search_orders", {"keyword": "耳机", "status": "已发货"})
    check("枚举外被拒", not v["ok"] and v["errors"][0]["path"] == "status", str(v))
    check("枚举外的错误类型是 literal_error", v["errors"][0]["type"] == "literal_error", str(v))
    v = validate("search_orders", {"keyword": "耳机", "admin": True})
    check("未定义字段被拒（additionalProperties=false 生效）", not v["ok"] and "admin" in v["errors"][0]["path"], str(v))
    v = validate("search_orders", {"keyword": "耳机", "limit": "十"})
    check("类型错被拒", not v["ok"] and v["errors"][0]["path"] == "limit", str(v))
    v = validate("search_orders", {"keyword": "耳机", "limit": 0})
    check("低于下界被拒", not v["ok"] and v["errors"][0]["type"] == "greater_than_equal", str(v))
    v = validate("schedule_meeting", {"title": "周会", "start": "下周二下午", "attendees": ["a@b.c"], "room": {"building": "A 座", "floor": 3}})
    check("时间格式错被拒（pattern 生效）", not v["ok"] and v["errors"][0]["path"] == "start", str(v))
    v = validate("schedule_meeting", {"title": "周会", "start": "2026-11-03T14:30", "attendees": ["a@b.c"], "room": {"building": "A 座", "floor": 99}})
    check("嵌套字段的错误路径带层级（room.floor）", not v["ok"] and v["errors"][0]["path"] == "room.floor", str(v))
    v = validate("search_orders", {"keyword": "耳机"})
    check("合法参数通过", v["ok"] and v["args"] == {"keyword": "耳机"}, str(v))
    check("默认值不混进 normalized（日志不能骗人）", "limit" not in validate("search_orders", {"keyword": "耳机"})["args"])
    v = validate("rm_rf", {})
    check("未知工具被拒", not v["ok"] and v["errors"][0]["type"] == "unknown_tool", str(v))
    v = validate("search_orders", "not-a-dict")
    check("arguments 不是对象被拒", not v["ok"] and v["errors"][0]["type"] == "type_error", str(v))

    print("\n  —— 漂移：手写 schema + 手写校验器 = 两份契约 ——")
    diffs = schema_diff(so, LEGACY_SCHEMA)
    check("漂移被检出", len(diffs) > 0, str(len(diffs)))
    check("漂移点包含 status 的枚举差异", any("status.enum" in d for d in diffs), str(diffs)[:120])
    check("漂移点包含 required 差异", any(d.startswith("required") for d in diffs), str(diffs)[:120])
    check("漂移点包含 additionalProperties 差异", any("additionalProperties" in d for d in diffs), str(diffs)[:120])
    case = {"keyword": "耳机", "status": "refunded"}
    check("同一份输入：生成 schema 接受", validate("search_orders", case)["ok"])
    legacy_rejected = False
    try:
        legacy_validate(dict(case))
    except LegacyError:
        legacy_rejected = True
    check("同一份输入：手写校验器拒绝（漂移是真实 bug）", legacy_rejected)
    legacy_accepts_extra = True
    try:
        legacy_validate({"keyword": "耳机", "limit": 5, "admin": True})
    except LegacyError:
        legacy_accepts_extra = False
    check("反方向也漂移：手写校验器放过未定义字段", legacy_accepts_extra and not validate("search_orders", {"keyword": "耳机", "admin": True})["ok"])

    print("\n  —— OpenAI tools 格式能被接受 ——")
    ts = to_openai_tools()
    check("三个工具都在", len(ts) == 3 and {t["function"]["name"] for t in ts} == set(TOOL_NAMES))
    check("每个都是 type=function", all(t["type"] == "function" for t in ts))
    check("每个 parameters 都是 type=object", all(t["function"]["parameters"]["type"] == "object" for t in ts))
    schemas_ok = all(set(t) == {"type", "function"} and set(t["function"]) == {"name", "description", "parameters"} for t in ts)
    check("顶层字段不多不少", schemas_ok)
    check("整包能 JSON 序列化", isinstance(json.dumps(ts, ensure_ascii=False), str))

    print("\n  —— 模型真的会填错 ——")
    out = run_once("帮我查一下已发货的耳机订单")
    tool_step = next(t for t in out["trace"] if t["type"] == "tool")
    check("模型把枚举值翻成了中文", tool_step["args"].get("status") == "已发货", str(tool_step["args"]))
    check("校验在 schema 层就拦下了", tool_step["ok"] is False and tool_step["errors"][0]["path"] == "status")
    check("拒绝被说成人话而不是 500", "status" in out["answer"] or "拒绝了" in out["answer"], out["answer"])

    out2 = run_once("查一下耳机订单")
    check("合法调用能一路走通", out2["trace"][-1].get("ok") is True or "条订单" in out2["answer"], out2["answer"])

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    check("GET /health", client.get("/health").status_code == 200)
    body = client.get("/tools").json()
    check("GET /tools 返回摊平后的 tools", '"$ref"' not in json.dumps(body))
    body = client.get("/schema/refund_order").json()
    check("GET /schema 给出两个视图", body["stats"]["refs_before"] == 0 and body["openai"]["required"] == ["order_id", "amount", "reason"])
    body = client.get("/schema/schedule_meeting").json()
    check("GET /schema 统计 $ref 数量", body["stats"]["refs_before"] >= 1 and body["stats"]["refs_after"] == 0, str(body["stats"]))
    body = client.post("/validate", json={"name": "search_orders", "arguments": '{"keyword":"耳机","status":"已发货"}'}).json()
    check("POST /validate 返回错误路径", body["ok"] is False and body["errors"][0]["path"] == "status")
    body = client.post("/validate", json={"name": "search_orders", "arguments": "{"}).json()
    check("POST /validate 拦下非法 JSON", body["errors"][0]["type"] == "json_decode_error")
    body = client.post("/validate", json={"name": "search_orders", "arguments": '{"keyword":"耳机","status":"refunded"}'}).json()
    check("POST /validate 合法时给出归一化参数", body["ok"] and body["normalized"]["status"] == "refunded")
    body = client.get("/drift").json()
    check("GET /drift 结论相反", body["generated_verdict"] == "accept" and body["legacy_verdict"].startswith("reject"))
    body = client.post("/chat", json={"question": "帮我查一下已发货的耳机订单"}).json()
    check("POST /chat 带着被拒的 trace", any(t.get("errors") for t in body["trace"] if t["type"] == "tool"))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
