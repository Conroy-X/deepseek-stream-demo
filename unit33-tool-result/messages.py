"""Unit 33 · 工具结果回填 —— 结果不是一个返回值，是一条消息。

一次工具调用在 messages 里占**两条**消息：

    {"role": "assistant", "content": null, "tool_calls": [{"id": "call_1", ...}]}
    {"role": "tool", "tool_call_id": "call_1", "content": "{\"city\":\"上海\"}"}

本章的产出是两个纯函数：

    append_tool_results()   把执行结果变成合法消息，一次调用一条，绝不漏
    validate_messages()     反过来检查整个数组，指出「哪一条缺了」

第二个比第一个值钱 —— 回填写一次就对了，但线上 400 全是它抓出来的。
"""

from __future__ import annotations

import json
from typing import Any

MAX_TOOL_CONTENT = 1200  # 字符数上限，超了就换成「截断信封」
MESSAGE_OVERHEAD = 4  # 每条消息的固定开销（role 等），粗估用


# ---------------------------------------------------------------------------
# 一、序列化：content 必须是字符串
# ---------------------------------------------------------------------------
def to_content(payload: Any, limit: int = MAX_TOOL_CONTENT) -> tuple[str, bool]:
    """把任意结果变成 tool 消息的 content。返回 (content, 是否被截断)。

    截断不是简单切片 —— 切一半的 JSON 会变成非法 JSON，模型读到的东西不可解析。
    正确做法是包一个「信封」：结构完整，原文进 preview。
    """
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    if len(text) <= limit:
        return text, False
    envelope = {"truncated": True, "original_chars": len(text), "preview": text[:limit]}
    return json.dumps(envelope, ensure_ascii=False), True


def error_result(kind: str, message: str) -> dict:
    """错误也是一种结果。形状必须统一，否则模型要猜。"""
    return {"ok": False, "error": {"type": kind, "message": message}}


def ok_result(data: Any) -> dict:
    return {"ok": True, "data": data}


def tool_message(call_id: str, name: str, payload: Any, limit: int = MAX_TOOL_CONTENT) -> dict:
    content, truncated = to_content(payload, limit)
    msg = {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}
    if truncated:
        msg["truncated"] = True  # 只给前端看，发请求前要剔掉
    return msg


def strip_local_fields(msg: dict) -> dict:
    """truncated 这种字段是自己的标记，真发给模型会被某些 SDK 拒掉。"""
    return {k: v for k, v in msg.items() if k != "truncated"}


# ---------------------------------------------------------------------------
# 二、回填：一次调用一条消息，id 必须对上
# ---------------------------------------------------------------------------
def append_tool_results(messages: list[dict], assistant: dict, results: list[dict]) -> list[dict]:
    """把 results 按 tool_calls 的顺序回填，返回新的 messages（不改原列表）。

    results 的每一项：{"call_id": ..., "name": ..., "payload": ...}
    缺一条就抛 ValueError —— 这是编程错误，不该静默发出去。
    """
    calls = assistant.get("tool_calls") or []
    if not calls:
        raise ValueError("assistant 消息里没有 tool_calls，没什么可回填的")

    by_id = {r["call_id"]: r for r in results}
    missing = [c["id"] for c in calls if c["id"] not in by_id]
    if missing:
        raise ValueError(f"缺少 {len(missing)} 条工具结果：{missing}")
    extra = [k for k in by_id if k not in {c["id"] for c in calls}]
    if extra:
        raise ValueError(f"多出 {len(extra)} 条不属于本次调用的结果：{extra}")

    out = list(messages)
    out.append({"role": "assistant", "content": assistant.get("content"), "tool_calls": calls})
    # 按 calls 的顺序回填，而不是按结果的到达顺序 —— 并行执行时顺序是随机的
    for call in calls:
        r = by_id[call["id"]]
        out.append(tool_message(call["id"], r.get("name") or call["function"]["name"], r["payload"]))
    return out


# ---------------------------------------------------------------------------
# 三、校验器：本章最值钱的东西
# ---------------------------------------------------------------------------
ROLES = {"system", "user", "assistant", "tool"}


