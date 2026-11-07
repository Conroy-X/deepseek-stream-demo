"""Unit 67 · Auth —— FastAPI 入口：认证 + 授权分开，401 与 403 分清楚。

启动：
    uv run uvicorn main:app --reload --port 8067

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from auth import AuthError, ForbiddenError, authenticate, authorize, create_token

app = FastAPI(title="Unit 67 · Auth", version="0.1.0")

# 演示密钥；生产从 .env 读且定期轮换
SECRET = os.getenv("AUTH_SECRET", "unit67-demo-secret")

# 不同角色的额度（RBAC 之外再加一层资源限制）
QUOTA = {"admin": 100, "user": 10, "guest": 2}


@app.post("/login", summary="签发 token（演示用，生产要走用户名密码校验）")
async def login(username: str = "rik", role: str = "user") -> dict:
    if role not in QUOTA:
        raise HTTPException(status_code=400, detail=f"未知角色 {role}")
    token = create_token({"sub": username, "role": role}, SECRET, ttl=3600)
    return {"token": token, "role": role, "quota": QUOTA[role]}


@app.get("/ask", summary="需要认证（401）且角色有 ask 权限（403）")
async def ask(authorization: str | None = Header(default=None)) -> dict:
    try:
        payload = authenticate(authorization, SECRET)      # 第一步：认证
        role = authorize(payload, "ask")                   # 第二步：授权
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e), headers={"WWW-Authenticate": "Bearer"})
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"answer": "回答：你好", "role": role, "quota": QUOTA[role]}


@app.get("/admin/set-quota", summary="需要 admin 权限（403 场景）")
async def set_quota(authorization: str | None = Header(default=None)) -> dict:
    try:
        payload = authenticate(authorization, SECRET)
        role = authorize(payload, "set_quota")
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e), headers={"WWW-Authenticate": "Bearer"})
    except ForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e))
    return {"ok": True, "role": role}


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({
        "hint": "先 POST /login 拿 token，再带 Authorization: Bearer <token> 访问 /ask 和 /admin/set-quota"
    })


if __name__ == "__main__":
    sys.exit(__import__("auth").run_selfcheck())
