"""Unit 31 · Function Calling —— 模型说要调什么，你负责执行并把结果喂回去。

阶段三的第一步。这一章只做**一轮工具调用**（Day 31 的范围），
多轮规划、反思、记忆是后面 Agent 的事。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8031
    打开 http://127.0.0.1:8031/

自检（离线，用规则驱动的假模型，不联网）：
    uv run python main.py

接真模型：配好 .env 后把 OfflineModel 换成 chat_api（文件里已给出实现）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from tools import TOOLS, ToolError, execute, validate_args

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 31 · Function Calling", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

MAX_ROUNDS = 3
CITIES = ("上海", "北京", "广州", "深圳")

SYSTEM = (
    "你是一个工具助手。需要查天气或做算术时，先调用工具，再根据工具返回结果回答。"
    "不要凭空编造温度或计算结果。"
)


# ---------------------------------------------------------------------------
# 离线模型：规则驱动，接口与真模型一致（返回 OpenAI 格式的 message）
# ---------------------------------------------------------------------------
class OfflineModel:
    name = "offline-rules"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, messages: list[dict]) -> dict:
        self.calls += 1

        # 已经有工具结果了 → 根据结果作答
        if messages[-1]["role"] == "tool":
            facts = []
            for m in messages:
                if m["role"] != "tool":
                    continue
                payload = json.loads(m["content"])
                if "error" in payload:
                    facts.append(f"工具报错：{payload['error']}")
                elif "temp" in payload:
                    facts.append(f"{payload['city']}今天{payload['sky']}，{payload['temp']}°{'C' if payload['unit'] == 'c' else 'F'}，湿度 {payload['humidity']}%")
                else:
                    facts.append(f"{payload['expression']} = {payload['value']}")
            return {"role": "assistant", "content": "；".join(facts) + "。"}

        question = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        call = self._decide(question)
        if call:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{uuid.uuid4().hex[:8]}",
                        "type": "function",
                        "function": {
                            "name": call[0],
                            "arguments": json.dumps(call[1], ensure_ascii=False),
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "这个问题不需要工具，我直接回答：我不知道。"}

    def _decide(self, question: str):
        if any(k in question for k in ("天气", "气温", "下雨", "多少度", "热不热", "冷不冷")):
            # 关键：模型会照着句式抠城市名，抠出来的可能不在枚举里（"杭州"）。
            # 这正是参数校验要拦的场景 —— 不是不让调，而是让错误可见。
            m = re.search(r"([\u4e00-\u9fa5]{2,4}?)(?:今天|的)?天气", question)
            city = next((c for c in CITIES if c in question), None) or (m.group(1) if m else None)
            if city:
                return ("get_weather", {"city": city})
        expr = re.search(r"[0-9(][0-9+\-*/().%\s]{2,}", question)
        if expr and any(k in question for k in ("算", "等于", "多少", "计算", "得几")):
            return ("calculate", {"expression": expr.group(0).strip()})
        if expr:
            return ("calculate", {"expression": expr.group(0).strip()})
        return None


# 真模型版本（配了 key 就能用，接口与 OfflineModel 相同）
def chat_api(messages: list[dict]) -> dict:
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
                "tools": TOOLS,
                "tool_choice": "auto",
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]


# ---------------------------------------------------------------------------
# Agent 循环：本轮唯一要写对的 20 行
# ---------------------------------------------------------------------------
def run_once(question: str, model=None, max_rounds: int = MAX_ROUNDS) -> dict:
    model = model or OfflineModel()
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": question},
    ]
    trace: list[dict] = []

    for round_no in range(1, max_rounds + 1):
        msg = model.complete(messages)
        trace.append({"round": round_no, "response": msg})

        calls = msg.get("tool_calls") or []
        if not calls:
            return {
                "answer": msg.get("content") or "",
                "messages": messages,
                "trace": trace,
                "rounds": round_no,
                "stopped": "answer",
            }

        # assistant 的 tool_calls 必须先入历史，否则后面的 role=tool 找不到对应的 id
        messages.append(
            {"role": "assistant", "content": msg.get("content"), "tool_calls": calls}
        )

        for call in calls:
            name = call["function"]["name"]
            raw = call["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {}
                result = {
                    "name": name,
                    "ok": False,
                    "content": json.dumps({"error": "arguments 不是合法 JSON"}, ensure_ascii=False),
                }
            else:
                result = execute(name, args).to_dict()

            trace.append({"round": round_no, "tool": name, "args": args, "result": result})
            # tool_call_id 必须与上面那条 call 的 id 一致，否则真模型会报 400
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "name": name,
                    "content": result["content"],
                }
            )

    return {
        "answer": "达到最大轮次仍未得到最终答案。",
        "messages": messages,
        "trace": trace,
        "rounds": max_rounds,
        "stopped": "max_rounds",
    }


class ChatIn(BaseModel):
    question: str = Field(min_length=1)
    max_rounds: int = Field(default=MAX_ROUNDS, ge=1, le=6)


class ToolCallIn(BaseModel):
    name: str = Field(min_length=1)
    arguments: dict = Field(default_factory=dict)


@app.get("/health", summary="有哪些工具")
def health() -> dict:
    return {
        "ok": True,
        "tools": [t["function"]["name"] for t in TOOLS],
        "model": os.getenv("OPENAI_MODEL") or "offline-rules",
        "max_rounds": MAX_ROUNDS,
    }


@app.get("/tools", summary="工具声明（JSON Schema）")
def list_tools() -> dict:
    return {"tools": TOOLS}


@app.post("/tool", summary="直接执行一个工具（调试用）")
def call_tool(req: ToolCallIn) -> dict:
    return execute(req.name, req.arguments).to_dict()


@app.post("/chat", summary="一问一答，中间的工具调用全部可见")
def chat(req: ChatIn) -> dict:
    if os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY"):
        try:
            return run_once(req.question, chat_api, req.max_rounds)
        except Exception as e:  # noqa: BLE001 - 真实调用失败时退回离线，保证演示不中断
            out = run_once(req.question, None, req.max_rounds)
            out["fallback_reason"] = f"{type(e).__name__}: {e}"
            return out
    return run_once(req.question, None, req.max_rounds)


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

    def raises(fn) -> tuple[bool, str]:
        try:
            fn()
        except ToolError as e:
            return True, str(e)
        return False, "没抛 ToolError"

    print("Unit 31 · Function Calling 自检（离线）\n")

    print("  —— 参数校验：模型给的 JSON 不可信 ——")
    ok, msg = raises(lambda: validate_args("get_weather", {}))
    check("缺必填参数被拒绝", ok and "city" in msg, msg)
    ok, msg = raises(lambda: validate_args("get_weather", {"city": 123}))
    check("类型不对被拒绝", ok, msg)
    ok, msg = raises(lambda: validate_args("get_weather", {"city": "杭州"}))
    check("枚举外的城市被拒绝", ok and "只能取" in msg, msg)
    ok, msg = raises(lambda: validate_args("get_weather", {"city": "上海", "extra": 1}))
    check("未定义参数被拒绝（严格模式）", ok and "未定义" in msg, msg)
    ok, msg = raises(lambda: validate_args("rm_rf", {}))
    check("未知工具被拒绝", ok, msg)
    check("合法参数通过并归一化", validate_args("get_weather", {"city": "上海"}) == {"city": "上海"})

    print("\n  —— 工具执行 ——")
    r = execute("get_weather", {"city": "上海"})
    check("天气工具返回结构化数据", r.ok and r.data["city"] == "上海", r.content)
    check("华氏度换算正确", execute("get_weather", {"city": "北京", "unit": "f"}).data["temp"] == 48.2)
    check("未知城市返回错误而不是崩溃", not execute("get_weather", {"city": "杭州"}).ok)
    check("计算器算对", execute("calculate", {"expression": "(120-30)*0.8"}).data["value"] == 72.0)
    check("计算器支持除法", execute("calculate", {"expression": "10/4"}).data["value"] == 2.5)

    print("\n  —— 计算器的安全边界（这里是最容易写出 RCE 的地方）——")
    for expr, label in [
        ('__import__("os").system("ls")', "拒绝 import"),
        ("open('/etc/passwd').read()", "拒绝函数调用"),
        ("2**999999", "拒绝超大指数"),
        ("a + b", "拒绝变量名"),
    ]:
        res = execute("calculate", {"expression": expr})
        check(label, not res.ok, res.content)

    print("\n  —— 一轮工具调用 ——")
    out = run_once("上海今天天气怎么样")
    tool_msgs = [m for m in out["messages"] if m["role"] == "tool"]
    asst_with_calls = [m for m in out["messages"] if m["role"] == "assistant" and m.get("tool_calls")]
    check("触发了工具调用", bool(asst_with_calls), str(out["trace"][0])[:80])
    check("执行了一条 role=tool 消息", len(tool_msgs) == 1, str(len(tool_msgs)))
    check("tool_call_id 与 assistant 的 id 对应", tool_msgs[0]["tool_call_id"] == asst_with_calls[0]["tool_calls"][0]["id"])
    check("最终答案是工具结果的自然语言", "上海" in out["answer"] and "°" in out["answer"], out["answer"])
    check("trace 里同时有模型响应和工具执行", len(out["trace"]) >= 3, str(len(out["trace"])))
    check("只用了 2 轮（调工具 → 出答案）", out["rounds"] == 2, str(out["rounds"]))

    out2 = run_once("(120-30)*0.8 等于多少")
    check("算术问题走 calculate", "72" in out2["answer"], out2["answer"])

    out3 = run_once("你们公司叫什么名字")
    check("不需要工具时直接回答", out3["rounds"] == 1 and not out3["messages"][-1].get("tool_calls"), str(out3["rounds"]))

    print("\n  —— 失败回填与轮次上限 ——")
    out4 = run_once("杭州天气怎么样")
    check("工具报错也变成可读回答", "没有数据" in out4["answer"] or "报错" in out4["answer"], out4["answer"])

    class LoopModel:
        """永远要求调工具的模型 —— 用来验证 max_rounds 真的拦得住。"""

        def complete(self, messages):
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{uuid.uuid4().hex[:6]}",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city":"上海"}'},
                    }
                ],
            }

    out5 = run_once("死循环测试", LoopModel(), max_rounds=3)
    check("到达轮次上限会停下", out5["stopped"] == "max_rounds", out5["stopped"])
    check("不会无限调用工具", out5["rounds"] == 3, str(out5["rounds"]))

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    check("GET /health", client.get("/health").status_code == 200)
    body = client.get("/tools").json()
    check("GET /tools 返回 JSON Schema", body["tools"][0]["function"]["parameters"]["required"] == ["city"])

    body = client.post("/chat", json={"question": "北京今天多少度"}).json()
    check("POST /chat 返回答案与 trace", "北京" in body["answer"] and body["trace"])

    body = client.post("/tool", json={"name": "calculate", "arguments": {"expression": "6*7"}}).json()
    check("POST /tool 可直接调试工具", body["ok"] and body["data"]["value"] == 42)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
