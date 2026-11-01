"""Unit 34 · Agent Loop —— 手写 while 循环，不借助任何框架。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8034

自检（离线）：
    uv run python main.py

只解决一件事：**循环的终止条件**。参数校验、消息截断前面两章做过了，这里从简。
"""

from __future__ import annotations

import json
import os
import sys
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent import MAX_STEPS_ANSWER, AgentRunner, ToolSpec, replay_state, to_openai_tools, unanswered_calls

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 34 · Agent Loop", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DEFAULT_MAX_STEPS = 6

FORECAST = {
    "上海": {"sky": "小雨", "rain_mm": 6},
    "北京": {"sky": "晴", "rain_mm": 0},
    "广州": {"sky": "中雨", "rain_mm": 12},
}


def get_forecast(city: str) -> dict:
    if city not in FORECAST:
        raise ValueError(f"没有 {city} 的预报")
    return {"city": city, **FORECAST[city]}


def build_packing_list(rain_mm: int) -> dict:
    items = ["雨伞", "防水外套"] if rain_mm >= 5 else ["雨伞"]
    return {"rain_mm": rain_mm, "items": items}


TOOLS = [
    ToolSpec(
        "get_forecast",
        "查城市今天的降雨量（毫米）。",
        {
            "type": "object",
            "properties": {"city": {"type": "string", "enum": list(FORECAST)}},
            "required": ["city"],
            "additionalProperties": False,
        },
        get_forecast,
    ),
    ToolSpec(
        "build_packing_list",
        "根据降雨量给出要带的东西。",
        {
            "type": "object",
            "properties": {"rain_mm": {"type": "integer", "minimum": 0}},
            "required": ["rain_mm"],
            "additionalProperties": False,
        },
        build_packing_list,
    ),
]


# ---------------------------------------------------------------------------
# 三种模型
# ---------------------------------------------------------------------------
class OfflineModel:
    """规则驱动：先查预报，下雨就再查该带什么，最后给答案。2 步或 3 步。"""

    name = "offline-rules"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict]) -> dict:
        self.calls += 1
        question = next((m["content"] for m in messages if m["role"] == "user"), "")
        payloads = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]

        if not payloads:
            city = next((c for c in FORECAST if c in question), "上海")
            return _call("get_forecast", {"city": city})

        forecast = next((p for p in payloads if "rain_mm" in p), None)
        if forecast and forecast["rain_mm"] > 0 and not any("items" in p for p in payloads):
            return _call("build_packing_list", {"rain_mm": forecast["rain_mm"]})

        packing = next((p for p in payloads if "items" in p), None)
        if forecast and packing:
            return {"role": "assistant", "content": f"{forecast['city']}{forecast['sky']}，降雨 {forecast['rain_mm']}mm，要带：{'、'.join(packing['items'])}。"}
        if forecast:
            return {"role": "assistant", "content": f"{forecast['city']}{forecast['sky']}，降雨 {forecast['rain_mm']}mm，不用带伞。"}
        return {"role": "assistant", "content": "查不到数据。"}


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


class ScriptedModel:
    """按剧本返回。用来精确控制「第几步返回什么」，测终止条件最好用。"""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.calls = 0

    def complete(self, messages: list[dict]) -> dict:
        self.calls += 1
        if self.script:
            return self.script.pop(0)
        return {"role": "assistant", "content": "剧本用完了"}


class LoopModel:
    """永远要求调同一个工具 —— 用来证明 max_steps 真的拦得住。"""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict]) -> dict:
        self.calls += 1
        return _call("get_forecast", {"city": "上海"})


class RecordingModel:
    """包一层，把每次收到的 messages 记下来，用来验证「工具结果下一步可见」。"""

    def __init__(self, inner):
        self.inner = inner
        self.snapshots: list[list[dict]] = []

    def complete(self, messages: list[dict]) -> dict:
        self.snapshots.append(json.loads(json.dumps(messages, ensure_ascii=False)))
        return self.inner.complete(messages)


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
                "messages": messages,
                "tools": to_openai_tools(TOOLS),
                "tool_choice": "auto",
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


