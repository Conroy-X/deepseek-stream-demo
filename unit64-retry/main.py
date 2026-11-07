"""Unit 64 · Retry —— 把重试器接进一个带「假不稳定 LLM」的接口。

启动：
    uv run uvicorn main:app --reload --port 8064

自检（离线，不联网）：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from retry import PermanentError, RetryableError, retry

app = FastAPI(title="Unit 64 · Retry", version="0.1.0")

# 假不稳定 LLM：前 2 次抛 RetryableError，第 3 次成功；参数含 "bad" 时抛 PermanentError
_CALLS = {"n": 0}
_RETRY_EVENTS: list[tuple[int, str, float]] = []


def fake_llm(q: str) -> str:
    _CALLS["n"] += 1
    if "bad" in q:
        raise PermanentError("参数含非法内容（4xx 语义，不该重试）")
    if _CALLS["n"] < 3:
        raise RetryableError("上游 503，暂时不可用")
    return f"回答:{q}（第 {_CALLS['n']} 次调用才成功）"


@app.get("/ask", summary="带重试的问答：前两次 503 会自动重试")
async def ask(q: str = Query(min_length=1, max_length=100)) -> dict:
    _CALLS["n"] = 0
    _RETRY_EVENTS.clear()
    try:
        answer = retry(
            lambda: fake_llm(q),
            max_attempts=4,
            base=0.05,
            max_time=5.0,
            on_retry=lambda a, e, d: _RETRY_EVENTS.append((a, type(e).__name__, round(d, 3))),
        )
        return {"answer": answer, "retries": len(_RETRY_EVENTS), "events": _RETRY_EVENTS}
    except PermanentError as e:
        return JSONResponse({"error": str(e), "retries": 0}, status_code=400)
    except RetryableError as e:
        return JSONResponse({"error": f"重试耗尽: {e}", "retries": len(_RETRY_EVENTS)}, status_code=503)


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/ask?q=hello（前两次503自动重试）或 /ask?q=bad（4xx不重试）"})


if __name__ == "__main__":
    sys.exit(__import__("retry").run_selfcheck())
