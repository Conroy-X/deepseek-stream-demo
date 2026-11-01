"""Unit 32 · 参数 Schema —— 一份契约，两个读者。

模型读 `description` 决定传什么；代码读同一份 schema 决定收不收。
所以这两个东西**必须同源**：从 Pydantic 模型生成。

    SearchOrdersArgs / ScheduleMeetingArgs / RefundOrderArgs   ← 唯一真源
        ├─ to_openai_tools()  → 给模型看的 tools 数组（description 是主角）
        └─ validate()         → 给代码用的校验器（model_validate）

本文件同时留了一份**手写 schema + 手写 if/else 校验器**（LEGACY_*），
它不是反面教材的摆设：自检会用 `schema_diff()` 证明两边已经漂移，
并且漂移的方向是「同一份输入，两边结论相反」。
"""

from __future__ import annotations

import copy
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

OrderStatus = Literal["pending", "paid", "shipped", "refunded"]
MeetingRoomBuilding = Literal["A 座", "B 座", "C 座"]


# ---------------------------------------------------------------------------
# 三个参数模型。description 一律写成「模型看得懂的自然语言」，
# 不写「string, required」这种给编译器看的话 —— 那是 schema 自己会表达的。
# ---------------------------------------------------------------------------
class SearchOrdersArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(
        min_length=1,
        max_length=40,
        description="订单号、收货人或商品名里的关键词。例如「SO-000123」「耳机」「张三」。",
    )
    status: OrderStatus = Field(
        default="pending",
        description="只看某个状态的订单。pending=待付款，paid=已付款，shipped=已发货，refunded=已退款。不传默认 pending。",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="最多返回几条订单，默认 10，上限 50。",
    )


class MeetingRoom(BaseModel):
    model_config = ConfigDict(extra="forbid")

    building: MeetingRoomBuilding = Field(description="楼名，只能是 A 座 / B 座 / C 座。")
    floor: int = Field(ge=1, le=30, description="楼层，1 到 30。")


class ScheduleMeetingArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=40, description="会议主题，2 到 40 个字。")
    start: str = Field(
        pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$",
        description="开始时间，必须是 2026-11-03T14:30 这种形式（ISO 本地时间，不带时区）。",
    )
    duration_min: int = Field(default=30, ge=15, le=180, description="会议时长（分钟），默认 30。")
    attendees: list[str] = Field(
        min_length=1,
        max_length=10,
        description="参会人邮箱列表，至少 1 个，最多 10 个。",
    )
    room: MeetingRoom = Field(description="会议室，包含楼名和楼层两个字段。")


class RefundOrderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(
        pattern=r"^SO-\d{6}$",
        description="订单号，格式必须是 SO- 加 6 位数字，例如 SO-000123。",
    )
    amount: float = Field(gt=0, le=100000, description="退款金额（元），必须大于 0。")
    reason: Literal["质量问题", "七天无理由", "发错货", "其他"] = Field(
        description="退款原因，只能从这四个里选一个，不要自己编。"
    )
    note: str | None = Field(
        default=None,
        max_length=100,
        description="补充说明，可选，最多 100 字。",
    )


_ORDERS = [
    {"order_id": "SO-000123", "buyer": "张三", "item": "降噪耳机", "status": "shipped", "amount": 899.0},
    {"order_id": "SO-000124", "buyer": "李四", "item": "机械键盘", "status": "paid", "amount": 459.0},
    {"order_id": "SO-000125", "buyer": "张三", "item": "降噪耳机", "status": "refunded", "amount": 899.0},
]


def _search_orders(keyword: str, status: str = "pending", limit: int = 10) -> dict:
    hits = [o for o in _ORDERS if keyword in o["order_id"] or keyword in o["buyer"] or keyword in o["item"]]
    hits = [o for o in hits if o["status"] == status]
    return {"count": len(hits), "items": hits[:limit]}


def _schedule_meeting(title: str, start: str, room: dict, attendees: list[str], duration_min: int = 30) -> dict:
    return {"title": title, "start": start, "room": room, "attendees": attendees, "duration_min": duration_min}


def _refund_order(order_id: str, amount: float, reason: str, note: str | None = None) -> dict:
    return {"order_id": order_id, "refunded": amount, "reason": reason, "note": note}


# 工具名 → (参数模型, 写给模型看的工具说明, 执行函数)
TOOL_SPECS: dict[str, tuple[type[BaseModel], str, Any]] = {
    "search_orders": (
        SearchOrdersArgs,
        "按关键词搜索订单。只能按状态筛，不能按金额区间筛。想查某个已知订单号时也用这个工具。",
        _search_orders,
    ),
    "schedule_meeting": (
        ScheduleMeetingArgs,
        "在指定会议室安排一场会议。需要会议主题、开始时间、参会人和会议室。",
        _schedule_meeting,
    ),
    "refund_order": (
        RefundOrderArgs,
        "对已付款订单发起退款。金额不能超过订单金额，原因只能从枚举里选。",
        _refund_order,
    ),
}

TOOL_NAMES = tuple(TOOL_SPECS)


