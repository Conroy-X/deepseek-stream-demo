"""Unit 19 环境自检 —— 不碰业务逻辑，只回答「这台机器能不能跑」。

跑法：uv run python check_env.py

和 main.py 的自检分工：
  check_env.py   依赖齐不齐、目录能不能写、端口占没占（环境问题）
  main.py        检索算得对不对、指标口径对不对（逻辑问题）
分开的好处是「跑不起来」和「跑出来是错的」不会混成同一个红叉。
"""

from __future__ import annotations

import asyncio
import importlib
import os
import socket
import sys
import tempfile

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
    print("Unit 19 · 环境自检\n")

    print("  —— 解释器与依赖 ——")
    check("Python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0])
    for mod in ("fastapi", "uvicorn", "pydantic", "httpx"):
        try:
            m = importlib.import_module(mod)
            check(f"可导入 {mod}", True, getattr(m, "__version__", ""))
        except ImportError as e:
            check(f"可导入 {mod}", False, str(e))

    print("\n  —— 项目文件 ——")
    for rel in ("main.py", "store.py", "metrics.py", "embedding.py", "static/index.html"):
        p = os.path.join(base, rel)
        check(f"{rel} 存在", os.path.exists(p),
              f"{os.path.getsize(p)} 字节" if os.path.exists(p) else "缺失")

    print("\n  —— 目录可写 ——")
    data_dir = os.path.join(base, "data")
    try:
        os.makedirs(data_dir, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=data_dir, delete=True):
            pass
        check("data/ 可写（向量库要放这里）", True, data_dir)
    except OSError as e:
        check("data/ 可写（向量库要放这里）", False, str(e))

    print("\n  —— embedder ——")
    try:
        import embedding as emb
        e = emb.get_embedder()
        check("embedder 可实例化", True, f"{type(e).__name__} dim={e.dim}")
        v1, v2 = asyncio.run(e.embed(["退款多久到账", "退款多久到账"]))
        check("同一文本两次结果一致（否则检索不可复现）", v1 == v2)
        check("向量已 L2 归一化（模长 ≈ 1）", abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-6,
              f"{sum(x * x for x in v1) ** 0.5:.6f}")
    except Exception as e:  # noqa: BLE001
        check("embedder 可用", False, f"{type(e).__name__}: {e}")

    print("\n  —— 存储层 ——")
    try:
        from store import VectorStore
        st = VectorStore(":memory:", dim=4)
        st.upsert([{"id": "x", "text": "测试", "vector": [1.0, 0.0, 0.0, 0.0],
                    "source": "t.md", "page": 1, "category": "test"}])
        hit = st.search([1.0, 0.0, 0.0, 0.0], top_k=1).hits[0]
        check("内存库 upsert + search 可用", hit.id == "x" and abs(hit.score - 1.0) < 1e-6,
              f"score={hit.score:.4f}")
        check("顶层 category 进 meta（过滤依赖它）", hit.meta.get("category") == "test", str(hit.meta))
    except Exception as e:  # noqa: BLE001
        check("存储层可用", False, f"{type(e).__name__}: {e}")

    print("\n  —— 语料与端口 ——")
    try:
        from main import ALL_CHUNKS, QUESTIONS
        check("语料可导入", len(ALL_CHUNKS) == 27, f"{len(ALL_CHUNKS)} 段")
        check("题目可导入", len(QUESTIONS) == 12, f"{len(QUESTIONS)} 题")
    except Exception as e:  # noqa: BLE001
        check("语料可导入", False, f"{type(e).__name__}: {e}")
    check("端口 8019 空闲（uvicorn 要占它）", port_free(8019),
          "" if port_free(8019) else "已被占用，先关掉旧进程")

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
