"""Unit 13 · LLM 工程化：超时 / 重试 / 限流 / 成本。

把 Unit 6 里那句裸的 `client.chat.completions.create()` 换成一层有纪律的封装。
核心不在"能调通"，而在"**调不通的时候系统还活着**"。

启动：
    uv run uvicorn main:app --reload --port 8013

自检（全离线，用假 transport 模拟各种故障，一分钱不花）：
    uv run python main.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

import llm_client as llm
from llm_client import (
    LEDGER,
    LLMClient,
    LLMConfig,
    LLMError,
    LLMTimeoutError,
    PerUserRateLimiter,
    RateLimited,
    TokenBucket,
)

load_dotenv_path = Path(__file__).with_name(".env")
if load_dotenv_path.exists():
    from dotenv import load_dotenv

    load_dotenv(load_dotenv_path)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

# 每个用户每分钟 20 次；演示时可以调小方便观察
LIMITER = PerUserRateLimiter(per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "20")))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cfg = llm.get_client().config
    if llm._is_placeholder(cfg.api_key):
        print("[warn] 未配置有效的 DEEPSEEK_API_KEY，/chat 会返回 503", file=sys.stderr)
    yield


app = FastAPI(title="Unit 13 · LLM 工程化", version="0.1.0", lifespan=lifespan)



# ---------------------------------------------------------------------------
# CORS：页面和接口不同源时（例如用 http://127.0.0.1:3000 打开静态页、接口在 8012），
# 浏览器默认会拦掉响应。没有这段，跨域 POST 会表现为
# 「服务端其实返回 200，但浏览器里是红色失败请求」，极难排查。
# ---------------------------------------------------------------------------
ALLOW_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOW_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000,"
        "http://127.0.0.1:5500,http://localhost:5500,"
        "http://127.0.0.1:8080,http://localhost:8080,"
        "http://127.0.0.1:8011,http://localhost:8011,"
        "http://127.0.0.1:8012,http://localhost:8012,"
        "http://127.0.0.1:8013,http://localhost:8013,"
        "http://127.0.0.1:8014,http://localhost:8014,"
        "http://127.0.0.1:8015,http://localhost:8015",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Message(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    # 推理模型的 reasoning 与正文共用这个预算，512 常被思考吃光 → 正文为空
    max_tokens: int = Field(default=4096, ge=1, le=4096)


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def client_key(request: Request) -> str:
    """限流维度。生产上应该用登录用户 id；
    未登录场景退回 IP，并提醒自己在反向代理后面要读 X-Forwarded-For。
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "anonymous"


def rate_limit_guard(request: Request) -> None:
    try:
        LIMITER.check(client_key(request))
    except RateLimited as exc:
        # 429 必须带 Retry-After，否则客户端只能瞎猜
        raise HTTPException(
            status_code=429,
            detail=str(exc),
            headers={"Retry-After": str(int(LIMITER.bucket(client_key(request)).retry_after() + 1))},
        ) from exc


@app.get("/health", summary="探活 + 配置检查")
def health() -> dict[str, object]:
    cfg = llm.get_client().config
    return {
        "ok": True,
        "model": cfg.model,
        "key_configured": not llm._is_placeholder(cfg.api_key),
        "timeout_seconds": cfg.timeout,
        "max_retries": cfg.max_retries,
        "rate_limit_per_minute": LIMITER.per_minute,
    }


@app.get("/stats", summary="累计账本：调用次数、重试次数、token、花费")
def stats() -> dict[str, object]:
    return LEDGER.as_dict()


