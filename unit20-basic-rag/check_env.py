"""Unit 20 环境自检 —— 只回答「这台机器能不能跑」，不验证 RAG 逻辑。

跑法：uv run python check_env.py

重点检查两件本项目特有的事：
  1. 配没配 key —— 决定了走 extractive 还是 api。配了 key 的自检会真花钱，
     所以这里要显式提示，而不是等到跑冒烟时才发现账单。
  2. traces.db 能不能写 —— trace 是本单元的第二交付物，写不进去等于没做。
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


def info(msg: str) -> None:
    print(f"  [INFO] {msg}")


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.connect_ex(("127.0.0.1", port)) != 0


def main() -> int:
    base = os.path.dirname(os.path.abspath(__file__))
    print("Unit 20 · 环境自检\n")

    print("  —— 解释器与依赖 ——")
    check("Python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0])
    for mod in ("fastapi", "uvicorn", "pydantic", "httpx", "dotenv"):
        try:
            m = importlib.import_module(mod)
            check(f"可导入 {mod}", True, getattr(m, "__version__", ""))
        except ImportError as e:
            check(f"可导入 {mod}", False, str(e))

    print("\n  —— 项目文件 ——")
    for rel in ("main.py", "rag.py", "retriever.py", "embedding.py",
                "static/index.html", ".env.example"):
        p = os.path.join(base, rel)
        check(f"{rel} 存在", os.path.exists(p),
              f"{os.path.getsize(p)} 字节" if os.path.exists(p) else "缺失")

    print("\n  —— 生成后端 ——")
    env = os.path.join(base, ".env")
    if os.path.exists(env):
        info(".env 存在，会覆盖 .env.example 的默认值")
    else:
        info("没有 .env —— 走默认值（extractive，不花钱）")
    key = (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    if key:
        info(f"检测到 API key（{key[:4]}…）：走 api 模式，冒烟会**真实计费**")
    else:
        info("没有 API key：走 extractive 离线后端，全流程零成本")
    try:
        import rag
        mode = rag.resolve_mode(None)
        check("模式判定可用", mode in ("extractive", "api"), f"当前 = {mode}")
        check("阈值是数字且落在 (0,1)", 0 < rag.DEFAULT_THRESHOLD < 1, str(rag.DEFAULT_THRESHOLD))
    except Exception as e:  # noqa: BLE001
        check("rag 模块可导入", False, f"{type(e).__name__}: {e}")

    print("\n  —— 检索层 ——")
    try:
        from retriever import Retriever
        r = Retriever()
        check("retriever 可构造", True, f"{len(r.corpus)} 段语料")
        hits = r.search("退款多久到账", top_k=3)
        check("检索能返回结果", len(hits) > 0, f"{len(hits)} 条，首条 {hits[0].id if hits else '—'}")
        check("检索结果带分数与页码",
              bool(hits) and 0 <= hits[0].score <= 1 and hits[0].page >= 1,
              f"score={hits[0].score:.3f} page={hits[0].page}" if hits else "")
    except Exception as e:  # noqa: BLE001
        check("检索层可用", False, f"{type(e).__name__}: {e}")

    print("\n  —— trace 存储 ——")
    try:
        import tempfile
        from rag import TraceStore
        d = os.path.join(base, "data")
        os.makedirs(d, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=d, suffix=".probe", delete=True):
            pass
        ts = TraceStore(os.path.join(tempfile.mkdtemp(prefix="u20-env-"), "t.db"))
        # save() 的 question / mode 是必填（NOT NULL 语义），少给就 KeyError
        tid = ts.save({"question": "env-check", "mode": "extractive", "answer": "ok"})
        check("trace 可写可读", ts.get(tid) is not None, f"id={tid[:8]}…")
        check("data/ 可写（traces.db 放这里）", True, d)
    except Exception as e:  # noqa: BLE001
        check("trace 存储可用", False, f"{type(e).__name__}: {e}")

    print("\n  —— 端口 ——")
    free = port_free(8020)
    check("端口 8020 空闲（uvicorn 要占它）", free, "" if free else "已被占用")

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
