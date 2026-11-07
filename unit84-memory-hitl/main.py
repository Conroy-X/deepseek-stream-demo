"""Unit 84 · Memory / HITL：长期记忆 + 人工审批门。

两个进阶能力：
  1. Memory：跨会话记住用户信息（带 TTL），下次不用重复问
  2. HITL（Human-in-the-loop）：危险操作必须人工点确认——
     这是 AI 应用的责任边界，不是可选项

启动：uv run uvicorn main:app --port 8084
自检：uv run python main.py
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

# ---------------------------------------------------------------------------
# 记忆：带 TTL 的键值存储（生产换 Redis / pgvector 长期记忆）
# ---------------------------------------------------------------------------
@dataclass
class Memory:
    default_ttl: int = 3600
    _data: dict = field(default_factory=dict)   # key -> (value, expire_at)

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        if ttl is not None and ttl <= 0:
            raise ValueError("ttl 必须 > 0")
        expire = time.monotonic() + (ttl if ttl is not None else self.default_ttl)
        self._data[key] = (value, expire)

    def get(self, key: str) -> str | None:
        item = self._data.get(key)
        if not item:
            return None
        value, expire = item
        if time.monotonic() >= expire:
            del self._data[key]
            return None
        return value

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def keys(self) -> list[str]:
        # 先清理过期，再返回
        now = time.monotonic()
        for k in [k for k, (_, e) in self._data.items() if e <= now]:
            self._data.pop(k, None)
        return sorted(self._data)


# ---------------------------------------------------------------------------
# HITL：危险操作审批门
# ---------------------------------------------------------------------------
DANGEROUS_TOOLS = {"refund", "delete_user", "drop_table", "transfer_money"}


@dataclass
class HitlGate:
    approved: set = field(default_factory=set)       # (tool, user)
    pending: list = field(default_factory=list)      # 待审批请求

    def needs_approval(self, tool: str) -> bool:
        return tool in DANGEROUS_TOOLS

    def request(self, tool: str, user: str, args: dict | None = None) -> dict:
        """发起审批请求，返回 pending 记录。"""
        rid = f"{tool}:{user}"
        if (tool, user) in self.approved:
            return {"status": "already_approved", "id": rid}
        if not any(p["id"] == rid for p in self.pending):
            self.pending.append({"id": rid, "tool": tool, "user": user, "args": args or {}})
        return {"status": "pending", "id": rid}

    def approve(self, tool: str, user: str, approver: str) -> dict:
        """审批通过。approver 记录「谁批的」——责任可追溯。"""
        if approver == user:
            # 不能自己批自己：这是最容易被忽略的合规点
            return {"ok": False, "error": "不能自己审批自己的请求"}
        self.approved.add((tool, user))
        self.pending = [p for p in self.pending if p["id"] != f"{tool}:{user}"]
        return {"ok": True, "tool": tool, "user": user, "approver": approver}

    def is_approved(self, tool: str, user: str) -> bool:
        return (tool, user) in self.approved

    def execute(self, tool: str, user: str) -> dict:
        """执行门：危险操作必须已审批。"""
        if self.needs_approval(tool) and not self.is_approved(tool, user):
            return {"ok": False, "status": 403, "error": f"{tool} 需要人工审批"}
        return {"ok": True, "result": f"{tool} 已执行（user={user}）"}


memory = Memory()
gate = HitlGate()

app = FastAPI(title="Unit 84 · Memory/HITL", version="0.1.0")


# ---- Memory 接口 ----
@app.put("/memory/{key}", summary="写入记忆（可指定 ttl）")
async def mem_set(key: str, value: str, ttl: int | None = None) -> dict:
    try:
        memory.set(key, value, ttl)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "key": key}


@app.get("/memory/{key}", summary="读取记忆（过期返回 404）")
async def mem_get(key: str) -> dict:
    v = memory.get(key)
    if v is None:
        raise HTTPException(status_code=404, detail=f"记忆 {key} 不存在或已过期")
    return {"key": key, "value": v}


@app.get("/memory", summary="列出所有记忆键")
async def mem_list() -> dict:
    return {"keys": memory.keys()}


# ---- HITL 接口 ----
@app.post("/hitl/request", summary="发起危险操作审批")
async def hitl_request(tool: str, user: str) -> dict:
    return gate.request(tool, user)


@app.post("/hitl/approve", summary="审批（不能自己批自己）")
async def hitl_approve(tool: str, user: str, approver: str) -> dict:
    out = gate.approve(tool, user, approver)
    if not out["ok"]:
        raise HTTPException(status_code=400, detail=out["error"])
    return out


@app.get("/hitl/pending", summary="待审批列表")
async def hitl_pending() -> dict:
    return {"pending": gate.pending}


@app.post("/tool/{tool}", summary="执行工具：危险操作需已审批")
async def tool_exec(tool: str, user: str) -> dict:
    out = gate.execute(tool, user)
    if not out["ok"]:
        raise HTTPException(status_code=out["status"], detail=out["error"])
    return out


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({
        "hint": "记忆：PUT/GET /memory/{key}；审批：POST /hitl/request → /hitl/approve → POST /tool/{tool}"
    })


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

    print("Unit 84 · Memory/HITL 自检\n")

    # ---- Memory ----
    m = Memory()
    m.set("user:rik:city", "上海")
    check("记忆写入读取", m.get("user:rik:city") == "上海")
    check("未写入返回 None", m.get("nope") is None)
    m.set("short", "v", ttl=1)
    time.sleep(1.1)
    check("TTL 过期后读取为 None", m.get("short") is None)
    check("过期键不在 keys 里", "short" not in m.keys())
    m.delete("user:rik:city")
    check("删除后读不到", m.get("user:rik:city") is None)
    try:
        m.set("bad", "v", ttl=0)
        check("ttl=0 抛错", False)
    except ValueError:
        check("ttl=0 抛错", True)
    # 结构化记忆（真实场景存 JSON）
    m.set("profile", json.dumps({"city": "上海", "lang": "zh"}, ensure_ascii=False))
    check("JSON 记忆可往返", json.loads(m.get("profile") or "{}")["city"] == "上海")

    # ---- HITL ----
    g = HitlGate()
    check("退款需要审批", g.needs_approval("refund") is True)
    check("查订单不需要审批", g.needs_approval("lookup_order") is False)

    # 未审批 → 403
    r = g.execute("refund", "rik")
    check("未审批执行被拒", r["ok"] is False and r["status"] == 403)

    # 发起 → 待审批里有
    g.request("refund", "rik")
    check("请求进入待审批", any(p["id"] == "refund:rik" for p in g.pending))

    # 自己批自己 → 拒绝
    r = g.approve("refund", "rik", "rik")
    check("不能自己批自己", r["ok"] is False)

    # 他人审批 → 通过
    r = g.approve("refund", "rik", "manager")
    check("他人审批通过", r["ok"] is True and r["approver"] == "manager")
    check("审批后待办清空", not any(p["id"] == "refund:rik" for p in g.pending))
    check("审批标记生效", g.is_approved("refund", "rik") is True)

    # 已审批 → 可执行
    r = g.execute("refund", "rik")
    check("审批后可执行", r["ok"] is True)

    # 审批只对本人有效
    r = g.execute("refund", "alice")
    check("审批只对本人有效", r["ok"] is False)

    # 非危险工具直接放行
    check("查订单直接放行", g.execute("lookup_order", "anyone")["ok"] is True)

    # ---- HTTP ----
    c = TestClient(app)
    check("HTTP 写记忆 200", c.put("/memory/t1", params={"value": "v1"}).status_code == 200)
    check("HTTP 读记忆", c.get("/memory/t1").json()["value"] == "v1")
    check("HTTP 读不存在 404", c.get("/memory/none").status_code == 404)
    check("HTTP 列记忆键", "t1" in c.get("/memory").json()["keys"])
    check("HTTP 发起审批", c.post("/hitl/request", params={"tool": "refund2", "user": "u1"}).json()["status"] == "pending")
    check("HTTP 自批被拒 400",
          c.post("/hitl/approve", params={"tool": "refund2", "user": "u1", "approver": "u1"}).status_code == 400)
    check("HTTP 他人批准 200",
          c.post("/hitl/approve", params={"tool": "refund2", "user": "u1", "approver": "boss"}).status_code == 200)
    check("HTTP 审批后执行 200", c.post("/tool/refund2", params={"user": "u1"}).status_code == 200)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())