"""Unit 79 · 工程化收尾：权限、日志、错误页、成本面板。

计划主题：把项目从「能演示」打磨成「能交付」——这一单元收四个横切面：

  1. 权限：接口级 RBAC（和 Unit 67 同一套逻辑，落到路由上）
  2. 日志：request_id + 耗时 + token 用量（成本的可观测基础）
  3. 错误页：统一 JSON 错误格式，不让内部堆栈泄漏给用户
  4. 成本面板：把 token 换算成钱，给运维一个实时视角

启动：uv run uvicorn main:app --port 8079
自检：uv run python main.py（离线）
"""

from __future__ import annotations

import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

# 单价（每百万 token），演示值
PRICE_IN = 1.0
PRICE_OUT = 2.0

ROLES: dict[str, set[str]] = {
    "admin": {"chat", "cost", "admin"},
    "user": {"chat"},
    "guest": set(),
}


@dataclass
class CostTracker:
    """把每次调用的 token 换算成钱，供 /cost/panel 展示。"""
    total_in: int = 0
    total_out: int = 0
    calls: int = 0

    def record(self, tokens_in: int, tokens_out: int) -> None:
        self.total_in += tokens_in
        self.total_out += tokens_out
        self.calls += 1

    def cost(self) -> float:
        return self.total_in / 1_000_000 * PRICE_IN + self.total_out / 1_000_000 * PRICE_OUT

    def panel(self) -> dict:
        return {
            "calls": self.calls,
            "tokens_in": self.total_in,
            "tokens_out": self.total_out,
            "cost": round(self.cost(), 6),
        }


@dataclass
class AppState:
    cost: CostTracker = field(default_factory=CostTracker)
    logs: list = field(default_factory=list)


state = AppState()
app = FastAPI(title="Unit 79 · 工程化收尾", version="0.1.0")


def _role(request: Request) -> str:
    return request.headers.get("X-Role", "guest")


def _require(request: Request, perm: str) -> str:
    role = _role(request)
    if perm not in ROLES.get(role, set()):
        raise HTTPException(status_code=403, detail=f"角色 {role} 无权限 {perm}")
    return role


# ---------------------------------------------------------------------------
# 日志中间件：request_id + 耗时
# ---------------------------------------------------------------------------
@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    rid = uuid.uuid4().hex[:8]
    _request_id.set(rid)
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = round((time.perf_counter() - t0) * 1000, 2)
    state.logs.append({"request_id": rid, "path": request.url.path,
                       "status": response.status_code, "elapsed_ms": elapsed})
    response.headers["X-Request-Id"] = rid
    return response


# ---------------------------------------------------------------------------
# 统一错误页
# ---------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        {"ok": False, "error": exc.detail, "request_id": _request_id.get()},
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    # 不把堆栈吐给用户，只记日志
    state.logs.append({"request_id": _request_id.get(), "path": request.url.path,
                       "status": 500, "error": type(exc).__name__})
    return JSONResponse(
        {"ok": False, "error": "服务内部错误", "request_id": _request_id.get()},
        status_code=500,
    )


# ---------------------------------------------------------------------------
# 业务接口
# ---------------------------------------------------------------------------
@app.get("/chat", summary="需要 chat 权限；记录 token 成本")
async def chat(request: Request, q: str = "hi") -> dict:
    _require(request, "chat")
    tokens_in = max(1, len(q))
    tokens_out = max(1, len(q) * 2)
    state.cost.record(tokens_in, tokens_out)
    return {"ok": True, "answer": f"回答:{q}", "request_id": _request_id.get()}


@app.get("/cost/panel", summary="成本面板（需 cost 权限）")
async def cost_panel(request: Request) -> dict:
    _require(request, "cost")
    return {"ok": True, "panel": state.cost.panel()}


@app.get("/logs", summary="最近日志（需 admin 权限）")
async def logs(request: Request) -> dict:
    _require(request, "admin")
    return {"ok": True, "logs": state.logs[-20:]}


@app.get("/", include_in_schema=False)
async def index() -> dict:
    return {"hint": "带 X-Role: user/admin 访问 /chat、/cost/panel、/logs"}


# ---------------------------------------------------------------------------
# 独立自检
# ---------------------------------------------------------------------------
def run_selfcheck() -> int:
    from fastapi.testclient import TestClient

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 79 · 工程化收尾 自检\n")
    c = TestClient(app)

    # ---- 权限：guest 无权限 ----
    r = c.get("/chat")
    check("guest 访问 /chat → 403", r.status_code == 403, str(r.status_code))
    r = c.get("/chat", headers={"X-Role": "user"})
    check("user 访问 /chat → 200", r.status_code == 200)
    r = c.get("/cost/panel", headers={"X-Role": "user"})
    check("user 访问成本面板 → 403", r.status_code == 403)
    r = c.get("/cost/panel", headers={"X-Role": "admin"})
    check("admin 访问成本面板 → 200", r.status_code == 200)
    r = c.get("/logs", headers={"X-Role": "admin"})
    check("admin 访问日志 → 200", r.status_code == 200)

    # ---- 统一错误格式 ----
    body = c.get("/chat").json()
    check("错误体有 ok=False", body["ok"] is False)
    check("错误体有 request_id", "request_id" in body)

    # ---- 日志中间件 ----
    r = c.get("/chat", headers={"X-Role": "user"})
    check("响应头有 X-Request-Id", "x-request-id" in {k.lower() for k in r.headers})
    check("日志记录了请求", len(state.logs) > 0)
    last = state.logs[-1]
    check("日志含耗时", "elapsed_ms" in last)

    # ---- 成本面板 ----
    panel = state.cost.panel()
    check("有调用次数", panel["calls"] >= 1)
    check("成本为正", panel["cost"] > 0)
    check("成本 = 入+出分开算", abs(panel["cost"] - (
        panel["tokens_in"] / 1e6 * PRICE_IN + panel["tokens_out"] / 1e6 * PRICE_OUT)) < 1e-9)

    # ---- 未知路由 → 404 统一格式 ----
    r = c.get("/nope")
    check("404 也不崩", r.status_code == 404)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
