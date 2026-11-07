"""Unit 84 · Memory / HITL：记忆持久化 + 人工审批门。

核心认知：Agent 两个进阶能力：
  1. Memory：跨会话记住用户信息（带 TTL），下次不用重复问
  2. HITL（Human-in-the-loop）：危险操作（退款/删除）必须人工点确认，
     不能全自动——这是 AI 应用的责任边界
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field


@dataclass
class Memory:
    ttl: int = 3600
    _data: dict = field(default_factory=dict)  # key -> (value, expire_at)

    def set(self, key: str, value: str, ttl: int | None = None) -> None:
        expire = time.monotonic() + (ttl if ttl is not None else self.ttl)
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


DANGEROUS_TOOLS = {"refund", "delete_user", "drop_table"}


@dataclass
class HITLGate:
    approved: set = field(default_factory=set)

    def require_approval(self, tool: str) -> bool:
        return tool in DANGEROUS_TOOLS

    def approve(self, tool: str, user: str) -> None:
        self.approved.add((tool, user))

    def is_approved(self, tool: str, user: str) -> bool:
        return (tool, user) in self.approved


def run_selfcheck() -> int:
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
    # Memory
    mem = Memory()
    mem.set("user:rik:city", "上海")
    check("记忆写入读取", mem.get("user:rik:city") == "上海")
    mem.set("short", "v", ttl=1)
    time.sleep(1.1)
    check("TTL 过期", mem.get("short") is None)
    check("未写入返回 None", mem.get("nope") is None)
    # 序列化往返（真实存 Redis/DB）
    payload = json.dumps({"k": "v"})
    mem.set("json", payload)
    check("JSON 记忆可往返", json.loads(mem.get("json")) == {"k": "v"})

    # HITL
    gate = HITLGate()
    check("退款需要审批", gate.require_approval("refund") is True)
    check("查订单不需要审批", gate.require_approval("lookup_order") is False)
    check("未审批不可执行", not gate.is_approved("refund", "rik"))
    gate.approve("refund", "rik")
    check("审批后可执行", gate.is_approved("refund", "rik"))
    check("审批只对本人有效", not gate.is_approved("refund", "alice"))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(run_selfcheck())
