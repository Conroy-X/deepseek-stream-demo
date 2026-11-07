"""Unit 63 · 结构化日志：手写 JSON 行日志器。

核心认知：**日志不是给人肉翻的，是给 grep / 聚合 / 告警用的。**

三个设计决定：
  1. JSON 每行一条 —— 可以 `grep '"event":"llm_call"'`，可以 jq 聚合，而不是读多行 traceback
  2. request_id 贯穿一次请求 —— 用 contextvar 传，中间件生成，LLM/工具日志自动带上
  3. 关键字段固定 —— ts / level / event / request_id 必有；LLM 调用带用量与耗时，
     工具调用带参数与耗时

为什么不用 structlog：本单元要的是「理解每条日志该有什么」，手写一遍才知道
structlog 帮你省了什么。生产直接上 structlog。
"""

from __future__ import annotations

import contextvars
import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# contextvar：每个请求独立的 request_id，中间件 set、业务代码 get，不靠函数传参
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def set_request_id(rid: str) -> None:
    _request_id.set(rid)


def get_request_id() -> str:
    return _request_id.get()


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# 日志器
# ---------------------------------------------------------------------------
LEVELS = ("debug", "info", "warn", "error")


@dataclass
class Logger:
    """把 dict 序列化成 JSON 行写到文件。"""

    path: Path
    _fh: Any = None

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def _emit(self, level: str, event: str, **fields: Any) -> None:
        record = {
            "ts": round(time.time(), 3),
            "level": level,
            "event": event,
            "request_id": get_request_id(),
            **fields,
        }
        # ensure_ascii=False 保留中文；sort_keys 让输出稳定（diff/测试友好）
        self._fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._fh.flush()

    def debug(self, event: str, **fields: Any) -> None:
        self._emit("debug", event, **fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit("info", event, **fields)

    def warn(self, event: str, **fields: Any) -> None:
        self._emit("warn", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._emit("error", event, **fields)

    def close(self) -> None:
        self._fh.close()


def read_logs(path: Path) -> list[dict]:
    """读回 JSON 行日志（自检用）。"""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------------------
# 业务动作：一次请求里的 LLM 调用 + 工具调用
# ---------------------------------------------------------------------------
def log_llm_call(logger: Logger, model: str, prompt_tokens: int, completion_tokens: int, elapsed_ms: float) -> None:
    logger.info(
        "llm_call",
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        elapsed_ms=round(elapsed_ms, 2),
    )


def log_tool_call(logger: Logger, tool: str, args: dict, ok: bool, elapsed_ms: float) -> None:
    logger.info(
        "tool_call",
        tool=tool,
        args=args,
        ok=ok,
        elapsed_ms=round(elapsed_ms, 2),
    )


def log_error(logger: Logger, exc: Exception, where: str) -> None:
    logger.error("exception", where=where, exc_type=type(exc).__name__, message=str(exc))


# ---------------------------------------------------------------------------
# 独立自检
# ---------------------------------------------------------------------------
def run_selfcheck() -> int:
    import tempfile

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 63 · 结构化日志 自检\n")

    tmp = Path(tempfile.mkdtemp(prefix="u63-"))
    logfile = tmp / "app.log"

    # ---- 基本写入 ----
    lg = Logger(logfile)
    lg.info("hello", k="v")
    lg.close()
    rows = read_logs(logfile)
    check("写了一条", len(rows) == 1, str(len(rows)))
    r = rows[0]
    check("有 ts", isinstance(r.get("ts"), float))
    check("level=info", r.get("level") == "info")
    check("event=hello", r.get("event") == "hello")
    check("自定义字段 k=v", r.get("k") == "v")
    check("request_id 默认 -", r.get("request_id") == "-")

    # ---- request_id 贯穿 ----
    lg2 = Logger(tmp / "app2.log")
    set_request_id("req-aaa")
    log_llm_call(lg2, "deepseek-flash", 100, 50, 1234.5)
    log_tool_call(lg2, "search", {"q": "x"}, True, 5.0)
    log_error(lg2, ValueError("boom"), "ask")
    lg2.close()

    rows2 = read_logs(tmp / "app2.log")
    check("写 3 条", len(rows2) == 3, str(len(rows2)))
    check("所有日志带同一 request_id", all(r["request_id"] == "req-aaa" for r in rows2))

    llm_row = next(r for r in rows2 if r["event"] == "llm_call")
    check("LLM 日志带 model", llm_row.get("model") == "deepseek-flash")
    check("LLM 日志带输入 token", llm_row.get("prompt_tokens") == 100)
    check("LLM 日志带输出 token", llm_row.get("completion_tokens") == 50)
    check("LLM 日志带耗时", llm_row.get("elapsed_ms") == 1234.5)

    tool_row = next(r for r in rows2 if r["event"] == "tool_call")
    check("工具日志带 tool", tool_row.get("tool") == "search")
    check("工具日志带 args", tool_row.get("args") == {"q": "x"})
    check("工具日志带 ok", tool_row.get("ok") is True)

    err_row = next(r for r in rows2 if r["event"] == "exception")
    check("错误日志 level=error", err_row.get("level") == "error")
    check("错误日志带异常类型", err_row.get("exc_type") == "ValueError")
    check("错误日志带位置", err_row.get("where") == "ask")

    # ---- 每行都是合法 JSON 且可 grep ----
    text = (tmp / "app2.log").read_text(encoding="utf-8")
    check("每行合法 JSON", all(json.loads(l) for l in text.splitlines() if l.strip()))
    check("能按 event grep 出 LLM 调用", text.count('"event": "llm_call"') == 1)

    # ---- 跨请求隔离 ----
    lg3 = Logger(tmp / "app3.log")
    set_request_id("req-111")
    lg3.info("start")
    set_request_id("req-222")
    lg3.info("start")
    lg3.close()
    r3 = read_logs(tmp / "app3.log")
    check("两次请求 id 不同", r3[0]["request_id"] == "req-111" and r3[1]["request_id"] == "req-222")

    # ---- 分级 ----
    lg4 = Logger(tmp / "app4.log")
    lg4.warn("w")
    lg4.close()
    check("warn 级别记录", read_logs(tmp / "app4.log")[0]["level"] == "warn")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_selfcheck())
