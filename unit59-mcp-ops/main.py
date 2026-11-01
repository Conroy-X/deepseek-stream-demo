"""Unit 59 · MCP 工程化实验台（端口 8059）。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

import ops

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Unit 59 · MCP 工程化")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "unit": 59, "modules": ["logging", "timeout", "concurrency", "error_codes"]}


@app.get("/api/error-codes")
def api_error_codes() -> dict[str, Any]:
    return {"codes": ops.ERROR_CODES}


@app.get("/api/logs")
def api_logs() -> dict[str, Any]:
    """跑一遍快+慢+炸，返回日志。"""
    async def run() -> dict[str, Any]:
        logger = ops.Logger()
        await ops.call_with_timeout(ops.fast_echo, {"text": "hi"}, 0.2, logger, "trace-1")
        await ops.call_with_timeout(ops.slow_echo, {"text": "hi", "delay": 0.5}, 0.2, logger, "trace-2")
        await ops.call_with_timeout(ops.boom_echo, {"text": "hi"}, 0.5, logger, "trace-3")
        return {"logs": logger.logs, "dropped": logger.dropped_fields, "redacted": logger.redacted_count, "dump": logger.dump()}
    return asyncio.run(run())


@app.get("/api/concurrent")
def api_concurrent() -> dict[str, Any]:
    """5 个并发调用。"""
    async def run() -> dict[str, Any]:
        logger = ops.Logger()
        args_list = [{"text": f"call-{i}"} for i in range(5)]
        results = await ops.concurrent_calls(ops.fast_echo, args_list, 0.5, logger)
        return {
            "results": [{"trace_id": tid, "result": r} for tid, r in results],
            "trace_ids": [tid for tid, _ in results],
            "unique_trace_ids": len(set(tid for tid, _ in results)),
        }
    return asyncio.run(run())


@app.get("/")
def index() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


# ── 自检 ─────────────────────────────────────────────────────────────────────

passed = 0
failed = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> None:
    from fastapi.testclient import TestClient

    print("\n=== Unit 59 · MCP 工程化 自检 ===\n")

    print("  —— 结构化日志 ——")
    logger = ops.Logger()
    logger.log(ts=123, level="INFO", event="tool_call", tool="add", duration_ms=5.2, ok=True, trace_id="t-1")
    check("白名单字段保留", len(logger.logs) == 1 and "tool" in logger.logs[0], str(logger.logs))
    check("ts 字段保留", logger.logs[0]["ts"] == 123, str(logger.logs[0]))
    check("trace_id 保留", logger.logs[0]["trace_id"] == "t-1", str(logger.logs[0]))

    logger.log(ts=124, level="WARN", event="call", tool="add", token="tok-secret-123", password="hunter2", sql="SELECT * FROM users")
    check("非白名单字段被剔除", "token" not in logger.logs[1] and "password" not in logger.logs[1] and "sql" not in logger.logs[1], str(logger.logs[1]))
    check("被剔的字段被记下", "token" in logger.dropped_fields and "password" in logger.dropped_fields, str(logger.dropped_fields))

    # 敏感值打码
    logger2 = ops.Logger()
    logger2.log(ts=125, level="INFO", event="auth", tool="login", trace_id="t-2", ok=True)
    # trace_id 里没有敏感词，不会打码
    check("无敏感词不打码", logger2.redacted_count == 0, str(logger2.redacted_count))

    # 带敏感值的日志（通过 tool 字段传）
    logger3 = ops.Logger()
    logger3.log(ts=126, level="INFO", event="auth", tool="token=Bearer abc123", trace_id="t-3", ok=True)
    check("token 敏感值被打码", "Bearer" not in logger3.logs[0]["tool"], str(logger3.logs[0]))
    check("打码计数累加", logger3.redacted_count >= 1, str(logger3.redacted_count))

    # 日志是 JSON
    dump = logger.dump()
    lines = dump.strip().split("\n")
    check("每条日志是 JSON", all(json.loads(l) for l in lines), dump[:100])

    print("  —— 超时 ——")
    async def probe_timeout() -> dict[str, Any]:
        logger = ops.Logger()
        fast = await ops.call_with_timeout(ops.fast_echo, {"text": "ok"}, 0.2, logger, "t-fast")
        slow = await ops.call_with_timeout(ops.slow_echo, {"text": "ok", "delay": 0.5}, 0.2, logger, "t-slow")
        boom = await ops.call_with_timeout(ops.boom_echo, {"text": "ok"}, 0.5, logger, "t-boom")
        return {"fast": fast, "slow": slow, "boom": boom, "logs": logger.logs}
    facts = asyncio.run(probe_timeout())
    check("快工具成功", facts["fast"]["ok"] is True, str(facts["fast"]))
    check("慢工具超时被捕获", facts["slow"]["ok"] is False and facts["slow"]["error_code"] == "E_TOOL_TIMEOUT", str(facts["slow"]))
    check("超时不崩整个进程", "error" in facts["slow"] and "超时" in facts["slow"]["error"], str(facts["slow"]))
    check("超时耗时约 200ms", 180 < facts["slow"]["duration_ms"] < 400, str(facts["slow"]["duration_ms"]))
    check("炸的工具也被接住", facts["boom"]["ok"] is False and "boom" in facts["boom"]["error"], str(facts["boom"]))
    check("炸的错误码是 E_TOOL_ERROR", facts["boom"]["error_code"] == "E_TOOL_ERROR", str(facts["boom"]))

    print("  —— 并发 ——")
    async def probe_concurrent() -> dict[str, Any]:
        logger = ops.Logger()
        args_list = [{"text": f"c-{i}"} for i in range(5)]
        results = await ops.concurrent_calls(ops.fast_echo, args_list, 0.5, logger)
        return {
            "count": len(results),
            "trace_ids": [tid for tid, _ in results],
            "unique": len(set(tid for tid, _ in results)),
            "logs": logger.logs,
        }
    conc = asyncio.run(probe_concurrent())
    check("5 个并发全完成", conc["count"] == 5, str(conc["count"]))
    check("5 个 trace_id 各不相同", conc["unique"] == 5, str(conc["trace_ids"]))
    check("日志记了 5 条", len(conc["logs"]) == 5, str(len(conc["logs"])))
    check("每条日志有 trace_id", all("trace_id" in l for l in conc["logs"]), str(conc["logs"]))

    print("  —— 错误码稳定 ——")
    for error_text, expected_code in [
        ("near 'FROM': syntax error", "E_SQL_SYNTAX"),
        ("检测到多语句", "E_SQL_FORBIDDEN"),
        ("只允许 SELECT", "E_SQL_FORBIDDEN"),
        ("attempt to write a readonly database", "E_SQL_FORBIDDEN"),
        ("工具执行超时", "E_TOOL_TIMEOUT"),
        ("timeout after 200ms", "E_TOOL_TIMEOUT"),
        ("Error executing tool boom", "E_TOOL_ERROR"),
        ("connection refused", "E_UPSTREAM"),
    ]:
        code = ops.error_code_for(error_text)
        check(f"「{error_text[:25]}」→ {expected_code}", code == expected_code, f"实际 {code}")

    # 同一种失败总是同一个码
    check("syntax error 稳定", ops.error_code_for("near x: syntax error") == ops.error_code_for("syntax error near y"), "不稳定")
    check("多语句稳定", ops.error_code_for("多语句1") == ops.error_code_for("多语句2"), "不稳定")

    print("  —— 对外响应不含 traceback ——")
    resp = ops.public_response("E_TOOL_ERROR", "Error executing tool boom\nTraceback (most recent call last):\n  ...")
    check("对外只有 error_code", resp["error_code"] == "E_TOOL_ERROR", str(resp))
    check("对外有简要 message", bool(resp["message"]), str(resp))
    check("对外不含 traceback", "Traceback" not in str(resp), str(resp))
    check("对外不含 error 原文", "error" not in resp, str(resp))

    print("  —— HTTP 层 ——")
    client = TestClient(app)
    check("/health 200", client.get("/health").status_code == 200)
    ec = client.get("/api/error-codes").json()
    check("5 个错误码", len(ec["codes"]) == 5, str(ec))
    check("错误码全以 E_ 开头", all(k.startswith("E_") for k in ec["codes"]), str(ec))
    logs = client.get("/api/logs").json()
    check("/api/logs 有 3 条日志", len(logs["logs"]) == 3, str(len(logs["logs"])))
    check("/api/logs 每条是 JSON", all(isinstance(l, dict) for l in logs["logs"]), str(logs["logs"][0]))
    conc_resp = client.get("/api/concurrent").json()
    check("/api/concurrent 5 个结果", len(conc_resp["results"]) == 5, str(conc_resp))
    check("/api/concurrent trace_id 唯一", conc_resp["unique_trace_ids"] == 5, str(conc_resp))

    page = client.get("/")
    check("GET / 返回页面", page.status_code == 200, str(page.status_code))
    check("页面含关键文案", "工程化" in page.text and len(page.text) > 3000, f"{len(page.text)} 字节")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()