# ---------------------------------------------------------------------------
# HTTP：会话式，支持「走一步」
# ---------------------------------------------------------------------------
SESSIONS: dict[str, AgentRunner] = {}


class StartIn(BaseModel):
    question: str = Field(min_length=1)
    max_steps: int = Field(default=DEFAULT_MAX_STEPS, ge=1, le=12)


class RunIn(StartIn):
    pass


def _state(runner: AgentRunner) -> dict:
    return runner.state()


@app.get("/health", summary="工具与默认步数上限")
def health() -> dict:
    return {"ok": True, "tools": [t.name for t in TOOLS], "max_steps": DEFAULT_MAX_STEPS, "model": os.getenv("OPENAI_MODEL") or "offline-rules"}


@app.get("/tools", summary="工具声明")
def tools() -> dict:
    return {"tools": to_openai_tools(TOOLS)}


@app.post("/session", summary="开一个可步进的会话")
def start(req: StartIn) -> dict:
    sid = uuid.uuid4().hex[:12]
    SESSIONS[sid] = AgentRunner(req.question, OfflineModel(), TOOLS, req.max_steps)
    return {"id": sid, "state": _state(SESSIONS[sid])}


@app.post("/session/{sid}/step", summary="走一步（一次模型调用 + 它要的工具）")
def step(sid: str) -> dict:
    runner = SESSIONS.get(sid)
    if runner is None:
        raise HTTPException(404, f"没有会话 {sid}")
    return {"id": sid, "state": runner.step()}


@app.get("/session/{sid}", summary="看当前状态")
def get_session(sid: str) -> dict:
    runner = SESSIONS.get(sid)
    if runner is None:
        raise HTTPException(404, f"没有会话 {sid}")
    return {"id": sid, "state": _state(runner)}


