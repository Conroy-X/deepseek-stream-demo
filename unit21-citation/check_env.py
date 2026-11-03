"""Unit 21 环境自检 —— 纯离线库 + FastAPI，环境要求很低，重点是「函数都在」。

跑法：uv run python check_env.py
"""

from __future__ import annotations

import importlib
import os
import socket
import sys

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.connect_ex(("127.0.0.1", port)) != 0


def main() -> int:
    base = os.path.dirname(os.path.abspath(__file__))
    print("Unit 21 · 环境自检\n")

    print("  —— 解释器与依赖 ——")
    check("Python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0])
    for mod in ("fastapi", "uvicorn", "pydantic", "httpx"):
        try:
            m = importlib.import_module(mod)
            check(f"可导入 {mod}", True, getattr(m, "__version__", ""))
        except ImportError as e:
            check(f"可导入 {mod}", False, str(e))

    print("\n  —— 项目文件 ——")
    for rel in ("main.py", "citation.py", "static/index.html", "notes.md"):
        p = os.path.join(base, rel)
        check(f"{rel} 存在", os.path.exists(p),
              f"{os.path.getsize(p)} 字节" if os.path.exists(p) else "缺失")

    print("\n  —— 纯函数库 ——")
    try:
        import citation as c
        for fn in ("extract_refs", "split_sentences", "strip_refs", "locate",
                   "support_score", "coverage", "validate", "render", "highlight"):
            check(f"citation.{fn} 存在", callable(getattr(c, fn, None)))
        check("NGRAM = 6（滑窗长度，改它要重标阈值）", c.NGRAM == 6, str(c.NGRAM))
        check("FABRICATED_BELOW = 0.5（低于此判伪造）", c.FABRICATED_BELOW == 0.5,
              str(c.FABRICATED_BELOW))
        check("CITE_RE 只认 1–2 位数字（[2026] 不算引用）",
              c.CITE_RE.pattern == r"\[(\d{1,2})\]", c.CITE_RE.pattern)
    except Exception as e:  # noqa: BLE001
        check("citation 模块可导入", False, f"{type(e).__name__}: {e}")

    print("\n  —— 端到端可用性（进程内，不起服务） ——")
    try:
        from citation import Source, render, validate
        S = [Source(1, "年假需提前 3 个工作日在系统提交申请，由直属主管审批。", "handbook.md", 1)]
        v = validate("年假需提前 3 个工作日在系统提交申请，由直属主管审批。[1]", S)
        check("validate 能跑通", v["coverage"] == 1.0 and v["ok"] is True, f"coverage={v['coverage']}")
        h = render("测试。[1]", S)
        check("render 能跑通且输出 HTML", "<sup" in h and "citation-answer" in h)
    except Exception as e:  # noqa: BLE001
        check("校验/渲染链路可用", False, f"{type(e).__name__}: {e}")

    print("\n  —— 端口 ——")
    free = port_free(8021)
    check("端口 8021 空闲（uvicorn 要占它）", free, "" if free else "已被占用")

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
