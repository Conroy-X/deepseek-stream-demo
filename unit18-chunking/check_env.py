"""Unit 18 环境自检 —— 先确认这台机器能不能做切块实验。

    uv run python check_env.py

它检查的是"实验条件"，不是代码质量：
依赖齐不齐、语料有多少、三种策略都能切、覆盖率是否 100%。
"""

from __future__ import annotations

import platform
import sys

from chunker import STRATEGIES, ChunkError, chunk_document, coverage
from embedding import get_embedder
from main import DEMO_PAGES, QUESTIONS, _adjacent_sims

ok = bad = 0


def check(name: str, fn) -> None:
    global ok, bad
    try:
        detail = fn()
        ok += 1
        print(f"  ✓ {name:36} {detail}")
    except Exception as e:  # noqa: BLE001
        bad += 1
        print(f"  ✗ {name:36} {type(e).__name__}: {e}")


def main() -> int:
    print("=" * 76)
    print("Unit 18 · 切块策略 环境自检")
    print("=" * 76)

    print("\n[1] 解释器")
    check("Python ≥ 3.11", lambda: platform.python_version() if sys.version_info >= (3, 11) else (_ for _ in ()).throw(RuntimeError(sys.version)))
    check("在 uv 虚拟环境里", lambda: "ok" if ".venv" in sys.prefix else (_ for _ in ()).throw(RuntimeError("用 uv run 跑")))

    print("\n[2] 依赖")
    for mod in ("fastapi", "httpx", "dotenv"):
        check(mod, lambda m=mod: getattr(__import__(m), "__version__", "已安装"))

    print("\n[3] 语料与题目")
    chars = sum(len(p) for p in DEMO_PAGES)
    check("内置语料非空", lambda: f"{len(DEMO_PAGES)} 页 / {chars} 字")
    check("题目数 ≥ 10", lambda: f"{len(QUESTIONS)} 道" if len(QUESTIONS) >= 10 else (_ for _ in ()).throw(RuntimeError(f"只有 {len(QUESTIONS)} 道，评测结论不稳")))

    print("\n[4] 向量后端（决定语义切分阈值怎么标定）")
    e = get_embedder()
    check("后端可用", lambda: f"{getattr(e, 'name', '?')} / {getattr(e, 'dim', '?')} 维")
    sims = _adjacent_sims(DEMO_PAGES, e)
    med = sorted(sims)[len(sims) // 2]
    check("相邻句相似度中位数", lambda: f"{med:.3f}（语义阈值应按这个量级定，别抄 0.8）")

    print("\n[5] 三种策略都能切且不丢字")
    for s in STRATEGIES:
        def probe(strategy: str = s):
            chunks = chunk_document(DEMO_PAGES, strategy, 160, 30)
            cov = coverage(chunks, chars + len(DEMO_PAGES) - 1)
            if cov < 0.999:
                raise RuntimeError(f"覆盖率 {cov:.3f}，丢字了")
            return f"{len(chunks)} 块，覆盖率 {cov:.3f}"

        check(f"策略 {s}", probe)

    print("\n[6] 参数校验")
    def bad_overlap():
        try:
            chunk_document(DEMO_PAGES, "fixed", 100, 100)
        except ChunkError:
            return "overlap >= size 被拒绝"
        raise RuntimeError("没拒绝，会死循环")

    def bad_strategy():
        try:
            chunk_document(DEMO_PAGES, "magic", 100, 10)
        except ChunkError:
            return "未知策略被拒绝"
        raise RuntimeError("没拒绝")

    def bad_size():
        try:
            chunk_document(DEMO_PAGES, "fixed", 0, 0)
        except ChunkError:
            return "size = 0 被拒绝"
        raise RuntimeError("没拒绝")

    check("overlap >= size", bad_overlap)
    check("未知策略", bad_strategy)
    check("size = 0", bad_size)

    print("\n" + "=" * 76)
    print(f"通过 {ok} / 失败 {bad}")
    if bad == 0:
        print("环境就绪。下一步：uv run python main.py（29 项）→ uv run python sweep.py（参数扫描）")
    print("=" * 76)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
