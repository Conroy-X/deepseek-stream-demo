"""Unit 16 · 压测：数据量 × 索引方式 的耗时与召回对比。

    uv run python bench.py            # 默认 1k / 1万 / 5万

它回答 notes.md 里那张表的每一格：
    · 无索引（Seq Scan + Sort）
    · HNSW
    · IVFFlat
并额外给出两件更重要的东西：
    · 建索引本身的耗时（生产上这是停机窗口）
    · recall@10（索引是近似检索，快不等于对）

## 数据为什么是"聚类 + 噪声"而不是纯随机

第一版用 `random()` 造向量，结果 HNSW 的 recall@10 只有 0.10 —— 不是索引坏，
是**高维随机向量彼此几乎等距**（512 维里任意两个随机向量的余弦分布在 0 附近极窄，
标准差约 1/√512 ≈ 0.044）。所有点距离都一样时，"找最近的 10 个"本身就是个病态问题，
任何图索引都会失效。这叫维度灾难下的一种退化。

真实 embedding 不是这样的：它们聚成一簇簇（同一主题的句子挤在一起），
簇内距离近、簇间距离远，图索引才有用武之地。
所以这里用 K 个簇心 + 小噪声生成，并让**查询向量也从同一分布采样** ——
查询来自另一个分布时，测出来的召回没有意义。
"""

from __future__ import annotations

import statistics
import time

import numpy as np
from sqlalchemy import text

from main import EMBED_DIM, TABLE, engine, init_table

SCALES = [1_000, 10_000, 50_000]
RUNS = 5
TOP_K = 10
CLUSTERS = 200
NOISE = 0.35  # 噪声越大，簇越糊，索引越难做


def _unit(x: np.ndarray) -> np.ndarray:
    return x / np.linalg.norm(x, axis=1, keepdims=True)


def _make(n: int, rng: np.random.Generator, centers: np.ndarray) -> np.ndarray:
    """从 clustered 分布采样 n 个单位向量。"""
    assign = rng.integers(0, len(centers), size=n)
    x = centers[assign] + rng.standard_normal((n, EMBED_DIM)) * NOISE
    return _unit(x)


def seed(target: int, rng: np.random.Generator, centers: np.ndarray) -> int:
    """把 bench-% 行补到 target 条。已够就不动。"""
    with engine.connect() as c:
        cur = c.execute(
            text(f"SELECT count(*) FROM {TABLE} WHERE text LIKE 'bench-%'")
        ).scalar_one()
    need = target - cur
    if need <= 0:
        return cur

    t0 = time.perf_counter()
    vecs = _make(need, rng, centers)
    rows = [
        (f"bench-{cur + i + 1}", "[" + ",".join(f"{v:.6f}" for v in vec) + "]")
        for i, vec in enumerate(vecs)
    ]
    # COPY 比逐条 INSERT 快一个数量级 —— 造 4 万行大约 2s
    with engine.begin() as conn:
        raw = conn.connection.driver_connection
        with raw.cursor() as cur_:
            with cur_.copy(f"COPY {TABLE} (text, emb) FROM STDIN") as copy:
                for row in rows:
                    copy.write_row(row)
    print(f"  造数 {need} 行，耗时 {time.perf_counter() - t0:.1f}s")
    return target


def drop_vector_indices():
    with engine.begin() as c:
        c.execute(text("DROP INDEX IF EXISTS chunks_emb_hnsw"))
        c.execute(text("DROP INDEX IF EXISTS chunks_emb_ivf"))


