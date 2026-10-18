"""Unit 1 · FastAPI 基础全貌（单文件版）。

对应原计划的 Day 1：路由、Path/Query、Pydantic、Async、异常、文件上传、Streaming。
把这份文件读透，就掌握了后面所有单元要用的 FastAPI 底座。

启动：
    uv run fastapi dev main.py        # 或在 venv 里：.venv/bin/uvicorn main:app --reload

打开 http://127.0.0.1:8000/docs 边看边试 —— FastAPI 自动生成的 Swagger 是本单元最强的学习工具。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

# --- venv 自愈：忘记 activate 也能跑（自动切换到本目录的 .venv） ---
def _reexec_in_venv():
    import os as _os
    import sys as _sys

    if _sys.prefix != _sys.base_prefix:
        return  # 已经在 venv 里
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _venv_py = _os.path.join(_here, ".venv", "bin", "python")
    if _os.path.exists(_venv_py):
        _os.execv(_venv_py, [_venv_py, _os.path.abspath(__file__)] + _sys.argv[1:])


_reexec_in_venv()
# --- venv 自愈 结束 ---

from fastapi import FastAPI, File, HTTPException, Path, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

app = FastAPI(
    title="Unit 1 · FastAPI 基础",
    version="0.1.0",
    description="路由 / Path / Query / Pydantic / Async / 异常 / 文件上传 / Streaming",
)


# ---------------------------------------------------------------------------
# 1. 最基础的路由
# ---------------------------------------------------------------------------
@app.get("/", summary="根路由：区分 GET 与路径")
async def index() -> dict[str, str]:
    return {"message": "Unit 1 FastAPI 基础", "docs": "/docs"}


# ---------------------------------------------------------------------------
# 2. Path 参数 + 类型校验
# ---------------------------------------------------------------------------
# 声明成 int 之后，FastAPI 会自动：
#   - 把 "abc" 拒掉并返回 422（不用你手写校验）
#   - 在 /docs 里标出该参数的类型
@app.get("/items/{item_id}", summary="Path 参数：自动类型转换与校验")
async def get_item(
    item_id: Annotated[int, Path(ge=1, description="商品 ID，必须 ≥ 1")],
    q: Annotated[str | None, Query(max_length=50, description="可选搜索词")] = None,
) -> dict[str, object]:
    return {"item_id": item_id, "q": q}


# ---------------------------------------------------------------------------
# 3. Query 参数：默认值、范围、枚举式校验
# ---------------------------------------------------------------------------
@app.get("/search", summary="Query 参数：分页与范围校验")
async def search(
    keyword: Annotated[str, Query(min_length=1, description="必填")],
    page: Annotated[int, Query(ge=1, description="页码，从 1 开始")] = 1,
    size: Annotated[int, Query(ge=1, le=100, description="每页条数，1~100")] = 10,
) -> dict[str, object]:
    return {"keyword": keyword, "page": page, "size": size, "items": [f"{keyword}-{i}" for i in range(size)]}


# ---------------------------------------------------------------------------
# 4. Pydantic 请求体：字段约束 + 嵌套模型
#   这是后面所有 LLM 接口的基础（结构化输出的校验就靠这套）
# ---------------------------------------------------------------------------
class Message(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$", description="角色")
    content: str = Field(min_length=1, max_length=8000, description="内容")


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, description="至少一条消息")
    temperature: float = Field(default=0.3, ge=0.0, le=2.0, description="随机性")
    max_tokens: int = Field(default=512, ge=1, le=4096, description="输出上限")


@app.post("/chat/validate", summary="Pydantic 请求体校验（不调模型，只看校验）")
async def validate_chat(req: ChatRequest) -> dict[str, object]:
    last = req.messages[-1]
    return {
        "message_count": len(req.messages),
        "last_role": last.role,
        "last_content_preview": last.content[:30],
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
    }


# ---------------------------------------------------------------------------
# 5. 异常处理：HTTPException + 自定义错误码
# ---------------------------------------------------------------------------
_FAKE_DB: dict[int, str] = {1: "苹果", 2: "香蕉"}


@app.get("/db/{item_id}", summary="异常：404 与 400 的区别")
async def read_from_fake_db(item_id: int) -> dict[str, object]:
    if item_id < 0:
        raise HTTPException(status_code=400, detail="item_id 不能为负数")
    if item_id not in _FAKE_DB:
        raise HTTPException(status_code=404, detail=f"item {item_id} 不存在")
    return {"item_id": item_id, "name": _FAKE_DB[item_id]}


# ---------------------------------------------------------------------------
# 6. Async：为什么 LLM 接口必须是 async
# ---------------------------------------------------------------------------
@app.get("/async-demo", summary="Async：并发等待不阻塞事件循环")
async def async_demo() -> dict[str, object]:
    """同时等待 3 个 0.5 秒的 IO，总耗时约 0.5 秒而不是 1.5 秒。

    这正是 LLM 接口的实现方式：等待上游 API 时让出事件循环，
    所以一个 worker 能同时服务很多个请求。
    """

    async def fake_io(n: int) -> int:
        await asyncio.sleep(0.5)
        return n * 10

    results = await asyncio.gather(*(fake_io(i) for i in range(1, 4)))
    return {"results": results, "note": "三个 0.5s 任务并发完成，总耗时约 0.5s"}


# ---------------------------------------------------------------------------
# 7. 文件上传
# ---------------------------------------------------------------------------
@app.post("/upload", summary="文件上传：读取大小与首行")
async def upload(file: Annotated[UploadFile, File(description="任意文本文件")]) -> dict[str, object]:
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
        preview = text.splitlines()[0] if text.splitlines() else ""
    except UnicodeDecodeError:
        preview = "(非 UTF-8 文本，无法预览)"
    return {
        "filename": file.filename,
        "content_type": file.content_type,
        "size_bytes": len(raw),
        "first_line": preview,
    }


# ---------------------------------------------------------------------------
# 8. Streaming：SSE 风格的分块返回
#    这是 Unit 3 之后所有"流式回答"的底层机制，先在无 LLM 的情况下看明白
# ---------------------------------------------------------------------------
@app.get("/stream", summary="Streaming：逐块返回（不依赖 LLM）")
async def stream(count: Annotated[int, Query(ge=1, le=20)] = 5) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        for i in range(1, count + 1):
            yield f"data: chunk-{i}\n\n"
            await asyncio.sleep(0.3)
        yield "data: [DONE]\n\n"

    # text/event-stream 是 SSE 的 MIME 类型；浏览器 EventSource 只认这个
    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# 自检入口：不启动服务也能验证所有路由与校验逻辑
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    try:
        from fastapi.testclient import TestClient
    except ImportError:
        print("需要 httpx 才能跑自检：uv pip install httpx")
        sys.exit(1)

    client = TestClient(app)
    passed = failed = 0

    def check(name: str, cond: bool, detail: str = "") -> None:
        global passed, failed
        if cond:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 1 · FastAPI 路由自检")
    check("根路由 200", client.get("/").status_code == 200)
    check("Path 正常", client.get("/items/5").json()["item_id"] == 5)
    check("Path 非数字 -> 422", client.get("/items/abc").status_code == 422)
    check("Path 越界 -> 422", client.get("/items/0").status_code == 422)
    check("Query 正常", client.get("/search", params={"keyword": "x", "size": 3}).json()["size"] == 3)
    check("Query 缺必填 -> 422", client.get("/search").status_code == 422)
    check("Query 超上限 -> 422", client.get("/search", params={"keyword": "x", "size": 999}).status_code == 422)

    good = {"messages": [{"role": "user", "content": "你好"}], "temperature": 0.5, "max_tokens": 100}
    check("Pydantic 正常请求 200", client.post("/chat/validate", json=good).status_code == 200)
    check("role 非法 -> 422", client.post("/chat/validate", json={"messages": [{"role": "bot", "content": "x"}]}).status_code == 422)
    check("消息为空 -> 422", client.post("/chat/validate", json={"messages": []}).status_code == 422)
    check("temperature 越界 -> 422", client.post("/chat/validate", json={**good, "temperature": 5}).status_code == 422)

    check("404 状态", client.get("/db/99").status_code == 404)
    check("400 状态", client.get("/db/-1").status_code == 400)
    check("400 的 detail", client.get("/db/-1").json()["detail"] == "item_id 不能为负数")

    check("async 并发返回三条", len(client.get("/async-demo").json()["results"]) == 3)
    check("上传返回大小", client.post("/upload", files={"file": ("a.txt", b"hello\nworld")}).json()["size_bytes"] == 11)
    r = client.get("/stream", params={"count": 3})
    check("流式 media type", r.headers["content-type"].startswith("text/event-stream"))
    check("流式内容完整", "chunk-3" in r.text and "[DONE]" in r.text)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
