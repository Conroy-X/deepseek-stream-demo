"""参数取舍实验：ef_search / probes 怎么影响 延迟 与 召回。

压测表里 recall 全是 1.00，是因为默认参数（ef_search=40 / probes=10）
在 5 万条、200 个簇的数据上已经够用 —— 故事没讲完。
这个脚本把参数一格一格往下压，直到开始漏，让"近似"这件事现形。

    uv run python sweep.py
"""

from __future__ import annotations

import statistics
import time

import numpy as np
from sqlalchemy import text

from bench import CLUSTERS, NOISE, _make
from main import TABLE, engine

TOP_K = 10
RUNS = 5
N = 50_000


def exact_topk(qv: str) -> list[int]:
    with engine.begin() as c:
        c.execute(text("SET LOCAL enable_indexscan = off"))
        return list(c.execute(text(_SQL), {"q": qv}).scalars().all())


_SQL = f"SELECT id FROM {TABLE} ORDER BY emb <=> CAST(:q AS vector) LIMIT {TOP_K}"


def run_with(setting: str, value: str, qv: str) -> tuple[float, list[int], str]:
    times, ids, node = [], [], ""
    for _ in range(RUNS):
        with engine.connect() as c:
            # 必须是同一个连接：SET 是会话级的，换连接就失效
            c.execute(text(f"SET {setting} = {value}"))
            t0 = time.perf_counter()
            ids = list(c.execute(text(_SQL), {"q": qv}).scalars().all())
            times.append((time.perf_counter() - t0) * 1000)
            plan = c.execute(
                text("EXPLAIN (FORMAT JSON) " + _SQL), {"q": qv}
            ).scalar_one()
    p = plan[0]["Plan"] if isinstance(plan, list) else plan["Plan"]
    while p.get("Plans"):
        if p["Node Type"] in ("Sort", "Limit", "Aggregate"):
            p = p["Plans"][0]
            continue
        break
    node = p.get("Node Type", "?") + (" using " + p["Index Name"] if "Index Name" in p else "")
    return statistics.median(times), ids, node


def build(kind: str, rows: int) -> float:
    with engine.begin() as c:
        c.execute(text("DROP INDEX IF EXISTS chunks_emb_hnsw"))
        c.execute(text("DROP INDEX IF EXISTS chunks_emb_ivf"))
    t0 = time.perf_counter()
    with engine.begin() as c:
        if kind == "hnsw":
            c.execute(
                text(
                    f"CREATE INDEX chunks_emb_hnsw ON {TABLE} "
                    "USING hnsw (emb vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
                )
            )
        else:
            lists = max(4, min(1000, rows // 1000))
            c.execute(
                text(
                    f"CREATE INDEX chunks_emb_ivf ON {TABLE} "
                    f"USING ivfflat (emb vector_cosine_ops) WITH (lists = {lists})"
                )
            )
    return time.perf_counter() - t0


def main() -> int:
    rng = np.random.default_rng(20261103)
    centers = rng.standard_normal((CLUSTERS, 512))
    qv = "[" + ",".join(f"{v:.6f}" for v in _make(1, rng, centers)[0]) + "]"

    t_exact, base, node = run_with("hnsw.ef_search", "40", qv)  # 先拿无索引基线
    with engine.begin() as c:
        c.execute(text("DROP INDEX IF EXISTS chunks_emb_hnsw"))
        c.execute(text("DROP INDEX IF EXISTS chunks_emb_ivf"))
        c.execute(text("SET LOCAL enable_indexscan = off"))
        t0 = time.perf_counter()
        base = list(c.execute(text(_SQL), {"q": qv}).scalars().all())
        t_exact = (time.perf_counter() - t0) * 1000
    print(f"无索引基线：{t_exact:.2f} ms（作为 recall 的对照组）\n")

    print("=== HNSW：ef_search 扫描 ===")
    tb = build("hnsw", N)
    print(f"建索引 {tb:.1f}s\n")
    print("| ef_search | P50 延迟 | recall@10 |")
    print("| --- | --- | --- |")
    for ef in (1, 4, 10, 40, 100, 200):
        t, ids, node = run_with("hnsw.ef_search", str(ef), qv)
        r = len(set(ids) & set(base)) / TOP_K
        print(f"| {ef} | {t:.2f} ms | {r:.2f} |")

    print("\n=== IVFFlat：probes 扫描（lists=50）===")
    tb2 = build("ivfflat", N)
    print(f"建索引 {tb2:.1f}s\n")
    print("| probes | P50 延迟 | recall@10 |")
    print("| --- | --- | --- |")
    for pr in (1, 2, 5, 10, 25, 50):
        t, ids, node = run_with("ivfflat.probes", str(pr), qv)
        r = len(set(ids) & set(base)) / TOP_K
        print(f"| {pr} | {t:.2f} ms | {r:.2f} |")

    with engine.begin() as c:
        c.execute(text("DROP INDEX IF EXISTS chunks_emb_hnsw"))
        c.execute(text("DROP INDEX IF EXISTS chunks_emb_ivf"))
    print("\n（已清理索引；数据仍在库里）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
