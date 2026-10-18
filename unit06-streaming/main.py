"""Unit 6 · Streaming：把 LLM 的逐字输出变成浏览器里能看见的效果。

这个单元要打通的两条链路：

    ① 后端流式    FastAPI StreamingResponse  →  OpenAI SDK 的 stream=True
  
    ② 浏览器消费  fetch + ReadableStream     →  自己解析 SSE 报文

为什么不用 EventSource？
    EventSource 只支持 GET，且不能带请求体。我们要 POST 一段对话历史，
    所以必须用 fetch + ReadableStream 手动解析。

启动：
    uv run uvicorn main:app --reload --port 8000
    然后打开 http://127.0.0.1:8000/static/index.html

自检（不花钱，不调模型）：
    python main.py
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path

# --- venv 自愈：忘记 activate 也能跑（自动切换到本目录的 .venv） ---
def _reexec_in_venv():
    import os as _os
    import sys as _sys

    if _sys.prefix != _sys.base_prefix:
        return
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _venv_py = _os.path.join(_here, ".venv", "bin", "python")
    if _os.path.exists(_venv_py):
        _os.execv(_venv_py, [_venv_py, _os.path.abspath(__file__)] + _sys.argv[1:])
# --- venv 自愈 结束 ---

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).with_name(".env"))

API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

app = FastAPI(title="Unit 6 · Streaming", version="0.1.0")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=4096)


class SimpleRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if not API_KEY or API_KEY.startswith("sk-你的"):
        raise HTTPException(
            status_code=500,
            detail="缺少 DEEPSEEK_API_KEY，请在 .env 里配置（cp .env.example .env）",
        )
    if _client is None:
        _client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=60.0)
    return _client


# ---------------------------------------------------------------------------
# 1. 非流式：一次返回完整答案
# ---------------------------------------------------------------------------
@app.post("/chat", summary="非流式：等全部生成完再返回")
async def chat(req: ChatRequest) -> dict[str, object]:
    client = get_client()
    started = time.perf_counter()
    resp = await client.chat.completions.create(
        model=MODEL,
        messages=[m.model_dump() for m in req.messages],
        temperature=req.temperature,
        max_tokens=req.max_tokens,
    )
    elapsed = time.perf_counter() - started
    return {
        "content": resp.choices[0].message.content,
        "usage": {
            "prompt_tokens": resp.usage.prompt_tokens if resp.usage else None,
            "completion_tokens": resp.usage.completion_tokens if resp.usage else None,
        },
        "elapsed_seconds": round(elapsed, 3),
        "mode": "non-stream",
    }


# ---------------------------------------------------------------------------
# 2. 流式：SSE
# ---------------------------------------------------------------------------
def sse_event(data: dict) -> str:
    """把字典编码成一条 SSE 报文。

    SSE 的格式是固定的：`data: <payload>` 后面跟两个换行。
    两个换行是「事件结束」的标志，客户端靠它切分。
    """
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_llm(messages: list[dict], req: ChatRequest) -> AsyncIterator[dict]:
    """调用 LLM 并逐块 yield 事件字典。

    设计成 yield dict 而不是 yield str：
      让上层决定怎么编码（SSE / NDJSON / 纯文本），逻辑和传输格式解耦。
    """
    client = get_client()
    started = time.perf_counter()
    first_token_at: float | None = None
    total_chars = 0

    stream = await client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=req.temperature,
        max_tokens=req.max_tokens,
        stream=True,
        stream_options={"include_usage": True},  # 让最后一块带上 token 统计
    )

    async for chunk in stream:
        if chunk.usage:  # 最后一个 chunk 才有
            yield {
                "type": "usage",
                "prompt_tokens": chunk.usage.prompt_tokens,
                "completion_tokens": chunk.usage.completion_tokens,
            }

        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if not delta:
            continue

        if first_token_at is None:
            first_token_at = time.perf_counter()
            yield {
                "type": "first_token",
                "ttft_seconds": round(first_token_at - started, 3),
            }

        total_chars += len(delta)
        yield {"type": "delta", "content": delta}

    yield {
        "type": "done",
        "total_seconds": round(time.perf_counter() - started, 3),
        "total_chars": total_chars,
    }


@app.post("/chat/stream", summary="流式：SSE 逐块返回")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    async def event_generator() -> AsyncIterator[str]:
        try:
            async for event in stream_llm([m.model_dump() for m in req.messages], req):
                # 关键：客户端断开后立刻停止，否则会继续消耗 token
                if await request.is_disconnected():
                    break
                yield sse_event(event)
        except HTTPException as exc:
            yield sse_event({"type": "error", "message": exc.detail})
        except Exception as exc:  # noqa: BLE001
            yield sse_event({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            return
        yield "data: [DONE]\n\n"  # 与 OpenAI 相同的结束标记

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 告诉 nginx 不要缓冲
        },
    )


# ---------------------------------------------------------------------------
# 3. 不依赖 LLM 的简单流式：用来先跑通链路、观察报文
# ---------------------------------------------------------------------------
@app.get("/stream/demo", summary="纯文本流式：不需要 API Key，先看懂报文长什么样")
async def stream_demo(
    text: str = "流式输出就是把一个回答切成很多小块，边生成边发送。",
    delay: float = 0.08,
) -> StreamingResponse:
    started = time.perf_counter()

    async def gen() -> AsyncIterator[str]:
        for i, ch in enumerate(text):
            yield sse_event(
                {
                    "type": "delta",
                    "index": i,
                    "content": ch,
                    "elapsed": round(time.perf_counter() - started, 3),
                }
            )
            await asyncio.sleep(delay)
        yield sse_event({"type": "done", "total_seconds": round(time.perf_counter() - started, 3)})
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/", summary="首页")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ---------------------------------------------------------------------------
# 自检：不需要 API Key，验证 SSE 编码与流式行为
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    try:
        from fastapi.testclient import TestClient
    except ImportError:
        print("需要 httpx：uv pip install httpx")
        sys.exit(1)

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 6 · Streaming 自检（不调模型）\n")

    # --- SSE 编码格式 ---
    ev = sse_event({"type": "delta", "content": "你好"})
    check("SSE 以 'data: ' 开头", ev.startswith("data: "))
    check("SSE 以两个换行结尾", ev.endswith("\n\n"))
    check("中文不被转义", "你好" in ev and "\\u" not in ev)
    check("可被 json 解析", json.loads(ev[6:].strip())["content"] == "你好")

    client = TestClient(app)

    # --- 纯文本流式 ---
    with client.stream("GET", "/stream/demo?text=abc&delay=0") as r:
        check("demo 返回 200", r.status_code == 200)
        check("content-type 是 event-stream", r.headers["content-type"].startswith("text/event-stream"))
        check("有 no-cache 头", r.headers.get("cache-control") == "no-cache")
        check("有 X-Accel-Buffering=no", r.headers.get("x-accel-buffering") == "no")
        body = "".join(r.iter_text())

    events = [ln[6:] for ln in body.splitlines() if ln.startswith("data: ") and ln[6:] != "[DONE]"]
    deltas = [json.loads(e) for e in events if json.loads(e).get("type") == "delta"]
    check("收到 3 个 delta", len(deltas) == 3, f"实际 {len(deltas)}")
    check("字符依次为 a/b/c", [d["content"] for d in deltas] == ["a", "b", "c"])
    check("有 done 事件", any(json.loads(e).get("type") == "done" for e in events))
    check("有 [DONE] 结束标记", "[DONE]" in body)

    # --- 断开连接的处理逻辑 ---
    # 直接验证：客户端中途关闭，生成器不应抛异常
    async def simulate():
        gen = await stream_demo(text="0123456789", delay=0)  # 注意要 await
        out = []
        async for item in gen.body_iterator:
            out.append(item)
            if len(out) >= 3:
                break  # 模拟客户端断开
        aclose = getattr(gen.body_iterator, "aclose", None)
        if aclose:
            await aclose()
        return len(out)

    n = asyncio.run(simulate())
    check("中途断开能干净退出", n == 3, f"实际 {n}")

    # --- 无 API Key 时的报错是 SSE 事件而不是 500 ---
    saved = os.environ.get("DEEPSEEK_API_KEY")
    os.environ["DEEPSEEK_API_KEY"] = ""
    import main as self_mod

    self_mod.API_KEY = ""
    try:
        with client.stream(
            "POST", "/chat/stream", json={"messages": [{"role": "user", "content": "hi"}]}
        ) as r:
            check("缺 Key 时仍返回 200（错误走 SSE 事件）", r.status_code == 200)
            body2 = "".join(r.iter_text())
        check("错误信息以 error 事件下发", '"type": "error"' in body2 or '"type":"error"' in body2)
        check("错误里有配置提示", "DEEPSEEK_API_KEY" in body2)
    finally:
        self_mod.API_KEY = saved or ""
        if saved is not None:
            os.environ["DEEPSEEK_API_KEY"] = saved

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
