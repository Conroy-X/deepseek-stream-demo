"""Unit 36 · Agent 错误处理 —— 工具一定会坏，坏的时候循环不能坏。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8036

自检（离线）：
    uv run python main.py

四件事：异常 → ToolResult；超时不阻塞；死循环熔断；幂等去重。
页面上一排「故障注入」按钮，点哪个都能看到 trace 和熔断原因。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from resilience import (
    DEFAULT_BUDGET,
    IdempotencyStore,
    LoopBreaker,
    ToolResult,
    call_many,
    call_tool,
    error_content,
    run_agent,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 36 · Agent 错误处理", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

PRICES = {"SH600519": 1680.0, "SZ000001": 11.2, "SH601318": 48.6}
SYSTEM = "你是行情助手。工具失败时如实转述错误，不要编造价格。"


# ---------------------------------------------------------------------------
# 世界：所有副作用和故障都发生在这里
# ---------------------------------------------------------------------------
class World:
    def __init__(self, fault: str | None = None):
        self.fault = fault
        self.calls = 0
        self.effects: list[dict] = []

    async def get_price(self, symbol: str) -> dict:
        self.calls += 1
        if self.fault == "raise":
            raise RuntimeError("行情服务 500 Internal Error")
        if self.fault == "timeout":
            await asyncio.sleep(3)
        else:
            await asyncio.sleep(0.01)
        if symbol not in PRICES:
            raise KeyError(f"没有 {symbol} 的行情")
        return {"symbol": symbol, "price": PRICES[symbol], "currency": "CNY"}

    async def refund(self, order_id: str, amount: float) -> dict:
        self.effects.append({"order_id": order_id, "amount": amount})
        return {"order_id": order_id, "refunded": amount, "effect_no": len(self.effects)}


TOOL_DECL = [
    {
        "type": "function",
        "function": {
            "name": "get_price",
            "description": "查询股票最新价。",
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string", "enum": list(PRICES)}},
                "required": ["symbol"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refund",
            "description": "给订单退款（有副作用，重复调用会真的退两次）。",
            "parameters": {
                "type": "object",
                "properties": {"order_id": {"type": "string"}, "amount": {"type": "number"}},
                "required": ["order_id", "amount"],
                "additionalProperties": False,
            },
        },
    },
]


def impl_for(world: World) -> dict:
    return {"get_price": world.get_price, "refund": world.refund}


# ---------------------------------------------------------------------------
# 两个模型
# ---------------------------------------------------------------------------
class OfflineModel:
    """查一次价格，然后根据结果（成功或报错）给一句话。"""

    name = "offline-rules"

    def complete(self, messages: list[dict]) -> dict:
        if messages[-1]["role"] == "tool":
            parts = []
            for m in messages:
                if m["role"] != "tool":
                    continue
                p = json.loads(m["content"])
                if "error" in p:
                    e = p["error"]
                    parts.append(f"工具失败（{e['kind']}）：{e['message']}")
                else:
                    parts.append(f"{p['symbol']} 现价 {p['price']} {p['currency']}")
            return {"role": "assistant", "content": "；".join(parts) + "。"}

        question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        symbol = next((s for s in PRICES if s in question), "SH600519")
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [_mk_call("get_price", {"symbol": symbol})],
        }


class LoopModel:
    """永远用同样的参数要同一个工具 —— 死循环的标准长相。"""

    def complete(self, messages: list[dict]) -> dict:
        return {"role": "assistant", "content": None, "tool_calls": [_mk_call("get_price", {"symbol": "SH600519"})]}


def _mk_call(name: str, args: dict) -> dict:
    return {
        "id": f"call_{uuid.uuid4().hex[:8]}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)},
    }


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
            json={"model": os.getenv("OPENAI_MODEL", "deepseek-flash"), "messages": messages, "tools": TOOL_DECL, "tool_choice": "auto"},
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


# ---------------------------------------------------------------------------
# 故障注入：四种场景共用一份代码
# ---------------------------------------------------------------------------
async def scenario(kind: str, question: str = "SH600519 现在多少钱", max_steps: int = 5, budget: float = DEFAULT_BUDGET) -> dict:
    world = World(fault=kind if kind in ("raise", "timeout") else None)
    breaker = LoopBreaker(3)
    model = LoopModel() if kind == "loop" else OfflineModel()

    t0 = time.perf_counter()
    st = await run_agent(question, model, impl_for(world), max_steps=max_steps, budget=budget, breaker=breaker, system=SYSTEM)
    wall_ms = (time.perf_counter() - t0) * 1000

    out = st.to_dict()
    out["wall_ms"] = round(wall_ms, 1)
    out["tool_calls_executed"] = world.calls
    out["effects"] = world.effects
    return out


async def replay_demo() -> dict:
    """同一个 tool_call_id 重放两次 —— 有副作用的工具只能真的执行一次。"""
    world = World()
    store = IdempotencyStore()
    key = "call_fixed_001"

    r1, rep1 = await store.once(key, lambda: world.refund("SO-000123", 100.0))
    r2, rep2 = await store.once(key, lambda: world.refund("SO-000123", 100.0))
    after_same_key = len(world.effects)
    r3, rep3 = await store.once("call_fixed_002", lambda: world.refund("SO-000123", 100.0))

    return {
        "key": key,
        "first": {"result": r1, "replayed": rep1},
        "second": {"result": r2, "replayed": rep2},
        "other_key": {"result": r3, "replayed": rep3},
        "effects": world.effects,
        "effect_count_after_replay": after_same_key,
        "effect_count": len(world.effects),
        "replays": store.replays,
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class FaultIn(BaseModel):
    kind: str = Field(default="none", description="none / raise / timeout / loop / replay")
    question: str = "SH600519 现在多少钱"
    max_steps: int = Field(default=5, ge=1, le=10)
    budget: float = Field(default=DEFAULT_BUDGET, gt=0, le=10)


@app.get("/health", summary="工具与默认超时预算")
def health() -> dict:
    return {"ok": True, "tools": [t["function"]["name"] for t in TOOL_DECL], "budget": DEFAULT_BUDGET, "model": "offline-rules"}


@app.get("/tools", summary="工具声明")
def tools() -> dict:
    return {"tools": TOOL_DECL}


@app.post("/run", summary="正常跑一次（无故障）")
async def run(req: FaultIn) -> dict:
    return await scenario("none", req.question, req.max_steps, req.budget)


@app.post("/fault", summary="注入一种故障，看循环还活不活")
async def fault(req: FaultIn) -> dict:
    if req.kind == "replay":
        return {"kind": "replay", **await replay_demo()}
    if req.kind not in ("none", "raise", "timeout", "loop"):
        return {"error": f"未知故障类型 {req.kind}", "allowed": ["none", "raise", "timeout", "loop", "replay"]}
    return {"kind": req.kind, **await scenario(req.kind, req.question, req.max_steps, req.budget)}


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

    print("Unit 36 · Agent 错误处理 自检（离线）\n")

    print("  —— 异常：变成 ToolResult，不冒泡 ——")
    w = World(fault="raise")
    res = asyncio.run(call_tool("get_price", {"symbol": "SH600519"}, impl_for(w)))
    check("异常被吞成结果：ok=False", res.ok is False)
    check("kind 标明是 exception", res.kind == "exception", res.kind)
    check("错误信息里有异常类型和原始消息", "RuntimeError" in res.content and "500" in res.content, res.content)

    raised = False
    try:
        asyncio.run(call_tool("get_price", {"symbol": "SH600519"}, impl_for(World(fault="raise"))))
    except Exception:  # noqa: BLE001
        raised = True
    check("调用方拿到的是结果，不是异常", not raised)

    ok_res = asyncio.run(call_tool("get_price", {"symbol": "SZ000001"}, impl_for(World())))
    check("异常之后正常调用不受影响", ok_res.ok and json.loads(ok_res.content)["price"] == 11.2)
    check("正常结果不带 error 字段", "error" not in json.loads(ok_res.content))

    print("\n  —— 超时：1 秒内必须返回 ——")
    w = World(fault="timeout")
    t0 = time.perf_counter()
    res = asyncio.run(call_tool("get_price", {"symbol": "SH600519"}, impl_for(w), budget=0.5))
    cost = (time.perf_counter() - t0) * 1000
    check("超时被判为 timeout", res.kind == "timeout", res.kind)
    check(f"0.5s 预算下 {cost:.0f}ms 内返回（工具本身要睡 3 秒）", cost < 1000, f"{cost:.0f}ms")
    check("错误信息说明了预算是多少", "0.5" in res.content, res.content)

    w = World(fault="timeout")
    calls = [_mk_call("get_price", {"symbol": "SH600519"}), _mk_call("get_price", {"symbol": "SZ000001"}), _mk_call("get_price", {"symbol": "SH601318"})]
    t0 = time.perf_counter()
    results = asyncio.run(call_many(calls, impl_for(w), budget=0.3))
    cost = (time.perf_counter() - t0) * 1000
    check("三个并发调用全部返回（没有整批崩）", len(results) == 3, str(len(results)))
    check("三个都是超时（World 是全局故障）", all(r.kind == "timeout" for r in results))
    check(f"并发超时也没拖成串行（{cost:.0f}ms）", cost < 1500, f"{cost:.0f}ms")

    print("\n  —— 死循环熔断：第 N 次就停 ——")
    b = LoopBreaker(3)
    args = {"symbol": "SH600519"}
    check("第 1 次放行", b.hit("get_price", args) is None)
    check("第 2 次放行", b.hit("get_price", args) is None)
    reason = b.hit("get_price", args)
    check("第 3 次熔断", reason is not None)
    check("熔断原因里有工具名和次数", reason and "get_price" in reason and "3" in reason, str(reason))
    check("换个参数不触发", b.hit("get_price", {"symbol": "SZ000001"}) is None)
    check("键序不同不算新调用", b.key("f", {"a": 1, "b": 2}) == b.key("f", {"b": 2, "a": 1}))
    b.reset()
    check("reset 之后重新计数", b.hit("get_price", args) is None)
    raised = False
    try:
        LoopBreaker(1)
    except ValueError:
        raised = True
    check("max_repeats=1 直接拒绝（第一次就熔断等于不能调工具）", raised)

    print("\n  —— 幂等：同一个 tool_call_id 只执行一次 ——")
    demo = asyncio.run(replay_demo())
    check("第一次真的执行了", demo["first"]["replayed"] is False)
    check("第二次是重放", demo["second"]["replayed"] is True)
    check("两次结果完全一致", demo["first"]["result"] == demo["second"]["result"])
    check("副作用只发生了一次", demo["effect_count_after_replay"] == 1, str(demo["effects"]))
    check("换一个 key 会重新执行", demo["other_key"]["replayed"] is False and demo["effect_count"] == 2)
    check("重放次数被记账", demo["replays"] == 1, str(demo["replays"]))

    print("\n  —— 循环集成：正常路径没被改坏 ——")
    normal = asyncio.run(scenario("none"))
    check("正常路径出答案", normal["stopped"] == "answer" and "1680" in normal["answer"], normal["answer"])
    check("正常路径用了 2 步", normal["steps"] == 2, str(normal["steps"]))
    check("正常路径没有熔断事件", normal["breaker_events"] == [])

    raised_run = asyncio.run(scenario("raise"))
    kinds = [t.get("kind") for t in raised_run["trace"] if t["type"] == "tool"]
    check("异常被回填成 tool 消息", kinds == ["exception"], str(kinds))
    check("循环继续到出答案（没有 500）", raised_run["stopped"] == "answer", str(raised_run["stopped"]))
    check("答案里如实转述了错误", "工具失败" in raised_run["answer"] or "行情服务" in raised_run["answer"], raised_run["answer"])

    t0 = time.perf_counter()
    to_run = asyncio.run(scenario("timeout", budget=0.5))
    cost = (time.perf_counter() - t0) * 1000
    check(f"超时场景整体 {cost:.0f}ms 结束（不是 3 秒 + 3 秒）", cost < 1500, f"{cost:.0f}ms")
    check("超时也被回填成 tool 消息", [t.get("kind") for t in to_run["trace"] if t["type"] == "tool"] == ["timeout"])
    check("超时后循环仍然走到 answer", to_run["stopped"] == "answer", str(to_run["stopped"]))

    loop_run = asyncio.run(scenario("loop"))
    check("死循环被熔断：stopped=loop_detected", loop_run["stopped"] == "loop_detected", str(loop_run["stopped"]))
    check("熔断发生在第 3 步", loop_run["steps"] == 3, str(loop_run["steps"]))
    check("熔断事件里写了原因", len(loop_run["breaker_events"]) == 1 and "get_price" in loop_run["breaker_events"][0]["reason"], str(loop_run["breaker_events"]))
    check("熔断前只真的执行了 2 次（第 3 次在执行前就被拦下）", loop_run["tool_calls_executed"] == 2, str(loop_run["tool_calls_executed"]))
    check("trace 里有 breaker 条目", any(t["type"] == "breaker" for t in loop_run["trace"]))
    tool_msgs = [m for m in loop_run["messages"] if m["role"] == "tool"]
    calls_n = [c for m in loop_run["messages"] if m["role"] == "assistant" for c in m.get("tool_calls") or []]
    check("熔断后 messages 依然合法（每条 tool_call 都有 tool 消息）", len(tool_msgs) == len(calls_n), f"{len(tool_msgs)} vs {len(calls_n)}")
    check("被拦下的那条写了 blocked", any("blocked" in m["content"] for m in tool_msgs))

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    check("GET /health", client.get("/health").json()["budget"] == DEFAULT_BUDGET)
    body = client.post("/fault", json={"kind": "raise"}).json()
    check("POST /fault raise → kind=exception", [t["kind"] for t in body["trace"] if t["type"] == "tool"] == ["exception"])
    body = client.post("/fault", json={"kind": "timeout", "budget": 0.5}).json()
    check("POST /fault timeout → 墙钟 < 1.5s", body["wall_ms"] < 1500, str(body["wall_ms"]))
    body = client.post("/fault", json={"kind": "loop"}).json()
    check("POST /fault loop → loop_detected", body["stopped"] == "loop_detected")
    body = client.post("/fault", json={"kind": "replay"}).json()
    check("POST /fault replay → 同 key 副作用 1 次、重放 1 次", body["effect_count_after_replay"] == 1 and body["replays"] == 1, str(body))
    body = client.post("/run", json={"question": "SZ000001 多少钱"}).json()
    check("POST /run 正常出答案", "11.2" in body["answer"], body["answer"])
    body = client.post("/fault", json={"kind": "nope"}).json()
    check("未知故障类型给出可用列表", "allowed" in body)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