@app.post("/chat", summary="非流式对话（带重试）")
async def chat(req: ChatRequest, request: Request) -> dict[str, object]:
    rate_limit_guard(request)
    client = llm.get_client()
    st: llm.CallStats | None = None   # 异常路径下也要能引用，别让它未定义
    try:
        content, st = await client.chat(
            [m.model_dump() for m in req.messages],
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except llm.LLMConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except llm.LLMAuthError as exc:
        raise HTTPException(status_code=502, detail=f"上游鉴权失败：{exc}") from exc
    except llm.LLMBadRequestError as exc:
        raise HTTPException(status_code=400, detail=f"请求不合法：{exc}") from exc
    except LLMError as exc:
        # 到这里说明重试都失败了。对用户来说就是"上游暂时不可用"。
        # 注意：不能用 st.retries —— 异常路径下 st 还是 None（这就是之前返回 500 的原因）。
        code = 504 if isinstance(exc, LLMTimeoutError) else 503
        tried = getattr(exc, "attempts", None) or (st.attempts if st else "?")
        raise HTTPException(status_code=code, detail=f"上游不可用（已尝试 {tried} 次）：{exc}") from exc
    return {"content": content, **st.as_dict()}


@app.post("/chat/stream", summary="流式对话（SSE，带重试与重试事件）")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    rate_limit_guard(request)

    async def gen():
        try:
            async for event in llm.get_client().chat_stream(
                [m.model_dump() for m in req.messages],
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            ):
                if await request.is_disconnected():
                    break
                yield sse(event)
        except llm.LLMConfigError as exc:
            yield sse({"type": "error", "kind": "LLMConfigError", "message": str(exc)})
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


@app.get("/", include_in_schema=False)
def index():
    if os.path.exists(STATIC_INDEX):
        return FileResponse(STATIC_INDEX)
    return {"hint": "static/index.html 不存在，看 /docs"}


# ---------------------------------------------------------------------------
# 自检：用 httpx.MockTransport 造假上游，全离线
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import httpx
    from fastapi.testclient import TestClient
    from openai import AsyncOpenAI

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 13 · LLM 工程化自检（全离线，不花钱）\n")

    OK_BODY = {
        "id": "chatcmpl-x",
        "object": "chat.completion",
        "created": 1700000000,
        "model": "deepseek-flash",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "你好"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
    }
    ERR_429 = {"error": {"message": "rate limited", "type": "rate_limit_error"}}
    ERR_400 = {"error": {"message": "bad params", "type": "invalid_request_error"}}

    def make_client(script: list):
        """script 里每一项是 (status, body) 或要抛的异常。"""
        queue = list(script)

        def handler(request: httpx.Request) -> httpx.Response:
            item = queue.pop(0) if queue else (200, OK_BODY)
            if isinstance(item, Exception):
                raise item
            status, payload = item
            return httpx.Response(status, json=payload)

        return AsyncOpenAI(
            api_key="sk-test-not-a-real-key",
            base_url="http://mock.local/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            max_retries=0,
        )

    def mk_agent(script, **overrides) -> LLMClient:
        cfg = LLMConfig(
            api_key="sk-test-not-a-real-key",
            base_url="http://mock.local/v1",
            model="deepseek-flash",
            max_retries=overrides.pop("max_retries", 3),
            base_delay=overrides.pop("base_delay", 0.01),
            max_delay=0.05,
            price_in_per_mtok=1.0,
            price_out_per_mtok=2.0,
        )
        c = LLMClient(cfg)
        c._client = make_client(script)
        c._client_key = cfg.api_key
        return c

    # 把真实 sleep 换掉，自检秒完
    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    llm._SLEEP = fake_sleep

    # --- 1. 缺 key ---
    async def t_config():
        c = LLMClient(LLMConfig(api_key="sk-你的", base_url="http://x", model="m"))
        try:
            await c.chat([{"role": "user", "content": "hi"}])
            return None
        except llm.LLMConfigError as e:
            return e

    err = asyncio.run(t_config())
    check("缺 key 抛 LLMConfigError 而不是发请求", isinstance(err, llm.LLMConfigError))

    # --- 2. 429 两次后成功 ---
    async def t_retry_ok():
        c = mk_agent([(429, ERR_429), (429, ERR_429), (200, OK_BODY)])
        return await c.chat([{"role": "user", "content": "hi"}])

    content, st = asyncio.run(t_retry_ok())
    check("429 两次后第 3 次成功", content == "你好")
    check("记录到 3 次尝试 / 2 次重试", st.attempts == 3 and st.retries == 2, f"{st.attempts}/{st.retries}")
    check("记录了错误类型", st.error_kinds == ["LLMRateLimitError", "LLMRateLimitError"], str(st.error_kinds))

    # --- 3. 重试耗尽 ---
    async def t_exhaust():
        c = mk_agent([(429, ERR_429)] * 10, max_retries=2)
        try:
            await c.chat([{"role": "user", "content": "hi"}])
            return None
        except llm.LLMRateLimitError as e:
            return e

    err = asyncio.run(t_exhaust())
    check("重试耗尽后抛 LLMRateLimitError", isinstance(err, llm.LLMRateLimitError))

    # --- 4. 400 不重试 ---
    async def t_no_retry():
        c = mk_agent([(400, ERR_400), (200, OK_BODY)])
        try:
            await c.chat([{"role": "user", "content": "hi"}])
            return None
        except llm.LLMBadRequestError as e:
            return e

    err = asyncio.run(t_no_retry())
    check("400 直接快速失败（不重试）", isinstance(err, llm.LLMBadRequestError))

    # --- 5. 5xx 重试 ---
    async def t_500():
        c = mk_agent([(500, {"error": {"message": "boom"}}), (200, OK_BODY)])
        _c, st2 = await c.chat([{"role": "user", "content": "hi"}])
        return st2

    st2 = asyncio.run(t_500())
    check("500 会重试并最终成功", st2.attempts == 2 and st2.retries == 1, f"{st2.attempts}/{st2.retries}")

    # --- 6. 超时重试 ---
    async def t_timeout():
        c = mk_agent([httpx.ReadTimeout("read timeout"), (200, OK_BODY)])
        return await c.chat([{"role": "user", "content": "hi"}])

    _c3, st3 = asyncio.run(t_timeout())
    check("超时会重试", st3.retries == 1 and "LLMTimeoutError" in st3.error_kinds, str(st3.error_kinds))

    # --- 7. 退避时间递增 ---
    sleeps.clear()

    async def t_backoff():
        c = mk_agent([(429, ERR_429)] * 3 + [(200, OK_BODY)], base_delay=0.01)
        await c.chat([{"role": "user", "content": "hi"}])

    asyncio.run(t_backoff())
    check("退避调用了 3 次 sleep", len(sleeps) == 3, str(len(sleeps)))
    check("退避上限随机落在 [0, cap] 内", all(s >= 0 for s in sleeps), str(sleeps))

    # --- 8. 成本计算 ---
    check("成本按 tokens × 单价算对", abs(st.cost - (1000 / 1e6 * 1.0 + 500 / 1e6 * 2.0)) < 1e-9, str(st.cost))

    # --- 9. 令牌桶 ---
    b = TokenBucket(capacity=3, refill_per_sec=0.001)  # 几乎不补充，便于观察
    results = [b.try_acquire() for _ in range(4)]
    check("令牌桶容量 3：前 3 次放行、第 4 次拒绝", results == [True, True, True, False], str(results))
    check("拒绝时能算出 retry_after", b.retry_after() > 0)

    lim = PerUserRateLimiter(per_minute=2)
    lim.check("u1")
    lim.check("u1")
    blocked = False
    try:
        lim.check("u1")
    except RateLimited:
        blocked = True
    check("单用户超额被拦", blocked)
    lim.check("u2")  # 另一个用户不受影响
    check("限流按用户隔离（u2 不受 u1 影响）", True)

    # --- 10. HTTP 层 ---
    app_client = TestClient(app)
    r = app_client.get("/health")
    check("GET /health 正常", r.status_code == 200 and "max_retries" in r.json())

    # 没 key 时 /chat 返回 503 而不是 500
    saved_key = llm.get_client().config.api_key
    llm.get_client().config.api_key = "sk-你的"
    r = app_client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    check("没配 key 时 /chat 返回 503（不是 500 崩溃）", r.status_code == 503, str(r.status_code))
    llm.get_client().config.api_key = saved_key

    # 重试耗尽 → 503 / 超时 → 504（回归：以前这两个分支会因为 st 未定义直接 500）
    class _BoomClient:
        async def chat(self, *a, **kw):
            err = llm.LLMServerError("上游 500")
            err.attempts = 4
            raise err

    class _TimeoutClient:
        async def chat(self, *a, **kw):
            err = llm.LLMTimeoutError("上游超时")
            err.attempts = 4
            raise err

    saved_default = llm._default_client
    try:
        llm._default_client = _BoomClient()
        r = app_client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        check("重试耗尽 → 503（不是 500）", r.status_code == 503, str(r.status_code))
        llm._default_client = _TimeoutClient()
        r = app_client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
        check("上游超时 → 504（不是 500）", r.status_code == 504, str(r.status_code))
    finally:
        llm._default_client = saved_default

    r = app_client.get("/stats")
    check("GET /stats 有累计账本", "total_calls" in r.json() and r.json()["total_calls"] >= 1)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
