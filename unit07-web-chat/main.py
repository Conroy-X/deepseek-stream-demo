"""Unit 7 · 全栈打通：后端（FastAPI + SSE + CORS）。

本单元的目标：**浏览器 → 后端 → LLM → 流式回浏览器**，整条链路跑通。

与 Unit 6 的区别：
    Unit 6 是同源访问（页面由 FastAPI 自己托管），不会遇到 CORS。
    Unit 7 前后端**分开部署**（前端 3000 / 后端 8000），必然跨域，
    所以这里必须显式配置 CORSMiddleware —— 这是 90% 的人第一次会卡住的地方。

启动：
    .venv/bin/uvicorn main:app --reload --port 8000

自检（不调模型）：
    .venv/bin/python main.py
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).with_name(".env"))

API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

# 允许跨域的前端地址。多个用逗号分隔。
# 注意：不能写 "*" 和 credentials 同时用；这里显式列出更安全。
ALLOW_ORIGINS = os.getenv(
    "ALLOW_ORIGINS",
    "http://127.0.0.1:3000,http://localhost:3000,"
    "http://127.0.0.1:5500,http://localhost:5500,"
    "http://127.0.0.1:8080,http://localhost:8080,"
    "null",  # file:// 打开时的 Origin
).split(",")

app = FastAPI(title="Unit 7 · 全栈打通", version="0.1.0")

# ---------------------------------------------------------------------------
# CORS：跨域必需。没有它，浏览器会拦掉所有请求，
# 而且报错出现在浏览器控制台，后端日志里**看不到任何东西** —— 这是最坑的地方。
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOW_ORIGINS if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # 让前端 JS 能读到这些响应头（默认只暴露少数几个）
    expose_headers=["x-ds-trace-id", "x-request-id"],
)


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=4096)


_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if not API_KEY or API_KEY.startswith("sk-你的"):
        raise HTTPException(
            status_code=500,
            detail="缺少 DEEPSEEK_API_KEY，请在 .env 里配置",
        )
    if _client is None:
        _client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=60.0)
    return _client


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/health", summary="健康检查：前端可用它探测后端是否在线")
async def health() -> dict[str, object]:
    return {
        "ok": True,
        "model": MODEL,
        "key_configured": bool(API_KEY) and not API_KEY.startswith("sk-你的"),
        "allow_origins": [o.strip() for o in ALLOW_ORIGINS if o.strip()],
    }


@app.post("/chat/stream", summary="流式问答（SSE）")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        started = time.perf_counter()
        try:
            client = get_client()
            stream = await client.chat.completions.create(
                model=MODEL,
                messages=[m.model_dump() for m in req.messages],
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            first_sent = False
            async for chunk in stream:
                if await request.is_disconnected():
                    break
                if chunk.usage:
                    yield sse(
                        {
                            "type": "usage",
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                        }
                    )
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if not delta:
                    continue
                if not first_sent:
                    first_sent = True
                    yield sse(
                        {"type": "first_token", "ttft": round(time.perf_counter() - started, 3)}
                    )
                yield sse({"type": "delta", "content": delta})
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
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/stream/demo", summary="不依赖模型的纯文本流式，用来验证链路")
async def demo(text: str = "这是一段用于验证链路是否通畅的示例文本。", delay: float = 0.06):
    async def gen() -> AsyncIterator[str]:
        for ch in text:
            yield sse({"type": "delta", "content": ch})
            import asyncio

            await asyncio.sleep(delay)
        yield sse({"type": "done"})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# 自检：不需要 API Key
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

    print("Unit 7 · 全栈打通 自检（不调模型）\n")
    client = TestClient(app)

    # --- 健康检查 ---
    r = client.get("/health")
    check("health 返回 200", r.status_code == 200)
    check("health 含 model", "model" in r.json())

    # --- CORS 预检（OPTIONS）---
    r = client.options(
        "/chat/stream",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    check("CORS 预检返回 200", r.status_code == 200, str(r.status_code))
    check(
        "预检带回 allow-origin",
        r.headers.get("access-control-allow-origin") == "http://localhost:3000",
        r.headers.get("access-control-allow-origin", "(无)"),
    )
    check(
        "允许 POST 方法",
        "POST" in (r.headers.get("access-control-allow-methods") or "").upper(),
    )

    # --- 非白名单来源应被拒 ---
    r = client.options(
        "/chat/stream",
        headers={
            "Origin": "http://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    check(
        "非白名单来源不带 allow-origin",
        r.headers.get("access-control-allow-origin") is None,
        r.headers.get("access-control-allow-origin", "(none)"),
    )

    # --- 普通请求也带 CORS 头 ---
    r = client.get("/health", headers={"Origin": "http://localhost:3000"})
    check(
        "普通响应带 allow-origin",
        r.headers.get("access-control-allow-origin") == "http://localhost:3000",
    )

    # --- 流式链路 ---
    with client.stream("GET", "/stream/demo?text=abc&delay=0") as resp:
        check("demo 200", resp.status_code == 200)
        check("content-type 正确", resp.headers["content-type"].startswith("text/event-stream"))
        body = "".join(resp.iter_text())
    events = [ln[6:] for ln in body.splitlines() if ln.startswith("data: ") and ln != "data: [DONE]"]
    deltas = [json.loads(e) for e in events if json.loads(e).get("type") == "delta"]
    check("收到 3 个字符", len(deltas) == 3, str(len(deltas)))
    check("字符顺序正确", [d["content"] for d in deltas] == ["a", "b", "c"])
    check("有 [DONE]", "[DONE]" in body)

    # --- 缺 Key 时错误走 SSE ---
    # 改「当前正在运行的这个模块」。python main.py 时模块身份是 __main__，
    # 用 import main 会得到另一个副本，对副本赋值不影响 get_client() 读到的 API_KEY。
    self_mod = sys.modules["__main__"]

    saved = self_mod.API_KEY
    try:
        self_mod.API_KEY = ""
        with client.stream(
            "POST", "/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}
        ) as resp:
            check("缺 Key 仍 200", resp.status_code == 200)
            b2 = "".join(resp.iter_text())
        check("错误以 SSE 事件下发", '"error"' in b2)
        check("错误提示含 DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY" in b2)
    finally:
        self_mod.API_KEY = saved

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
