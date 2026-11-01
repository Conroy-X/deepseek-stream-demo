"""Unit 59 · MCP 工程化：日志、超时、并发、错误码。

四块都要实测：
  1. 结构化日志：JSON 格式 + 字段白名单 + 敏感字段剔除
  2. 超时：asyncio.wait_for，超时转 is_error 不崩
  3. 并发：5 个并发调用，trace_id 各不相同
  4. 错误码：E_SQL_SYNTAX / E_SQL_FORBIDDEN / E_TOOL_TIMEOUT / E_TOOL_ERROR / E_UPSTREAM
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────────────────
# 1. 结构化日志
# ─────────────────────────────────────────────────────────────────────────

#: 日志字段白名单。不在表里的字段一律剔除。
LOG_FIELDS = {"ts", "level", "event", "tool", "duration_ms", "ok", "trace_id", "rounds", "error_code"}

#: 敏感关键词：出现在值里的就打码
SENSITIVE_PATTERNS = [
    (re.compile(r"(token|password|secret|api_key|bearer)\s*[:=]\s*\S+", re.IGNORECASE), "***REDACTED***"),
    (re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"), "***CARD***"),  # 信用卡号
]


@dataclass
class Logger:
    logs: list[dict[str, Any]] = field(default_factory=list)
    dropped_fields: list[str] = field(default_factory=list)
    redacted_count: int = 0

    def log(self, **fields: Any) -> dict[str, Any]:
        """写一条结构化日志。只保留白名单字段，敏感值打码。"""
        entry: dict[str, Any] = {}
        dropped: list[str] = []
        for k, v in fields.items():
            if k not in LOG_FIELDS:
                dropped.append(k)
                continue
            if isinstance(v, str):
                redacted = v
                for pattern, replacement in SENSITIVE_PATTERNS:
                    new = pattern.sub(replacement, redacted)
                    if new != redacted:
                        self.redacted_count += 1
                    redacted = new
                entry[k] = redacted
            else:
                entry[k] = v
        self.logs.append(entry)
        self.dropped_fields.extend(dropped)
        return entry

    def dump(self) -> str:
        return "\n".join(json.dumps(l, ensure_ascii=False) for l in self.logs)


# ─────────────────────────────────────────────────────────────────────────
# 4. 错误码
# ─────────────────────────────────────────────────────────────────────────

ERROR_CODES = {
    "E_SQL_SYNTAX": "SQL 语法错",
    "E_SQL_FORBIDDEN": "SQL 被安全闸拦（多语句/写操作/PRAGMA）",
    "E_TOOL_TIMEOUT": "工具执行超时",
    "E_TOOL_ERROR": "工具内部异常",
    "E_UPSTREAM": "上游服务不可用",
}


def error_code_for(error: str) -> str:
    """根据错误文本返回稳定的错误码。同一种失败总是同一个码。"""
    if "syntax error" in error.lower() or "near" in error.lower():
        return "E_SQL_SYNTAX"
    if "多语句" in error or "只允许 SELECT" in error or "readonly" in error.lower():
        return "E_SQL_FORBIDDEN"
    if "timeout" in error.lower() or "超时" in error:
        return "E_TOOL_TIMEOUT"
    if "Error executing tool" in error:
        return "E_TOOL_ERROR"
    return "E_UPSTREAM"


def public_response(error_code: str, error: str) -> dict[str, Any]:
    """对外返回：错误码 + 简要说明，不含 traceback。"""
    return {
        "error_code": error_code,
        "message": ERROR_CODES.get(error_code, "未知错误"),
        # 故意不返回 error 原文（可能含 traceback / 实现细节）
    }


# ─────────────────────────────────────────────────────────────────────────
# 2. 超时
# ─────────────────────────────────────────────────────────────────────────


async def call_with_timeout(func: Any, args: dict[str, Any], timeout: float, logger: Logger, trace_id: str) -> dict[str, Any]:
    """带超时的工具调用。超时转 is_error，不崩。"""
    start = time.monotonic()
    try:
        result = await asyncio.wait_for(func(**args), timeout=timeout)
        duration = (time.monotonic() - start) * 1000
        logger.log(ts=time.time(), level="INFO", event="tool_call", tool=getattr(func, "__name__", "?"),
                   duration_ms=round(duration, 1), ok=True, trace_id=trace_id)
        return {"ok": True, "result": result, "duration_ms": round(duration, 1)}
    except asyncio.TimeoutError:
        duration = (time.monotonic() - start) * 1000
        logger.log(ts=time.time(), level="WARN", event="tool_timeout", tool=getattr(func, "__name__", "?"),
                   duration_ms=round(duration, 1), ok=False, trace_id=trace_id, error_code="E_TOOL_TIMEOUT")
        return {"ok": False, "error": "工具执行超时", "error_code": "E_TOOL_TIMEOUT", "duration_ms": round(duration, 1)}
    except Exception as e:
        duration = (time.monotonic() - start) * 1000
        error_text = str(e)
        # 工具内部抛的异常（非超时、非上游连接）归 E_TOOL_ERROR
        if "timeout" not in error_text.lower() and "connection" not in error_text.lower():
            code = "E_TOOL_ERROR"
        else:
            code = error_code_for(error_text)
        logger.log(ts=time.time(), level="ERROR", event="tool_error", tool=getattr(func, "__name__", "?"),
                   duration_ms=round(duration, 1), ok=False, trace_id=trace_id, error_code=code)
        return {"ok": False, "error": error_text, "error_code": code, "duration_ms": round(duration, 1)}


# ─────────────────────────────────────────────────────────────────────────
# 3. 并发
# ─────────────────────────────────────────────────────────────────────────


async def concurrent_calls(func: Any, args_list: list[dict[str, Any]], timeout: float, logger: Logger) -> list[dict[str, Any]]:
    """并发调 N 次，每次独立 trace_id。"""
    trace_ids = [str(uuid.uuid4())[:8] for _ in args_list]
    tasks = [call_with_timeout(func, args, timeout, logger, tid) for args, tid in zip(args_list, trace_ids)]
    results = await asyncio.gather(*tasks)
    return list(zip(trace_ids, results))


# ─────────────────────────────────────────────────────────────────────────
# 演示用的异步工具
# ─────────────────────────────────────────────────────────────────────────


async def fast_echo(text: str) -> str:
    """快工具，0ms 返回。"""
    return f"echo:{text}"


async def slow_echo(text: str, delay: float = 0.5) -> str:
    """慢工具，故意超时。"""
    await asyncio.sleep(delay)
    return f"slow:{text}"


async def boom_echo(text: str) -> str:
    """会抛异常的工具。"""
    raise RuntimeError(f"boom on {text}")