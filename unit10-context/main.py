"""Unit 10 · 上下文管理 —— 把三种策略接进真实对话接口。

与 Unit 9 的区别：
    Unit 9 每轮把**完整历史**发出去。
    Unit 10 在发出前先按预算裁剪，并在响应里返回「本次用了多少 token、丢了什么」。

核心接口：
    POST /chat/stream   支持 strategy=none|sliding|summarize
    GET  /stats/{cid}   查看某个会话的 token 占用
    GET  /compare       用同一份历史对比三种策略（不调模型）

启动（uv 托管：自动用本目录的 .venv 与 uv.lock）：
    uv run uvicorn main:app --reload --port 8000

自检（不花钱）：
    uv run python main.py

测试 context 模块：
    uv run python test_context.py
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from context import (
    Budget,
    TrimResult,
    estimate_tokens,
    messages_tokens,
    trim_messages,
)

load_dotenv(Path(__file__).with_name(".env"))

API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

# 上下文预算：历史最多用多少 token
MAX_HISTORY_TOKENS = int(os.getenv("MAX_HISTORY_TOKENS", "2000"))
RESERVED_OUTPUT = int(os.getenv("RESERVED_OUTPUT", "1024"))
SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "你是一个乐于助人的 AI 助手。回答准确、简洁。如果对话被截断过，只需基于现有信息作答。",
)

ALLOW_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOW_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:5500,"
        "http://localhost:5500,http://127.0.0.1:8080,http://localhost:8080,null",
    ).split(",")
    if o.strip()
]

app = FastAPI(title="Unit 10 · 上下文管理", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if not API_KEY or API_KEY.startswith("sk-你的"):
        raise HTTPException(status_code=500, detail="缺少 DEEPSEEK_API_KEY，请在 .env 配置")
    if _client is None:
        _client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=90.0)
    return _client


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# 摘要器：用一个便宜的小请求把旧对话压缩
# ---------------------------------------------------------------------------
async def llm_summarizer(old_messages: list[dict]) -> str:
    """把旧对话压成 3 句话以内的摘要。"""
    transcript = "\n".join(
        f"{m['role']}: {m['content'][:400]}" for m in old_messages
    )
    client = get_client()
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是一个对话摘要器。把用户提供的多轮对话压缩成不超过 3 句话的中文摘要，"
                    "保留：用户的关键信息、已确认的事实、未完成的任务。不要评论，直接输出摘要。"
                ),
            },
            {"role": "user", "content": f"对话记录：\n{transcript}\n\n请输出摘要："},
        ],
        temperature=0.2,
        # 摘要虽然短，但推理模型会先在思考里花掉一部分；300 容易只拿到空串
        max_tokens=800,
    )
    return (resp.choices[0].message.content or "").strip()


async def trim_with_llm_summary(
    messages: list[dict], budget: int, keep_recent: int = 4
) -> "TrimResult":
    """异步版的摘要裁剪。

    context.trim_summarize 的 summarizer 是同步接口（便于离线测试），
    但真实摘要要 await 模型调用，所以这里单独写一份异步实现。
    两边的裁剪规则保持一致：System + [摘要] + 最近 keep_recent 条。
    """
    original = messages_tokens(messages)

    system = [messages[0]] if messages and messages[0].get("role") == "system" else []
    rest = messages[1:] if system else messages

    # 没超预算：原样返回，不花额外的摘要调用费
    if original <= budget:
        return TrimResult(list(messages), "summarize", original, original)

    recent = rest[-keep_recent:] if len(rest) > keep_recent else rest
    older = rest[: len(rest) - len(recent)]

    summary_text = await llm_summarizer(older) if older else ""

    final: list[dict] = list(system)
    if summary_text:
        final.append({"role": "system", "content": f"以下是之前对话的摘要：\n{summary_text}"})
    final.extend(recent)

    return TrimResult(
        messages=final,
        strategy="summarize",
        original_tokens=original,
        final_tokens=messages_tokens(final),
        dropped_messages=len(older),
        summarized=bool(summary_text),
        summary=summary_text,
    )


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    strategy: Literal["none", "sliding", "summarize"] = "sliding"
    max_history_tokens: int | None = Field(default=None, ge=100, le=200_000)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    # 推理模型：思考 token 与正文共用这个预算，1024 常被思考吃光
    max_tokens: int = Field(default=4096, ge=1, le=4096)


# ---------------------------------------------------------------------------
# 主接口：带裁剪的流式对话
# ---------------------------------------------------------------------------
@app.post("/chat/stream", summary="流式对话（自动做上下文裁剪）")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        started = time.perf_counter()
        budget = req.max_history_tokens or MAX_HISTORY_TOKENS

        raw = [m.model_dump() for m in req.messages]
        # 确保有 System
        if raw[0].get("role") != "system":
            raw = [{"role": "system", "content": SYSTEM_PROMPT}] + raw

        # 先告诉前端「裁剪前后」的情况
        before = messages_tokens(raw)
        yield sse(
            {
                "type": "trim",
                "strategy": req.strategy,
                "budget": budget,
                "before_tokens": before,
            }
        )

        try:
            if req.strategy == "summarize":
                trim = await trim_with_llm_summary(raw, budget, yield_status=None)
            else:
                trim = trim_messages(raw, max_tokens=budget, strategy=req.strategy)

            yield sse(
                {
                    "type": "trimmed",
                    "final_tokens": trim.final_tokens,
                    "dropped": trim.dropped_messages,
                    "summarized": trim.summarized,
                    "messages_sent": len(trim.messages),
                }
            )
            if trim.summarized:
                yield sse({"type": "summary", "text": trim.summary})

            client = get_client()
            stream = await client.chat.completions.create(
                model=MODEL,
                messages=trim.messages,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            first = True
            finish_reason: str | None = None
            reasoning_tokens: int | None = None
            async for chunk in stream:
                if await request.is_disconnected():
                    break
                if chunk.usage:
                    details = getattr(chunk.usage, "completion_tokens_details", None)
                    reasoning_tokens = getattr(details, "reasoning_tokens", None)
                    yield sse(
                        {
                            "type": "usage",
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                            "reasoning_tokens": reasoning_tokens,
                        }
                    )
                if not chunk.choices:
                    continue
                if chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
                delta = chunk.choices[0].delta.content
                if not delta:
                    continue
                if first:
                    first = False
                    yield sse({"type": "first_token", "ttft": round(time.perf_counter() - started, 3)})
                yield sse({"type": "delta", "content": delta})

            if not first:
                pass
            else:
                # 一个 delta 都没出：多半是推理把预算吃光了
                if finish_reason == "length":
                    msg = (
                        f"推理耗尽了 max_tokens={req.max_tokens}"
                        f"（reasoning_tokens≈{reasoning_tokens}），正文还没开始就被截断。"
                        "把 max_tokens 调到 4096 再试。"
                    )
                else:
                    msg = f"模型没有返回正文（finish_reason={finish_reason}）。"
                yield sse({"type": "error", "message": msg})
            yield sse({"type": "done", "elapsed": round(time.perf_counter() - started, 3)})
        except HTTPException as exc:
            yield sse({"type": "error", "message": exc.detail})
        except Exception as exc:  # noqa: BLE001
            yield sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# 对比接口：同一份历史，三种策略各裁一次（不调模型）
# ---------------------------------------------------------------------------
class CompareRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    max_history_tokens: int = Field(default=2000, ge=100, le=200_000)


@app.post("/compare", summary="对比三种策略的裁剪效果（不调模型、不花钱）")
async def compare(req: CompareRequest) -> dict:
    raw = [m.model_dump() for m in req.messages]
    if raw[0].get("role") != "system":
        raw = [{"role": "system", "content": SYSTEM_PROMPT}] + raw

    budget = req.max_history_tokens
    results = []
    for strat in ("none", "sliding"):
        r = trim_messages(raw, max_tokens=budget, strategy=strat)
        results.append(
            {
                "strategy": strat,
                "original_tokens": r.original_tokens,
                "final_tokens": r.final_tokens,
                "saved_ratio": round(r.saved_ratio, 4),
                "dropped": r.dropped_messages,
                "messages_sent": len(r.messages),
                "summarized": r.summarized,
            }
        )

    # summarize 用「假摘要」估算体积（真实摘要需调模型）
    from context import trim_summarize

    def fake(old: list[dict]) -> str:
        return "（此处是一段约 120 字的旧对话摘要，真实场景由模型生成）" + "。" * 40

    r = trim_summarize(raw, max_tokens=budget, summarizer=fake)
    results.append(
        {
            "strategy": "summarize(估算)",
            "original_tokens": r.original_tokens,
            "final_tokens": r.final_tokens,
            "saved_ratio": round(r.saved_ratio, 4),
            "dropped": r.dropped_messages,
            "messages_sent": len(r.messages),
            "summarized": r.summarized,
        }
    )
    return {"budget": budget, "results": results}


@app.get("/budget", summary="查看当前上下文预算配置")
async def get_budget() -> dict:
    b = Budget(total=64_000, reserved_output=RESERVED_OUTPUT, system=estimate_tokens(SYSTEM_PROMPT))
    return {
        "context_window": b.total,
        "reserved_output": b.reserved_output,
        "system_tokens": b.system,
        "max_history_tokens": MAX_HISTORY_TOKENS,
        "table": b.table(),
    }


@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "model": MODEL,
        "key_configured": bool(API_KEY) and not API_KEY.startswith("sk-你的"),
        "max_history_tokens": MAX_HISTORY_TOKENS,
    }


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    from fastapi.testclient import TestClient

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 10 · 上下文管理 自检（不调模型）\n")
    client = TestClient(app)

    r = client.get("/health")
    check("health 200", r.status_code == 200)
    check("含 max_history_tokens", "max_history_tokens" in r.json())

    r = client.get("/budget")
    check("budget 200", r.status_code == 200)
    check("含预算表", "table" in r.json() and "上下文预算表" in r.json()["table"])

    # --- compare 接口 ---
    history = [{"role": "system", "content": "你是助手。"}]
    for i in range(12):
        history.append({"role": "user", "content": f"第{i}轮问题" + "补" * 80})
        history.append({"role": "assistant", "content": f"第{i}轮回答" + "完" * 80})

    r = client.post("/compare", json={"messages": history, "max_history_tokens": 800})
    check("compare 200", r.status_code == 200)
    data = r.json()
    check("返回三种策略", len(data["results"]) == 3, str(len(data["results"])))
    names = [x["strategy"] for x in data["results"]]
    check("含 none", "none" in names)
    check("含 sliding", "sliding" in names)
    check("含 summarize", any("summarize" in n for n in names))

    none_r = next(x for x in data["results"] if x["strategy"] == "none")
    slide_r = next(x for x in data["results"] if x["strategy"] == "sliding")
    check("none 不裁剪", none_r["dropped"] == 0 and none_r["final_tokens"] == none_r["original_tokens"])
    check("sliding 确实省了", slide_r["final_tokens"] < slide_r["original_tokens"])
    check("sliding 未超预算", slide_r["final_tokens"] <= 800, str(slide_r["final_tokens"]))

    # --- 缺 Key 时错误走 SSE，且 trim 事件已下发 ---
    import sys as _sys

    self_mod = _sys.modules["__main__"]
    saved = self_mod.API_KEY
    try:
        self_mod.API_KEY = ""
        with client.stream(
            "POST",
            "/chat/stream",
            json={"messages": [{"role": "user", "content": "hi"}], "strategy": "sliding"},
        ) as resp:
            check("流式接口 200", resp.status_code == 200)
            body = "".join(resp.iter_text())
        check("收到 trim 事件", '"type": "trim"' in body)
        check("收到 trimmed 事件", '"type": "trimmed"' in body)
        check("缺 Key 时错误走 SSE", '"type": "error"' in body)
    finally:
        self_mod.API_KEY = saved

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
