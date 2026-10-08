"""最小可用的 DeepSeek 流式问答 Demo（单文件，不带多轮对话）。

启动：
    ./run.sh                          # 或 .venv/bin/uvicorn main:app --port 8001

调用：
    浏览器  http://localhost:8001/ask?q=用一句话介绍 FastAPI
    curl -N "http://localhost:8001/ask?q=用一句话介绍 FastAPI"

只做两件事：拿参数里的问题去问 DeepSeek，然后把答案边收边吐给客户端。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

load_dotenv(Path(__file__).with_name(".env"))  # 按文件位置读 .env，不依赖启动目录

API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEFAULT_MODEL", "deepseek-flash")

app = FastAPI(title="DeepSeek 流式问答 Demo", version="0.1.0")


@app.get("/", summary="用法提示")
async def index() -> dict[str, str]:
    return {
        "usage": "/ask?q=你的问题",
        "example": "/ask?q=用一句话介绍 FastAPI",
    }


@app.get("/ask", summary="提问并流式返回答案")
async def ask(q: str = Query(..., min_length=1, description="要问的问题")) -> StreamingResponse:
    if not API_KEY:
        raise HTTPException(status_code=500, detail="请在 .env 里配置 DEEPSEEK_API_KEY")

    async def stream() -> AsyncIterator[str]:
        payload = {
            "model": MODEL,
            "stream": True,
            # 单轮：只把当前这个问题发过去，不带任何历史
            "messages": [{"role": "user", "content": q}],
        }
        headers = {"Authorization": f"Bearer {API_KEY}"}

        async with httpx.AsyncClient(base_url=BASE_URL, timeout=120.0) as client:
            async with client.stream(
                "POST", "/chat/completions", json=payload, headers=headers
            ) as response:
                if response.status_code != 200:
                    detail = (await response.aread()).decode("utf-8", "replace")
                    yield f"[上游错误 {response.status_code}] {detail}"
                    return

                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        delta = json.loads(data)["choices"][0].get("delta") or {}
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    piece = delta.get("content")
                    if piece:
                        print("piece:", piece)  # 打印到服务端日志
                        yield piece  # 收到一小段就立刻推给客户端

    return StreamingResponse(stream(), media_type="text/plain; charset=utf-8")