def validate_messages(messages: list[dict]) -> list[dict]:
    """检查整个数组的合法性，返回问题列表（空列表 = 合法）。

    每条问题带 index，能直接指到出问题的那一行。检查四件事：
        1. role 合法，tool 消息的 content 是字符串
        2. 每条 tool 消息都能找到前面**未被应答**的同 id tool_call
        3. 每个 tool_call 都有且只有一条 tool 消息应答
        4. tool 消息的 name 和 tool_call 的函数名一致
    """
    problems: list[dict] = []

    def add(index: int, code: str, msg: str) -> None:
        problems.append({"index": index, "code": code, "msg": msg})

    # pending: call_id -> (声明它的 assistant 下标, 函数名)
    pending: dict[str, tuple[int, str]] = {}
    seen_ids: set[str] = set()

    for i, m in enumerate(messages):
        role = m.get("role")
        if role not in ROLES:
            add(i, "unknown_role", f"role={role!r} 不在 {sorted(ROLES)} 里")
            continue

        if role == "tool":
            cid = m.get("tool_call_id")
            if not isinstance(m.get("content"), str):
                add(i, "content_not_str", f"content 是 {type(m.get('content')).__name__}，必须是 str")
            if not m.get("content"):
                add(i, "empty_content", "content 是空字符串，模型会以为工具什么都没返回")
            if not cid:
                add(i, "orphan_tool", "tool 消息没有 tool_call_id")
            elif cid not in pending:
                if cid in seen_ids:
                    add(i, "duplicate_tool_result", f"tool_call_id={cid} 被回填了两次")
                else:
                    add(i, "orphan_tool", f"tool_call_id={cid} 找不到前面声明它的 assistant")
            else:
                _, fn_name = pending.pop(cid)
                if m.get("name") and m["name"] != fn_name:
                    add(i, "name_mismatch", f"name={m['name']} 与 tool_call 的 {fn_name} 不一致")
            seen_ids.add(cid or "")
            continue

        # 非 tool 消息出现时，前面所有 tool_call 都必须已被应答完
        if pending:
            for cid, (idx, fn_name) in pending.items():
                add(idx, "missing_tool_result", f"第 {idx} 条声明的 {fn_name}({cid}) 没有对应的 tool 消息")
            pending.clear()

        for call in m.get("tool_calls") or []:
            cid = call.get("id")
            fn = call.get("function") or {}
            if not cid:
                add(i, "tool_calls_bad_shape", "tool_calls 里的调用没有 id")
                continue
            if call.get("type") != "function":
                add(i, "tool_calls_bad_shape", f"{cid} 的 type 是 {call.get('type')!r}，应为 'function'")
            if not fn.get("name"):
                add(i, "tool_calls_bad_shape", f"{cid} 没有 function.name")
                continue
            if not isinstance(fn.get("arguments", ""), str):
                add(i, "tool_calls_bad_shape", f"{cid} 的 arguments 是对象，必须是 JSON 字符串")
            if cid in pending:
                add(i, "tool_calls_bad_shape", f"tool_call id {cid} 重复")
            pending[cid] = (i, fn.get("name"))

    for cid, (idx, fn_name) in pending.items():
        add(idx, "missing_tool_result", f"第 {idx} 条声明的 {fn_name}({cid}) 在结尾也没有被应答")

    return problems


def summarise_problems(problems: list[dict]) -> str:
    if not problems:
        return "消息数组合法"
    return "；".join(f"[{p['index']}] {p['code']}: {p['msg']}" for p in problems[:4]) + ("…" if len(problems) > 4 else "")


# ---------------------------------------------------------------------------
# 四、token 粗估：中文 1 字 ≈ 1 token，ASCII 4 字符 ≈ 1 token
# ---------------------------------------------------------------------------
def est_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk + (len(text) - cjk + 3) // 4


def count_tokens(messages: list[dict]) -> dict:
    total = MESSAGE_OVERHEAD * len(messages)
    tool_tokens = 0
    for m in messages:
        part = est_tokens(m.get("content") or "")
        total += part
        if m.get("role") == "tool":
            tool_tokens += part
        for call in m.get("tool_calls") or []:
            total += est_tokens(call["function"].get("arguments") or "")
    return {"messages": len(messages), "total": total, "tool_tokens": tool_tokens}
