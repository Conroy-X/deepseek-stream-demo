"""Unit 69 · 数据泄露与工具权限 —— FastAPI 入口。

启动：
    uv run uvicorn main:app --reload --port 8069

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from security import (
    DataStore,
    ToolPermissionError,
    contains_sensitive,
    execute_tool,
    redact,
)

app = FastAPI(title="Unit 69 · AI Security: 数据泄露与工具权限", version="0.1.0")

_store = DataStore()


@app.get("/ask", summary="输出过滤：答案里的敏感信息会被脱敏")
async def ask(q: str = Query(min_length=1, max_length=200)) -> dict:
    # 假 LLM：直接回显用户输入 + 一段可能含敏感信息的文本
    raw_answer = f"你问的是「{q}」。客服邮箱 support@corp.test，热线 13812345678。"  # pii-check: allow
    if contains_sensitive(raw_answer):
        return {"filtered": True, "answer": redact(raw_answer)}
    return {"filtered": False, "answer": raw_answer}


@app.get("/data", summary="数据访问隔离：只能读自己的数据")
async def get_data(user_id: str, key: str) -> dict:
    try:
        return {"data": _store.get(user_id, key)}
    except PermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)


@app.get("/tool", summary="工具权限：角色无权限则 403")
async def tool(role: str, name: str, order_id: str = "order_1") -> dict:
    try:
        result = execute_tool(role, name, {"store": _store, "user_id": role, "order_id": order_id})
        return {"result": result}
    except ToolPermissionError as e:
        return JSONResponse({"error": str(e)}, status_code=403)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/ask 看输出脱敏；/data?user_id=alice&key=order_1 看隔离；/tool?role=user&name=refund 看越权"})


if __name__ == "__main__":
    sys.exit(__import__("security").run_selfcheck())