# ---------------------------------------------------------------------------
# 一、摊平：$defs + $ref → 模型能直接读的嵌套对象
# ---------------------------------------------------------------------------
def _inline(node: Any, defs: dict, seen: tuple[str, ...] = ()) -> Any:
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref[len("#/$defs/") :]
            if name in seen:  # 循环引用：摊平会无限展开，只能报错
                raise ValueError(f"schema 存在循环引用：{' → '.join(seen + (name,))}")
            target = _inline(copy.deepcopy(defs[name]), defs, seen + (name,))
            # $ref 旁边的 description 要保留（Pydantic 会把字段注释挂在这里）
            extra = {k: v for k, v in node.items() if k != "$ref"}
            if extra:
                target = {**target, **extra}
            return target
        return {k: _inline(v, defs, seen) for k, v in node.items() if k != "title"}
    if isinstance(node, list):
        return [_inline(x, defs, seen) for x in node]
    return node


def flatten_schema(schema: dict) -> dict:
    """把 Pydantic 的 $defs/$ref 摊平成内联对象，并去掉 title。"""
    defs = schema.get("$defs") or {}
    out = _inline({k: v for k, v in schema.items() if k != "$defs"}, defs)
    return out


# ---------------------------------------------------------------------------
# 二、给模型的 tools 数组
# ---------------------------------------------------------------------------
def build_tool(name: str) -> dict:
    model, desc, _ = TOOL_SPECS[name]
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": flatten_schema(model.model_json_schema()),
        },
    }


def to_openai_tools() -> list[dict]:
    return [build_tool(n) for n in TOOL_NAMES]


# ---------------------------------------------------------------------------
# 三、给代码的校验器：同一个模型，输出可定位的错误路径
# ---------------------------------------------------------------------------
def validate(tool_name: str, raw: Any) -> dict:
    """返回 {ok, args?, errors?}。errors 里每条都带 path，能直接指给模型看。"""
    if tool_name not in TOOL_SPECS:
        return {"ok": False, "errors": [{"path": "", "type": "unknown_tool", "msg": f"未知工具 {tool_name}"}]}
    if not isinstance(raw, dict):
        return {"ok": False, "errors": [{"path": "", "type": "type_error", "msg": "arguments 必须是 JSON 对象"}]}

    model, _, _ = TOOL_SPECS[tool_name]
    try:
        obj = model.model_validate(raw)
    except ValidationError as e:
        return {"ok": False, "errors": [{"path": _path(err["loc"]), "type": err["type"], "msg": err["msg"]} for err in e.errors()]}
    # exclude_unset：默认值不要混进「模型实际传了什么」里，否则日志会骗人
    return {"ok": True, "args": obj.model_dump(exclude_unset=True)}


def _path(loc: tuple) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


# ---------------------------------------------------------------------------
# 四、反面：手写 schema + 手写 if/else，两套东西各写一遍
# ---------------------------------------------------------------------------
LEGACY_SCHEMA = {
    "type": "object",
    "properties": {
        "keyword": {"type": "string", "description": "关键词"},
        "status": {"type": "string", "enum": ["pending", "paid", "shipped"], "description": "订单状态"},
        "limit": {"type": "integer", "description": "条数"},
    },
    # 漏了两件事：limit 有默认值不该是必填；refunded 是后加的状态，枚举没跟上
    "required": ["keyword", "limit"],
}


class LegacyError(Exception):
    pass


def legacy_validate(raw: dict) -> dict:
    if "keyword" not in raw:
        raise LegacyError("缺少必填参数：keyword")
    if "limit" not in raw:
        raise LegacyError("缺少必填参数：limit")
    status = raw.get("status")
    if status is not None and status not in LEGACY_SCHEMA["properties"]["status"]["enum"]:
        raise LegacyError(f"参数 status 只能取 {LEGACY_SCHEMA['properties']['status']['enum']}")
    return raw  # 多余的字段直接放过


def schema_diff(a: Any, b: Any, path: str = "") -> list[str]:
    """递归比较两份 schema，返回所有差异路径。路径形如 properties.status.enum。"""
    if isinstance(a, dict) and isinstance(b, dict):
        out: list[str] = []
        for k in sorted(set(a) | set(b)):
            sub = f"{path}.{k}" if path else k
            if k not in a:
                out.append(f"{sub}: 只在第二份里")
            elif k not in b:
                out.append(f"{sub}: 只在第一份里")
            else:
                out.extend(schema_diff(a[k], b[k], sub))
        return out
    if a != b:
        return [f"{path}: {json.dumps(a, ensure_ascii=False)} ≠ {json.dumps(b, ensure_ascii=False)}"]
    return []


# ---------------------------------------------------------------------------
# 五、执行（只是让链路能跑通，不是本单元重点）
# ---------------------------------------------------------------------------
def execute(tool_name: str, raw: Any) -> dict:
    """校验 → 执行。校验失败不抛异常，返回一条结构化错误（Unit 33 会把这条变成消息）。"""
    v = validate(tool_name, raw)
    if not v["ok"]:
        return {"ok": False, "content": json.dumps({"error": v["errors"]}, ensure_ascii=False), "errors": v["errors"]}
    fn = TOOL_SPECS[tool_name][2]
    data = fn(**v["args"])
    return {"ok": True, "content": json.dumps(data, ensure_ascii=False), "data": data}
