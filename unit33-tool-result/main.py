"""Unit 33 · Tool Result 回填 —— 结果要变成一条消息，而不是一个返回值。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8033

自检（离线）：
    uv run python main.py

只解决一件事：**messages 数组的形状**。
参数怎么校验是 Unit 32，循环怎么转是 Unit 34，这里刻意只做「一轮 + 回填 + 校验」。
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from messages import (
    MAX_TOOL_CONTENT,
    append_tool_results,
    count_tokens,
    error_result,
    est_tokens,
    ok_result,
    strip_local_fields,
    summarise_problems,
    to_content,
    validate_messages,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 33 · Tool Result 回填", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SYSTEM = "你是工具助手。工具返回什么就用什么，不要编造。"

# ---------------------------------------------------------------------------
# 工具：一个小的，一个故意大的（用来演示截断）
# ---------------------------------------------------------------------------
WEATHER = {"上海": (18, "多云"), "北京": (9, "晴"), "广州": (24, "小雨"), "深圳": (25, "阴")}

ORDERS = [
    {
        "order_id": f"SO-{i:06d}",
        "buyer": ["张三", "李四", "王五"][i % 3],
        "item": ["降噪耳机", "机械键盘", "显示器支架"][i % 3],
        "status": ["paid", "shipped", "refunded"][i % 3],
        "amount": 199.0 + i * 37,
        "created_at": f"2026-10-{i % 28 + 1:02d}T10:00:00",
        "address": f"上海市浦东新区某某路 {100 + i} 号 {i} 室",
    }
    for i in range(40)
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询城市今天的天气。",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "enum": list(WEATHER)}},
                "required": ["city"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_orders",
            "description": "搜订单。返回 40 条完整记录时体积很大，用来演示截断。",
            "parameters": {
                "type": "object",
                "properties": {"keyword": {"type": "string"}},
                "required": ["keyword"],
                "additionalProperties": False,
            },
        },
    },
]


def run_tool(name: str, args: dict) -> dict:
    """执行工具，返回统一形状的结果（成功/失败都在里面）。"""
    if name == "get_weather":
        city = args.get("city")
        if city not in WEATHER:
            return error_result("bad_city", f"没有 {city} 的天气数据")
        temp, sky = WEATHER[city]
        return ok_result({"city": city, "temp": temp, "sky": sky})
    if name == "search_orders":
        kw = args.get("keyword") or ""
        hits = [o for o in ORDERS if kw in o["order_id"] or kw in o["buyer"] or kw in o["item"]]
        return ok_result({"count": len(hits), "items": hits})
    return error_result("unknown_tool", f"未知工具 {name}")


# ---------------------------------------------------------------------------
# 离线模型：会一次要两个工具（并行），也会要一个会返回巨大结果的工具
# ---------------------------------------------------------------------------
class OfflineModel:
    name = "offline-rules"

    def complete(self, messages: list[dict]) -> dict:
        if messages[-1]["role"] == "tool":
            return {"role": "assistant", "content": self._answer(messages)}

        question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        cities = [c for c in WEATHER if c in question]
        if len(cities) >= 2:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": json.dumps({"city": c}, ensure_ascii=False)},
                    }
                    for c in cities[:3]
                ],
            }
        if cities:
            return self._call("get_weather", {"city": cities[0]})
        if "订单" in question:
            return self._call("search_orders", {"keyword": "耳机" if "耳机" in question else ""})
        return {"role": "assistant", "content": "这个问题不需要工具。"}

    @staticmethod
    def _call(name: str, args: dict) -> dict:
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

    @staticmethod
    def _answer(messages: list[dict]) -> str:
        parts = []
        for m in messages:
            if m["role"] != "tool":
                continue
            payload = json.loads(m["content"])
            if payload.get("truncated"):
                parts.append(f"结果太大（原 {payload['original_chars']} 字符），只看了前 1200 字符")
            elif "error" in payload:
                parts.append(f"{payload['error']['type']}：{payload['error']['message']}")
            elif "count" in payload["data"]:
                parts.append(f"命中 {payload['data']['count']} 条订单")
            else:
                d = payload["data"]
                parts.append(f"{d['city']}{d['sky']}{d['temp']}°C")
        return "；".join(parts) + "。"


def chat_api(messages: list[dict]) -> dict:
    """真模型版本（自检永不调用）。"""
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
                "model": os.getenv("OPENAI_MODEL", "deepseek-chat"),
                "messages": [strip_local_fields(m) for m in messages],
                "tools": TOOLS,
                "tool_choice": "auto",
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


# ---------------------------------------------------------------------------
# 一轮：模型 → 执行 → 回填 → 再问一次
# ---------------------------------------------------------------------------
def run_turn(question: str, model=None) -> dict:
    model = model or OfflineModel()
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": question},
    ]
    assistant = model.complete(messages)

    if not assistant.get("tool_calls"):
        messages.append({"role": "assistant", "content": assistant.get("content")})
        return {
            "messages": messages,
            "assistant": assistant,
            "results": [],
            "problems": validate_messages(messages),
            "tokens": count_tokens(messages),
            "answer": assistant.get("content") or "",
        }

    results = []
    for call in assistant["tool_calls"]:
        name = call["function"]["name"]
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            results.append({"call_id": call["id"], "name": name, "payload": error_result("bad_arguments", "arguments 不是合法 JSON")})
            continue
        results.append({"call_id": call["id"], "name": name, "payload": run_tool(name, args)})

    messages = append_tool_results(messages, assistant, results)
    problems = validate_messages(messages)

    final = model.complete(messages)
    messages.append({"role": "assistant", "content": final.get("content")})
    return {
        "messages": messages,
        "assistant": assistant,
        "results": results,
        "problems": problems,
        "tokens": count_tokens(messages),
        "answer": final.get("content") or "",
        "truncated": [m for m in messages if m.get("truncated")],
    }


# ---------------------------------------------------------------------------
# 四种经典的坏数组（页面上点一下就能看校验器怎么抓）
# ---------------------------------------------------------------------------
def broken_cases() -> dict[str, list[dict]]:
    call = {
        "id": "call_a",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city":"上海"}'},
    }
    base = [{"role": "user", "content": "上海天气"}]
    good_assistant = {"role": "assistant", "content": None, "tool_calls": [call]}
    return {
        "缺一条 tool 消息": base + [good_assistant, {"role": "assistant", "content": "我猜是 18 度"}],
        "tool_call_id 写错": base
        + [good_assistant, {"role": "tool", "tool_call_id": "call_b", "name": "get_weather", "content": "{}"}],
        "tool 消息在 assistant 之前": base
        + [{"role": "tool", "tool_call_id": "call_a", "name": "get_weather", "content": "{}"}, good_assistant],
        "content 塞了 dict": base
        + [good_assistant, {"role": "tool", "tool_call_id": "call_a", "name": "get_weather", "content": {"city": "上海"}}],
        "同一条回填两次": base
        + [
            good_assistant,
            {"role": "tool", "tool_call_id": "call_a", "name": "get_weather", "content": "{}"},
            {"role": "tool", "tool_call_id": "call_a", "name": "get_weather", "content": "{}"},
        ],
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class TurnIn(BaseModel):
    question: str = Field(min_length=1)


class MessagesIn(BaseModel):
    messages: list[dict]


class ContentIn(BaseModel):
    payload: str = Field(default="", description="原始结果文本，用来试截断")
    limit: int = Field(default=MAX_TOOL_CONTENT, ge=1, le=10000)


@app.get("/health", summary="工具与截断上限")
def health() -> dict:
    return {"ok": True, "tools": [t["function"]["name"] for t in TOOLS], "limit": MAX_TOOL_CONTENT}


@app.post("/turn", summary="跑一轮：模型 → 执行 → 回填")
def turn(req: TurnIn) -> dict:
    return run_turn(req.question)


@app.post("/validate", summary="校验一个 messages 数组")
def check(req: MessagesIn) -> dict:
    problems = validate_messages(req.messages)
    return {"problems": problems, "summary": summarise_problems(problems), "tokens": count_tokens(req.messages)}


@app.get("/broken", summary="五种经典坏数组，各自被校验器抓成什么")
def broken() -> dict:
    out = []
    for name, msgs in broken_cases().items():
        problems = validate_messages(msgs)
        out.append({"case": name, "problems": problems, "summary": summarise_problems(problems)})
    return {"cases": out}


@app.post("/truncate", summary="试一次截断，看 content 长什么样")
def truncate(req: ContentIn) -> dict:
    content, did = to_content(req.payload, req.limit)
    out = {"truncated": did, "chars": len(content), "content": content}
    if did:
        out["parsed_ok"] = isinstance(json.loads(content), dict)
        out["naive_cut"] = req.payload[: req.limit]
        try:
            json.loads(out["naive_cut"])
            out["naive_parsed_ok"] = True
        except json.JSONDecodeError:
            out["naive_parsed_ok"] = False
    return out


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

    def codes(problems: list[dict]) -> set[str]:
        return {p["code"] for p in problems}

    print("Unit 33 · Tool Result 回填 自检（离线）\n")

    CALL_A = {"id": "call_a", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"上海"}'}}
    CALL_B = {"id": "call_b", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"北京"}'}}

    print("  —— 合法的数组长什么样 ——")
    good = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": "上海天气"},
        {"role": "assistant", "content": None, "tool_calls": [CALL_A]},
        {"role": "tool", "tool_call_id": "call_a", "name": "get_weather", "content": '{"ok":true}'},
        {"role": "assistant", "content": "上海 18 度"},
    ]
    check("标准五条消息序列合法", validate_messages(good) == [], summarise_problems(validate_messages(good)))

    two = [
        {"role": "user", "content": "上海和北京的天气"},
        {"role": "assistant", "content": None, "tool_calls": [CALL_A, CALL_B]},
        {"role": "tool", "tool_call_id": "call_b", "name": "get_weather", "content": '{"ok":true}'},
        {"role": "tool", "tool_call_id": "call_a", "name": "get_weather", "content": '{"ok":true}'},
        {"role": "assistant", "content": "都查到了"},
    ]
    check("两条 tool 消息顺序可以颠倒（真模型按 id 匹配）", validate_messages(two) == [], summarise_problems(validate_messages(two)))
    check("system 可以不在开头也能过（校验器只管结构）", validate_messages([{"role": "user", "content": "hi"}, {"role": "system", "content": "x"}]) == [])

    print("\n  —— 缺一条就要能被指出来（本章最值钱的部分）——")
    p = validate_messages(good[:3] + [{"role": "assistant", "content": "我猜 18 度"}])
    check("缺 tool 消息被检出", "missing_tool_result" in codes(p), str(p))
    check("缺 tool 消息的错误指向声明它的那条", p[0]["index"] == 2, str(p))
    check("错误信息里带上工具名和 id", "get_weather" in p[0]["msg"] and "call_a" in p[0]["msg"], p[0]["msg"])

    p = validate_messages(good[:3] + [{"role": "tool", "tool_call_id": "call_x", "name": "get_weather", "content": "{}"}])
    check("id 写错同时报两条（孤儿 + 缺失）", {"orphan_tool", "missing_tool_result"} <= codes(p), str(codes(p)))

    p = validate_messages(good[:4] + [{"role": "tool", "tool_call_id": "call_a", "name": "get_weather", "content": "{}"}])
    check("同一条回填两次被检出", "duplicate_tool_result" in codes(p), str(codes(p)))

    p = validate_messages([{"role": "tool", "tool_call_id": "call_a", "name": "get_weather", "content": "{}"}, good[2]])
    check("tool 消息在 assistant 之前被检出", "orphan_tool" in codes(p), str(codes(p)))

    p = validate_messages(good[:3] + [{"role": "tool", "tool_call_id": "call_a", "name": "get_weather", "content": {"city": "上海"}}])
    check("content 塞 dict 被检出", "content_not_str" in codes(p), str(codes(p)))
    p = validate_messages(good[:3] + [{"role": "tool", "tool_call_id": "call_a", "name": "get_weather", "content": ""}])
    check("content 空串被检出", "empty_content" in codes(p), str(codes(p)))
    p = validate_messages(good[:3] + [{"role": "tool", "tool_call_id": "call_a", "name": "calculate", "content": "{}"}])
    check("name 对不上被检出", "name_mismatch" in codes(p), str(codes(p)))
    p = validate_messages([{"role": "robot", "content": "hi"}])
    check("非法 role 被检出", "unknown_role" in codes(p), str(codes(p)))
    p = validate_messages(
        [{"role": "user", "content": "x"}, {"role": "assistant", "tool_calls": [{"id": "c", "type": "function", "function": {"name": "f", "arguments": {"a": 1}}}]}, {"role": "tool", "tool_call_id": "c", "content": "{}"}]
    )
    check("arguments 是对象被检出（必须是 JSON 字符串）", "tool_calls_bad_shape" in codes(p), str(codes(p)))
    p = validate_messages([{"role": "user", "content": "x"}, {"role": "assistant", "tool_calls": [CALL_A]}])
    check("结尾悬挂的调用也被检出", "missing_tool_result" in codes(p), str(codes(p)))

    print("\n  —— append_tool_results：一次调用一条，id 必须对上 ——")
    ctx = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "上海和北京"}]
    asst = {"role": "assistant", "content": None, "tool_calls": [CALL_A, CALL_B]}
    results = [
        {"call_id": "call_b", "name": "get_weather", "payload": ok_result({"city": "北京"})},
        {"call_id": "call_a", "name": "get_weather", "payload": ok_result({"city": "上海"})},
    ]
    out = append_tool_results(ctx, asst, results)
    check("回填后长度 = 原长 + 1 + 2", len(out) == len(ctx) + 3, str(len(out)))
    tool_msgs = [m for m in out if m["role"] == "tool"]
    check("两条 tool 消息都生成了", len(tool_msgs) == 2)
    check("回填顺序按 tool_calls 而不是结果到达顺序", [m["tool_call_id"] for m in tool_msgs] == ["call_a", "call_b"])
    check("回填后的数组通过校验", validate_messages(out) == [], summarise_problems(validate_messages(out)))
    check("assistant 的 content=None 被原样保留", out[2]["content"] is None and out[2]["tool_calls"])

    for label, res in [
        ("缺结果直接抛错（不静默发出）", [results[0]]),
        ("多余结果直接抛错", results + [{"call_id": "call_zzz", "name": "x", "payload": {}}]),
    ]:
        raised = False
        try:
            append_tool_results(ctx, asst, res)
        except ValueError:
            raised = True
        check(label, raised)

    raised = False
    try:
        append_tool_results(ctx, {"role": "assistant", "content": "没有工具"}, [])
    except ValueError:
        raised = True
    check("没有 tool_calls 时回填直接抛错", raised)

    print("\n  —— 序列化：json.dumps 而不是 str(dict) ——")
    payload = ok_result({"city": "上海", "temp": 18})
    content, truncated = to_content(payload)
    check("content 是 str", isinstance(content, str))
    check("中文不被转义成 \\u", "上海" in content)
    check("json 是双引号", '"city"' in content)
    naive = str(payload)
    naive_ok = True
    try:
        json.loads(naive)
    except json.JSONDecodeError:
        naive_ok = False
    check("str(dict) 出来的东西不是合法 JSON", not naive_ok and "'" in naive, naive[:40])
    check("错误结果形状统一（ok=False + error.type/message）", error_result("bad_city", "没有数据") == {"ok": False, "error": {"type": "bad_city", "message": "没有数据"}})

    print("\n  —— 截断：切一半的 JSON 是废的 ——")
    big = ok_result({"items": [{"id": i, "name": "商品名称很长" * 5} for i in range(40)]})
    big_text = json.dumps(big, ensure_ascii=False)
    content, truncated = to_content(big, limit=MAX_TOOL_CONTENT)
    check("超限时被截断", truncated is True)
    check("截断后的 content 仍是合法 JSON", isinstance(json.loads(content), dict))
    env = json.loads(content)
    check("截断信封里标了 truncated=True", env["truncated"] is True)
    check("截断信封里记了原始长度", env["original_chars"] == len(big_text), f"{env.get('original_chars')} vs {len(big_text)}")
    check("preview 长度等于上限", len(env["preview"]) == MAX_TOOL_CONTENT)
    naive_cut = big_text[:MAX_TOOL_CONTENT]
    naive_parse_ok = True
    try:
        json.loads(naive_cut)
    except json.JSONDecodeError:
        naive_parse_ok = False
    check("朴素切片反而变成非法 JSON（所以不能用切片）", not naive_parse_ok)
    small = ok_result({"city": "上海"})
    c2, t2 = to_content(small)
    check("未超限时原样输出、不标截断", t2 is False and json.loads(c2) == small)

    print("\n  —— token 粗估 ——")
    check("两个汉字 ≈ 2 token", est_tokens("中文") == 2, str(est_tokens("中文")))
    check("越长 token 越多（单调）", est_tokens("一二三四五六七八九十") > est_tokens("一二三"))
    check("ASCII 4 字符 ≈ 1 token", est_tokens("abcd") == 1, str(est_tokens("abcd")))
    tk = count_tokens(good)
    check("count_tokens 统计了消息条数", tk["messages"] == 5, str(tk))
    check("tool 消息的 token 被单独统计", tk["tool_tokens"] > 0, str(tk))

    print("\n  —— 端到端：一轮两个工具 ——")
    turn_out = run_turn("上海和北京的天气")
    tm = [m for m in turn_out["messages"] if m["role"] == "tool"]
    check("模型一次要了两个工具", len(turn_out["assistant"]["tool_calls"]) == 2)
    check("回填了两条 tool 消息", len(tm) == 2, str(len(tm)))
    check("端到端数组通过校验", turn_out["problems"] == [], summarise_problems(turn_out["problems"]))
    check("最终回答引用了两条结果", "上海" in turn_out["answer"] and "北京" in turn_out["answer"], turn_out["answer"])

    turn_big = run_turn("查一下我的订单")
    big_msgs = [m for m in turn_big["messages"] if m["role"] == "tool"]
    check("大结果被截断并打了标记", big_msgs[0].get("truncated") is True)
    check("发给模型前要剥掉本地字段", "truncated" not in strip_local_fields(big_msgs[0]))
    check("截断后模型仍能读懂（密封在 JSON 里）", "结果太大" in turn_big["answer"], turn_big["answer"])

    plain = run_turn("你们公司叫什么")
    check("不需要工具时数组只有 system/user/assistant 三条", len(plain["messages"]) == 3 and not any(m["role"] == "tool" for m in plain["messages"]), str(len(plain["messages"])))

    print("\n  —— 五种坏数组，全都要被抓出来 ——")
    cases = broken_cases()
    for name in cases:
        p = validate_messages(cases[name])
        check(f"抓出：{name}", len(p) > 0, name)

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    check("GET /health", client.get("/health").json()["limit"] == MAX_TOOL_CONTENT)
    body = client.post("/turn", json={"question": "上海和北京的天气"}).json()
    check("POST /turn 返回完整 messages", len([m for m in body["messages"] if m["role"] == "tool"]) == 2)
    check("POST /turn 附带 token 估算", body["tokens"]["total"] > 0)
    body = client.post("/validate", json={"messages": cases["缺一条 tool 消息"]}).json()
    check("POST /validate 指出缺了一条", body["problems"][0]["code"] == "missing_tool_result")
    body = client.get("/broken").json()
    check("GET /broken 五个场景全被抓住", len(body["cases"]) == 5 and all(c["problems"] for c in body["cases"]))
    body = client.post("/truncate", json={"payload": big_text, "limit": 200}).json()
    check("POST /truncate 同时给出信封与朴素切片的对比", body["parsed_ok"] is True and body["naive_parsed_ok"] is False)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
