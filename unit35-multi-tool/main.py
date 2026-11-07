"""Unit 35 · 多工具路由 —— 工具一多，主要矛盾从「怎么执行」变成「选哪个」。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8035

自检（离线）：
    uv run python main.py

本章只解决一件事：**路由质量**。执行、回填、循环都是前面几章做过的，这里从简。
页面能看三样东西：工具清单、为什么选这个工具、四个配置下的准确率对照。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from routing import (
    IMPL,
    QUESTIONS,
    RATES,
    TOOLS_2,
    TOOLS_5,
    UNITS,
    accuracy,
    bare_tools,
    explain,
    rank,
    route,
    vague_tools,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 35 · Multi Tool", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SYSTEM = "你是助手。用户的问题可能同时需要多个工具，能并行调用的就一起调。"


# ---------------------------------------------------------------------------
# 从自然语言里抽参数（离线模型的「手」）
# ---------------------------------------------------------------------------
def _num(text: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None


def fill_args(name: str, question: str) -> dict:
    if name == "get_weather":
        city = next((c for c in ("上海", "北京", "广州", "深圳") if c in question), "上海")
        return {"city": city}
    if name == "calculate":
        m = re.search(r"[0-9(][0-9+\-*/().%\s]{2,}", question)
        expr = m.group(0).strip() if m else "1+1"
        mul = re.search(r"(\d+)\s*乘\s*(\d+)", question)
        if mul:
            expr = f"{mul.group(1)}*{mul.group(2)}"
        return {"expression": expr}
    if name == "convert_currency":
        found = sorted((c for c in RATES if c in question), key=question.index)
        amount = _num(question) or 1.0
        return {"amount": amount, "from_currency": found[0] if found else "美元", "to_currency": found[1] if len(found) > 1 else "人民币"}
    if name == "convert_unit":
        found = sorted((u for u in UNITS if u in question), key=question.index)
        return {"value": _num(question) or 1.0, "from_unit": found[0] if found else "公里", "to_unit": found[1] if len(found) > 1 else "米"}
    if name == "lookup_order":
        m = re.search(r"SO-\d{6}", question)
        return {"order_id": m.group(0) if m else "SO-000123"}
    return {}


def plan(question: str, tools: list[dict]) -> list[dict]:
    """决定要调哪几个工具。两个城市/两种换算就并行两个调用 —— 这是本章的新东西。"""
    calls: list[dict] = []

    cities = [c for c in ("上海", "北京", "广州", "深圳") if c in question]
    if ("天气" in question or "度" in question or "下雨" in question) and len(cities) >= 2:
        for c in cities[:3]:
            calls.append(_mk_call("get_weather", {"city": c}))
        return calls

    pick, _ = route(question, tools)
    if pick is None:
        return calls
    return [_mk_call(pick, fill_args(pick, question))]


def _mk_call(name: str, args: dict) -> dict:
    return {
        "id": f"call_{uuid.uuid4().hex[:8]}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


def execute_calls(calls: list[dict]) -> list[dict]:
    """并行调用（这里是串行实现，但每个都独立计时 —— 关键是不让一个失败影响另一个）。"""
    out = []
    for call in calls:
        name = call["function"]["name"]
        t0 = time.perf_counter()
        try:
            args = json.loads(call["function"].get("arguments") or "{}")
        except json.JSONDecodeError:
            out.append({"call_id": call["id"], "name": name, "ok": False, "args": {}, "content": json.dumps({"error": "arguments 不是合法 JSON"}, ensure_ascii=False), "ms": 0.0})
            continue
        fn = IMPL.get(name)
        if fn is None:
            out.append({"call_id": call["id"], "name": name, "ok": False, "args": args, "content": json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False), "ms": 0.0})
            continue
        try:
            data = fn(**args)
            content, ok = json.dumps(data, ensure_ascii=False), True
        except Exception as e:  # noqa: BLE001 - 一个工具坏了不能带走整批
            content, ok = json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False), False
        out.append({"call_id": call["id"], "name": name, "ok": ok, "args": args, "content": content, "ms": round((time.perf_counter() - t0) * 1000, 1)})
    return out


def unanswered_calls(messages: list[dict]) -> list[str]:
    pending: dict[str, str] = {}
    orphan: list[str] = []
    for m in messages:
        if m["role"] == "tool":
            pending.pop(m.get("tool_call_id") or "", None)
            continue
        orphan.extend(pending)
        pending = {c["id"]: c["function"]["name"] for c in m.get("tool_calls") or []}
    orphan.extend(pending)
    return orphan


class OfflineModel:
    """规则版模型：路由靠 routing.route()，参数靠正则抠。"""

    name = "offline-rules"

    def __init__(self, tools: list[dict] | None = None):
        self.tools = tools or TOOLS_5
        self.calls = 0

    def complete(self, messages: list[dict]) -> dict:
        self.calls += 1
        if messages[-1]["role"] == "tool":
            parts = []
            for m in messages:
                if m["role"] != "tool":
                    continue
                p = json.loads(m["content"])
                if "error" in p:
                    parts.append(f"失败：{p['error']}")
                elif "temp_c" in p:
                    parts.append(f"{p['city']}{p['sky']}{p['temp_c']}°C")
                elif "value" in p and "expression" in p:
                    parts.append(f"{p['expression']}={p['value']}")
                elif "value" in p:
                    parts.append(f"{p['amount']}{p['from']}≈{p['value']}{p['to']}" if "amount" in p else f"{p['value']}{p['from']}={p['result']}{p['to']}")
                elif "order_id" in p:
                    parts.append(f"{p['order_id']} 状态 {p['status']}，金额 {p['amount']} 元")
            return {"role": "assistant", "content": "；".join(parts) + "。"}

        question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        calls = plan(question, self.tools)
        if not calls:
            return {"role": "assistant", "content": "没有合适的工具，我直接回答：不知道。"}
        return {"role": "assistant", "content": None, "tool_calls": calls}


def ask(question: str, tools: list[dict] | None = None) -> dict:
    tools = tools or TOOLS_5
    model = OfflineModel(tools)
    messages: list[dict] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    first = model.complete(messages)
    calls = first.get("tool_calls") or []
    if not calls:
        return {"question": question, "calls": [], "results": [], "messages": messages + [{"role": "assistant", "content": first["content"]}], "answer": first["content"], "ranked": rank(question, tools)[:3]}

    messages.append({"role": "assistant", "content": None, "tool_calls": calls})
    results = execute_calls(calls)
    for r in results:
        messages.append({"role": "tool", "tool_call_id": r["call_id"], "name": r["name"], "content": r["content"]})
    final = model.complete(messages)
    messages.append({"role": "assistant", "content": final["content"]})
    return {
        "question": question,
        "calls": [{"id": c["id"], "name": c["function"]["name"], "args": json.loads(c["function"]["arguments"])} for c in calls],
        "results": results,
        "messages": messages,
        "answer": final["content"],
        "unanswered": unanswered_calls(messages),
        "ranked": rank(question, tools)[:3],
    }


def chat_api(messages: list[dict], tools: list[dict]) -> dict:
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
            json={"model": os.getenv("OPENAI_MODEL", "deepseek-flash"), "messages": messages, "tools": tools, "tool_choice": "auto"},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class QIn(BaseModel):
    question: str = Field(min_length=1)


@app.get("/health", summary="五个工具")
def health() -> dict:
    return {"ok": True, "tools": [t["function"]["name"] for t in TOOLS_5], "model": os.getenv("OPENAI_MODEL") or "offline-rules"}


@app.get("/tools", summary="完整工具声明（页面直接渲染）")
def tools() -> dict:
    return {"tools": TOOLS_5}


@app.post("/route", summary="只要路由结论：选谁、凭什么、其他候选")
def do_route(req: QIn) -> dict:
    return explain(req.question, TOOLS_5)


@app.post("/ask", summary="路由 → 并行执行 → 按 id 回填 → 回答")
def do_ask(req: QIn) -> dict:
    return ask(req.question)


@app.get("/bench", summary="四个配置的 10 题准确率")
def bench() -> dict:
    configs = [
        ("五工具 · 描述完整", TOOLS_5),
        ("五工具 · 描述含糊", vague_tools()),
        ("五工具 · 删掉 description", bare_tools()),
        ("两工具 · 只有天气和计算", TOOLS_2),
    ]
    return {
        "cases": [{"label": label, **accuracy(t)} for label, t in configs],
        "questions": [{"q": q["q"], "expect": q["expect"]} for q in QUESTIONS],
    }


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

    print("Unit 35 · Multi Tool 自检（离线）\n")

    print("  —— 五个工具各自都能被路由到 ——")
    for item in QUESTIONS:
        picked, score = route(item["q"], TOOLS_5)
        check(f"路由：{item['q']} → {item['expect']}", picked == item["expect"], f"实际 {picked} ({score})")

    print("\n  —— 准确率：工具变多 / 描述变差，数字会掉 ——")
    full = accuracy(TOOLS_5)
    vague = accuracy(vague_tools())
    bare = accuracy(bare_tools())
    two = accuracy(TOOLS_2)
    check("五工具 + 完整描述：10/10", full["correct"] == 10, f"{full['correct']}/10")
    check("描述含糊：掉到 9/10", vague["correct"] == 9, f"{vague['correct']}/10")
    check("删掉 description：掉到 6/10", bare["correct"] == 6, f"{bare['correct']}/10")
    check("只有两个工具时：4/10（另外 6 题根本没有对应工具）", two["correct"] == 4, f"{two['correct']}/10")
    check("准确率随描述质量单调不降", full["correct"] >= vague["correct"] >= bare["correct"])

    print("\n  —— 误选：记录下来，而不是删掉 ——")
    picked, score = route("深圳到北京的机票多少钱", TOOLS_5)
    check("没有机票工具时，问题被塞给 get_weather（真实误选，被记录）", picked == "get_weather", f"实际 {picked} ({score})")
    check("误选的分数还不低（它很自信）", score > 5, str(score))
    picked2, _ = route("上海有什么好吃的", TOOLS_5)
    check("「好吃的」也被塞给 get_weather", picked2 == "get_weather", str(picked2))
    check("两工具配置下同样误选：美元问题被塞给天气", any(r["q"] == "100 美元能换多少人民币" and r["picked"] == "get_weather" for r in two["rows"]), str(two["rows"][4]))
    nos = [r for r in bare["rows"] if r["picked"] is None]
    check("删掉描述后不是选错，而是「一个都选不出来」", len(nos) == 4, str([r["q"] for r in nos]))
    check("删掉描述后订单类问题全军覆没", all(not r["ok"] for r in bare["rows"] if "SO-" in r["q"]))

    print("\n  —— 并行：一次返回多个 tool_calls ——")
    out = ask("上海和北京今天多少度")
    check("两个城市 → 两个调用", len(out["calls"]) == 2, str(len(out["calls"])))
    check("两个调用都是 get_weather", [c["name"] for c in out["calls"]] == ["get_weather", "get_weather"])
    check("参数不同（分别抽对了城市）", [c["args"]["city"] for c in out["calls"]] == ["上海", "北京"], str([c["args"] for c in out["calls"]]))
    check("回填了两条 tool 消息", len([m for m in out["messages"] if m["role"] == "tool"]) == 2)
    check("每个结果的 call_id 都对得上调用", [r["call_id"] for r in out["results"]] == [c["id"] for c in out["calls"]])
    check("并行结果顺序与调用顺序一致", [r["name"] for r in out["results"]] == ["get_weather", "get_weather"])
    check("没有悬空的 tool_call", out["unanswered"] == [], str(out["unanswered"]))
    check("答案里两个城市都有", "上海" in out["answer"] and "北京" in out["answer"], out["answer"])

    out2 = ask("上海、北京、广州今天下雨吗")
    check("三个城市 → 三个调用", len(out2["calls"]) == 3, str(len(out2["calls"])))
    check("三个都回填了", len([m for m in out2["messages"] if m["role"] == "tool"]) == 3)

    print("\n  —— 单工具与参数抽取 ——")
    out3 = ask("100 美元能换多少人民币")
    check("货币换算只调一个工具", len(out3["calls"]) == 1 and out3["calls"][0]["name"] == "convert_currency")
    args = out3["calls"][0]["args"]
    check("从/到货币抽对", args["from_currency"] == "美元" and args["to_currency"] == "人民币", str(args))
    check("金额抽对", args["amount"] == 100.0, str(args))
    check("换算结果出现在答案里", "≈" in out3["answer"] or "709" in out3["answer"], out3["answer"])

    out4 = ask("5 公里是多少米")
    a4 = out4["calls"][0]["args"]
    check("单位换算的单位抽对", a4["from_unit"] == "公里" and a4["to_unit"] == "米", str(a4))
    check("单位换算结果正确（5000 米）", "5000" in out4["answer"], out4["answer"])

    out5 = ask("订单 SO-000123 到哪了")
    check("订单号抽对", out5["calls"][0]["args"]["order_id"] == "SO-000123", str(out5["calls"][0]["args"]))
    check("订单状态出现在答案里", "已发货" in out5["answer"], out5["answer"])

    print("\n  —— 一个工具坏了，不影响另一个 ——")
    calls = [_mk_call("get_weather", {"city": "杭州"}), _mk_call("get_weather", {"city": "北京"})]
    res = execute_calls(calls)
    check("第一个失败", res[0]["ok"] is False, res[0]["content"])
    check("第二个仍然成功", res[1]["ok"] is True, res[1]["content"])
    check("失败也是统一格式（error 字段）", "error" in json.loads(res[0]["content"]))
    check("每个结果都带耗时（ms）", all("ms" in r for r in res))

    calls = [_mk_call("rm_rf", {})]
    res = execute_calls(calls)
    check("未知工具返回错误而不是抛异常", res[0]["ok"] is False and "未知工具" in res[0]["content"])

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    check("GET /health 列出五个工具", len(client.get("/health").json()["tools"]) == 5)
    body = client.get("/tools").json()
    check("GET /tools 每条都有 description", all(t["function"].get("description") for t in body["tools"]))
    body = client.post("/route", json={"question": "5 公里是多少米"}).json()
    check("POST /route 给出 picked 与候选", body["picked"] == "convert_unit" and len(body["ranked"]) >= 3, str(body["picked"]))
    check("POST /route 给出命中的词", bool(body["detail"][0]["hits"]), str(body["detail"][0]["hits"]))
    body = client.post("/ask", json={"question": "上海和北京今天多少度"}).json()
    check("POST /ask 并行两个调用", len(body["calls"]) == 2)
    body = client.get("/bench").json()
    check("GET /bench 四个配置", len(body["cases"]) == 4)
    check("GET /bench 里五工具的准确率最高", body["cases"][0]["correct"] == 10 and body["cases"][3]["correct"] == 4, str([c["correct"] for c in body["cases"]]))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