@app.post("/run", summary="一次跑完")
def run(req: RunIn) -> dict:
    return AgentRunner(req.question, OfflineModel(), TOOLS, req.max_steps).run()


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

    print("Unit 34 · Agent Loop 自检（离线）\n")

    print("  —— 终止条件：什么时候停 ——")
    m = ScriptedModel([{"role": "assistant", "content": "不需要工具，直接答"}])
    st = AgentRunner("你好", m, TOOLS).run()
    check("无工具直接回答：1 步", st["steps"] == 1, str(st["steps"]))
    check("无工具直接回答：stopped=answer", st["stopped"] == "answer", str(st["stopped"]))
    check("无工具时 messages 只多了 1 条", len(st["messages"]) == 3, str(len(st["messages"])))

    m = ScriptedModel([{"role": "assistant", "content": "", "tool_calls": None}])
    st = AgentRunner("空回答", m, TOOLS).run()
    check("content 空且无 tool_calls：停下并标 empty", st["stopped"] == "empty", str(st["stopped"]))
    check("empty 时只调用一次模型", m.calls == 1, str(m.calls))

    both = {
        "role": "assistant",
        "content": "我先查一下天气",
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_forecast", "arguments": '{"city":"北京"}'}}],
    }
    m = ScriptedModel([both, {"role": "assistant", "content": "北京晴"}])
    st = AgentRunner("北京天气", m, TOOLS).run()
    check("content 和 tool_calls 同时有：继续调工具", st["steps"] == 2, str(st["steps"]))
    check("那句话被保留在 assistant 消息里", st["messages"][2]["content"] == "我先查一下天气", str(st["messages"][2])[:80])
    check("这类轮次的 decision 是 call_tools", st["trace"][0]["decision"] == "call_tools")

    print("\n  —— 收敛步数：2 步 / 3 步 ——")
    m2 = OfflineModel()
    st2 = AgentRunner("北京出差要带伞吗", m2, TOOLS).run()
    check("晴天：2 步收敛（查预报 → 回答）", st2["steps"] == 2, str(st2["steps"]))
    check("晴天：答案说不用带伞", "不用带伞" in st2["answer"], st2["answer"])
    check("每步只调一次模型", m2.calls == st2["steps"], f"{m2.calls} vs {st2['steps']}")

    m3 = OfflineModel()
    st3 = AgentRunner("上海出差要带伞吗", m3, TOOLS).run()
    check("雨天：3 步收敛（查预报 → 查清单 → 回答）", st3["steps"] == 3, str(st3["steps"]))
    check("雨天：答案里带上要带的东西", "雨伞" in st3["answer"], st3["answer"])
    check("雨天：调了两个不同工具", [t["tool"] for t in st3["trace"] if t["type"] == "tool"] == ["get_forecast", "build_packing_list"])

    rec = RecordingModel(OfflineModel())
    AgentRunner("广州出差要带伞吗", rec, TOOLS).run()
    check("第二步的模型调用能看到上一步的工具结果", any(x["role"] == "tool" for x in rec.snapshots[1]), str(len(rec.snapshots)))
    check("第一步的模型调用看不到任何工具结果", not any(x["role"] == "tool" for x in rec.snapshots[0]))

    print("\n  —— 触顶：max_steps 是保险丝 ——")
    loop_model = LoopModel()
    st4 = AgentRunner("永远要调工具", loop_model, TOOLS, max_steps=3).run()
    check("触顶停下：stopped=max_steps", st4["stopped"] == "max_steps", str(st4["stopped"]))
    check("触顶：步数正好等于上限", st4["steps"] == 3, str(st4["steps"]))
    check("触顶：模型只被调了 3 次", loop_model.calls == 3, str(loop_model.calls))
    check("触顶：答案换成兜底文案", st4["answer"] == MAX_STEPS_ANSWER, st4["answer"])
    check("触顶后 messages 里没有悬空的 tool_calls", unanswered_calls(st4["messages"]) == [], str(unanswered_calls(st4["messages"])))
    check("触顶仍然执行了最后一次工具（否则数组非法）", len([t for t in st4["trace"] if t["type"] == "tool"]) == 3)

    st5 = AgentRunner("x", LoopModel(), TOOLS, max_steps=1).run()
    check("max_steps=1 只跑一轮", st5["steps"] == 1 and st5["stopped"] == "max_steps")

    raised = False
    try:
        AgentRunner("x", OfflineModel(), TOOLS, max_steps=0)
    except ValueError:
        raised = True
    check("max_steps=0 直接拒绝（否则一次都不会跑）", raised)

    print("\n  —— trace：能回放，也能看懂 ——")
    check("trace 里 model 条目数等于 steps", len([t for t in st3["trace"] if t["type"] == "model"]) == st3["steps"])
    check("trace 每条都有合法 type", all(t["type"] in {"model", "tool"} for t in st3["trace"]))
    check("trace 的 step 序号单调不减", [t["step"] for t in st3["trace"]] == sorted(t["step"] for t in st3["trace"]))
    tool_entries = [t for t in st3["trace"] if t["type"] == "tool"]
    check("tool 条目记了工具名与参数", tool_entries[0]["tool"] == "get_forecast" and tool_entries[0]["args"]["city"] == "上海", str(tool_entries[0])[:100])
    check("tool 条目带结果摘要（有长度上限）", len(tool_entries[0]["digest"]) <= 90, str(len(tool_entries[0]["digest"])))
    check("每个 model 条目都带 decision", all(t.get("decision") for t in st3["trace"] if t["type"] == "model"))
    check("trace 回放能重建出同样的 messages", replay_state(st3) == st3["messages"])
    check("回放出来的数组仍然合法", unanswered_calls(replay_state(st3)) == [])

    print("\n  —— 步进执行与一次跑完等价 ——")
    r = AgentRunner("上海出差要带伞吗", OfflineModel(), TOOLS)
    check("刚创建时 steps=0 且未结束", r.steps == 0 and not r.done)
    r.step()
    check("走一步后 steps=1", r.steps == 1 and not r.done)
    r.step()
    check("走两步后还没结束（雨天要第三步）", r.steps == 2 and not r.done, str(r.stopped))
    r.step()
    check("走三步后结束，stopped=answer", r.done and r.stopped == "answer")
    before = r.steps
    r.step()
    check("结束之后再 step 不会继续推进", r.steps == before)
    def _norm(obj):
        """把随机生成的 id 抹平再比较 —— 结构一致才是真的等价。"""
        if isinstance(obj, dict):
            return {k: ("<id>" if k in ("id", "tool_call_id") else _norm(v)) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_norm(x) for x in obj]
        return obj

    check("步进的 trace 与一次跑完结构一致", _norm(r.state()["trace"]) == _norm(st3["trace"]))
    check("步进的 messages 与一次跑完结构一致", _norm(r.state()["messages"]) == _norm(st3["messages"]))

    print("\n  —— 工具出错也不能掀翻循环 ——")
    bad = {
        "role": "assistant",
        "tool_calls": [{"id": "c9", "type": "function", "function": {"name": "get_forecast", "arguments": '{"city":"杭州"}'}}],
    }
    m = ScriptedModel([bad, {"role": "assistant", "content": "杭州查不到"}])
    st6 = AgentRunner("杭州天气", m, TOOLS).run()
    check("未知城市：工具报错但循环继续", st6["steps"] == 2 and st6["stopped"] == "answer")
    check("报错信息进了 tool 消息", "没有 杭州 的预报" in st6["messages"][3]["content"], st6["messages"][3]["content"])

    bad2 = {
        "role": "assistant",
        "tool_calls": [{"id": "c10", "type": "function", "function": {"name": "get_forecast", "arguments": "{坏的"}}],
    }
    st7 = AgentRunner("坏参数", ScriptedModel([bad2, {"role": "assistant", "content": "参数错了"}]), TOOLS).run()
    check("arguments 不是合法 JSON 也不崩", st7["stopped"] == "answer" and st7["trace"][1]["ok"] is False)

    bad3 = {
        "role": "assistant",
        "tool_calls": [{"id": "c11", "type": "function", "function": {"name": "rm_rf", "arguments": "{}"}}],
    }
    st8 = AgentRunner("未知工具", ScriptedModel([bad3, {"role": "assistant", "content": "没有这个工具"}]), TOOLS).run()
    check("未知工具返回错误结果而不是崩溃", st8["trace"][1]["ok"] is False and "未知工具" in st8["messages"][3]["content"])

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    check("GET /health", client.get("/health").json()["max_steps"] == DEFAULT_MAX_STEPS)
    check("GET /tools 返回两个工具", len(client.get("/tools").json()["tools"]) == 2)

    body = client.post("/session", json={"question": "上海出差要带伞吗"}).json()
    sid = body["id"]
    check("POST /session 开出来是 0 步", body["state"]["steps"] == 0 and body["state"]["stopped"] is None)

    body = client.post(f"/session/{sid}/step").json()
    check("走一步后 trace 有内容", body["state"]["steps"] == 1 and body["state"]["trace"])
    body = client.post(f"/session/{sid}/step").json()
    body = client.post(f"/session/{sid}/step").json()
    check("走到第三步结束", body["state"]["stopped"] == "answer" and body["state"]["answer"], str(body["state"]["stopped"]))
    check("GET /session 能看状态", client.get(f"/session/{sid}").json()["state"]["steps"] == 3)
    check("未知会话返回 404", client.get("/session/nope").status_code == 404)

    body = client.post("/run", json={"question": "北京出差要带伞吗"}).json()
    check("POST /run 一次跑完是 2 步", body["steps"] == 2, str(body["steps"]))
    body = client.post("/run", json={"question": "上海", "max_steps": 2}).json()
    check("POST /run 可以限制步数并触顶", body["stopped"] == "max_steps" and body["steps"] == 2)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