def build_index(kind: str, rows: int) -> float:
    """建索引并返回耗时（秒）。"""
    drop_vector_indices()
    t0 = time.perf_counter()
    with engine.begin() as c:
        if kind == "hnsw":
            c.execute(
                text(
                    f"CREATE INDEX chunks_emb_hnsw ON {TABLE} "
                    "USING hnsw (emb vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
                )
            )
        elif kind == "ivfflat":
            # pgvector 经验值：lists ≈ rows/1000（≤1M 行）。小表兜底给 4，
            # 否则 1 个桶 = 退化成全扫，索引根本没意义。
            lists = max(4, min(1000, rows // 1000))
            c.execute(
                text(
                    f"CREATE INDEX chunks_emb_ivf ON {TABLE} "
                    f"USING ivfflat (emb vector_cosine_ops) WITH (lists = {lists})"
                )
            )
    return time.perf_counter() - t0


SQL = f"""
    SELECT id FROM {TABLE}
    ORDER BY emb <=> CAST(:q AS vector)
    LIMIT {TOP_K}
"""


def _plan_node(plan) -> str:
    p = plan[0]["Plan"] if isinstance(plan, list) else plan["Plan"]
    while p.get("Plans"):
        if p["Node Type"] in ("Sort", "Limit", "Aggregate"):
            p = p["Plans"][0]
            continue
        break
    return p.get("Node Type", "?") + (" using " + p["Index Name"] if "Index Name" in p else "")


def query(qv: str, runs: int = RUNS) -> tuple[float, list[int], str]:
    """跑多次取中位数，同时拿回执行计划和 Top-K id（算 recall 用）。"""
    times: list[float] = []
    ids: list[int] = []
    node = ""
    for _ in range(runs):
        with engine.connect() as c:
            ids = list(c.execute(text(SQL), {"q": qv}).scalars().all())
            plan = c.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + SQL), {"q": qv}
            ).scalar_one()
        t = plan[0]["Execution Time"] if isinstance(plan, list) else plan["Execution Time"]
        times.append(float(t))
        node = _plan_node(plan)
    return statistics.median(times), ids, node


def main() -> int:
    print("=" * 78)
    print("Unit 16 · 索引压测")
    print("=" * 78)
    init_table()

    rng = np.random.default_rng(20261103)
    # 簇心**不要**归一化：512 维标准正态的模长约 √512 ≈ 22.6，
    # 噪声（每维 std=0.35）的模长约 7.9，比值 0.35 —— 簇结构才明显（同簇余弦≈0.89）。
    # 若先把簇心压成模长 1，噪声反而大 7.9 倍，数据会退化成纯随机（同簇余弦≈0.01），
    # 索引测出来的召回就成了假数据。这个坑踩过一次，留在这里当路标。
    centers = rng.standard_normal((CLUSTERS, EMBED_DIM))
    qvec = _make(1, rng, centers)[0]
    qv = "[" + ",".join(f"{v:.6f}" for v in qvec) + "]"

    rows_table: list[dict] = []
    for n in SCALES:
        print(f"\n[{n:,} 行]")
        seed(n, rng, centers)

        drop_vector_indices()
        t0, base_ids, base_node = query(qv)
        print(f"  无索引     {t0:8.2f} ms   ({base_node})")

        tb1 = build_index("hnsw", n)
        with engine.begin() as c:
            c.execute(text("SET hnsw.ef_search = 40"))
        t1, hnsw_ids, n1 = query(qv)
        print(f"  HNSW       {t1:8.2f} ms   ({n1})  建索引 {tb1:.1f}s")

        tb2 = build_index("ivfflat", n)
        with engine.begin() as c:
            c.execute(text("SET ivfflat.probes = 10"))
        t2, ivf_ids, n2 = query(qv)
        print(f"  IVFFlat    {t2:8.2f} ms   ({n2})  建索引 {tb2:.1f}s")

        def recall(cand: list[int]) -> float:
            return len(set(cand) & set(base_ids)) / max(1, len(set(base_ids)))

        print(f"  recall@{TOP_K}  HNSW {recall(hnsw_ids):.2f}   IVFFlat {recall(ivf_ids):.2f}")
        rows_table.append(
            {
                "n": n,
                "seq": t0,
                "hnsw": t1,
                "ivf": t2,
                "b1": tb1,
                "b2": tb2,
                "r1": recall(hnsw_ids),
                "r2": recall(ivf_ids),
            }
        )

    drop_vector_indices()
    print("\n" + "=" * 78)
    print("复制到 notes.md 的实测表：")
    print("| 数据量 | 无索引 | HNSW | IVFFlat | 建 HNSW | 建 IVFFlat | recall@10 HNSW / IVFFlat |")
    print("| --- | --- | --- | --- | --- | --- | --- |")
    for r in rows_table:
        print(
            f"| {r['n']:,} | {r['seq']:.1f} ms | {r['hnsw']:.1f} ms | {r['ivf']:.1f} ms | "
            f"{r['b1']:.1f}s | {r['b2']:.1f}s | {r['r1']:.2f} / {r['r2']:.2f} |"
        )
    with engine.connect() as c:
        size = c.execute(
            text(f"SELECT pg_size_pretty(pg_total_relation_size('{TABLE}'))")
        ).scalar_one()
    print(f"\n表总大小：{size}（留在库里不影响后续单元；清理：DELETE FROM chunks WHERE text LIKE 'bench-%'）")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
