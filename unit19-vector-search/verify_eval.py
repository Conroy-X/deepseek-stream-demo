"""Unit 19 实测复现脚本 —— README / notes 里每张表都由这里生成。

跑法：
    uv run python verify_eval.py

它做三件事：
  1. 复现 README 的四张表（K 扫描、阈值扫描、逐题明细、过滤）
  2. 做「去掉干扰段」的对照实验 —— 证明指标好看可能是评测集的锅
  3. 断言所有文档里写死的数字，对不上就报错（文档漂移比 bug 更难发现）

为什么要有这个脚本：README 里的数字是手抄的，代码改一次就可能全错，
而没人会回头重抄。让脚本成为唯一数据源，文档只是它的输出。
"""

from __future__ import annotations

import os
import statistics
import sys
import tempfile
import time

import embedding as emb
from main import ALL_CHUNKS, CORPUS, DISTRACTORS, QUESTIONS, _vectorize
from metrics import evaluate
from store import VectorStore

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def build_store(chunks: list[dict], path: str) -> VectorStore:
    store = VectorStore(path)
    store.clear()
    vecs = _vectorize([c["text"] for c in chunks])
    store.upsert([{**c, "vector": v} for c, v in zip(chunks, vecs)])
    return store


def ranked_rows(store: VectorStore, qvecs: list[list[float]], top_k: int,
                min_score: float | None = None, filters: dict | None = None) -> list[dict]:
    rows = []
    for q, qv in zip(QUESTIONS, qvecs):
        res = store.search(qv, top_k=top_k, min_score=min_score, filters=filters)
        rows.append(
            {
                "q": q["q"],
                "ranked": [h.id for h in res.hits],
                "gold": [q["gold"]],
                "count": len(res.hits),
                "top_score": round(res.hits[0].score, 4) if res.hits else 0.0,
                "expected": q["expected"],
                "text_ok": any(q["expected"] in h.text for h in res.hits),
            }
        )
    return rows


def main() -> int:
    e = emb.get_embedder()
    print(f"[0] 环境：embedder={type(e).__name__} dim={e.dim} "
          f"正文={len(CORPUS)} 干扰={len(DISTRACTORS)} 合计={len(ALL_CHUNKS)} 题目={len(QUESTIONS)}")
    print()

    tmp = tempfile.mkdtemp(prefix="u19-verify-")
    full_db = os.path.join(tmp, "full.db")
    store = build_store(ALL_CHUNKS, full_db)
    qvecs = _vectorize([q["q"] for q in QUESTIONS])
    wide = ranked_rows(store, qvecs, 10)

    # ---------------- [1] K 扫描 ----------------
    print("[1] K 扫描（12 题 / 27 段）")
    print()
    print("| K | Recall@K | MRR | 命中题数 |")
    print("| ---: | ---: | ---: | ---: |")
    sweep = {}
    for k in (1, 3, 5, 10):
        r = evaluate(wide, k)
        sweep[k] = r
        print(f"| {k} | {r['recall_at_k']:.3f} | {r['mrr']:.3f} | {r['hits']}/{r['questions']} |")
    print()
    check("Recall@1 == 0.917", abs(sweep[1]["recall_at_k"] - 0.917) < 5e-4, f"{sweep[1]['recall_at_k']}")
    check("MRR == 0.925", abs(sweep[1]["mrr"] - 0.925) < 5e-4, f"{sweep[1]['mrr']}")
    check("Recall@10 == 1.000", sweep[10]["recall_at_k"] == 1.0)
    check("K 从 3 到 10 召回变大、MRR 不变",
          sweep[10]["recall_at_k"] > sweep[3]["recall_at_k"]
          and abs(sweep[10]["mrr"] - sweep[3]["mrr"]) < 1e-9,
          f"recall {sweep[3]['recall_at_k']}→{sweep[10]['recall_at_k']}, "
          f"mrr {sweep[3]['mrr']}→{sweep[10]['mrr']}")
    print()

    # ---------------- [2] 阈值扫描 ----------------
    print("[2] 阈值扫描（top_k=3）")
    print()
    print("| min_score | Recall@3 | MRR | 平均返回 | 完全为空的题 |")
    print("| ---: | ---: | ---: | ---: | ---: |")
    thr_rows = {}
    for t in (None, 0.1, 0.15, 0.2):
        rows = ranked_rows(store, qvecs, 3, t)
        r = evaluate(rows, 3)
        avg = round(sum(x["count"] for x in rows) / len(rows), 2)
        empty = sum(1 for x in rows if x["count"] == 0)
        thr_rows[t] = (r["recall_at_k"], avg, empty)
        print(f"| {'无' if t is None else t} | {r['recall_at_k']:.3f} | {r['mrr']:.3f} | {avg:.2f} | {empty} |")
    print()
    check("阈值 0.15 召回不掉", thr_rows[0.15][0] == thr_rows[None][0],
          f"{thr_rows[None][0]} → {thr_rows[0.15][0]}")
    check("阈值 0.15 平均返回 == 2.08", abs(thr_rows[0.15][1] - 2.08) < 5e-3, f"{thr_rows[0.15][1]}")
    check("阈值 0.2 开始掉召回", thr_rows[0.2][0] < thr_rows[0.15][0],
          f"{thr_rows[0.15][0]} → {thr_rows[0.2][0]}")
    print()

    # ---------------- [3] 逐题明细 ----------------
    print("[3] 逐题明细（top_k=10，看谁掉队）")
    print()
    print("| # | 问题 | rank | 最高分 | 答案原文在结果里 |")
    print("| ---: | --- | ---: | ---: | :---: |")
    worst = None
    # rank 是 evaluate() 算出来的，rows 里只有 id 列表 —— 故意只算一处，避免两套判定
    for i, (d, det) in enumerate(zip(wide, evaluate(wide, 10)["details"]), 1):
        rank = "—" if det["rank"] is None else det["rank"]
        print(f"| {i} | {d['q']} | {rank} | {d['top_score']:.3f} | "
              f"{'是' if d['text_ok'] else '否'} |")
        if det["rank"] and det["rank"] > 1 and worst is None:
            worst = {**d, "rank": det["rank"]}
    print()
    check("掉队的题是「住宿费一晚最多报多少」",
          worst is not None and worst["q"] == "住宿费一晚最多报多少", str(worst and worst["q"]))
    check("它排在第 10 名", worst is not None and worst["rank"] == 10, str(worst and worst["rank"]))
    check("所有题的结果里都带答案原文片段", all(d["text_ok"] for d in wide),
          f"{sum(d['text_ok'] for d in wide)}/{len(wide)}")
    print()

    # ---------------- [4] 对照：去掉干扰段 ----------------
    print("[4] 对照实验：只用 16 段正文（去掉 11 段干扰）")
    print()
    clean_db = os.path.join(tmp, "clean.db")
    clean = build_store(CORPUS, clean_db)
    clean_wide = ranked_rows(clean, qvecs, 10)
    clean_det = evaluate(clean_wide, 10)["details"]
    r1 = evaluate(clean_wide, 1)
    r10 = evaluate(clean_wide, 10)
    print("| 语料 | Recall@1 | Recall@10 | MRR@10 |")
    print("| --- | ---: | ---: | ---: |")
    print(f"| 16 段正文（干净） | {r1['recall_at_k']:.3f} | {r10['recall_at_k']:.3f} | {r10['mrr']:.3f} |")
    print(f"| 27 段（含干扰） | {sweep[1]['recall_at_k']:.3f} | {sweep[10]['recall_at_k']:.3f} | {sweep[10]['mrr']:.3f} |")
    print()
    # 那题在两套语料里各排第几 —— 这是全章最该看清的一个数字演变
    clean_rank = next(d["rank"] for d, q in zip(clean_det, QUESTIONS) if q["q"] == worst["q"])
    print(f"「{worst['q']}」：干净语料 rank={clean_rank} → 加干扰后 rank={worst['rank']}")
    print()
    check("干净语料上 Recall@1 仍不是 1.000（掉队不是干扰段造成的）",
          r1["recall_at_k"] < 1.0, f"{r1['recall_at_k']}")
    check("那题在干净语料上就已不在第 1（语义盲区先起作用）",
          clean_rank > 1, f"rank={clean_rank}")
    check("干扰段把它进一步推后（干扰放大语义盲区）",
          worst["rank"] > clean_rank, f"{clean_rank} → {worst['rank']}")
    check("加干扰后 MRR 下降（干扰段确实抬高了噪声）",
          r10["mrr"] > sweep[10]["mrr"], f"{r10['mrr']} → {sweep[10]['mrr']}")
    print()

    # ---------------- [5] 过滤 ----------------
    print("[5] metadata 过滤：category=refund")
    print()
    # 关键方法论点：过滤必须按类目切分题目集再评。
    # 用 12 道全量题去测单类过滤，9 道题本来就不属于该类，召回必然 0 —— 那不是过滤的错。
    cat_of = {c["id"]: c["category"] for c in ALL_CHUNKS}
    refund_idx = [i for i, q in enumerate(QUESTIONS) if cat_of[q["gold"]] == "refund"]
    cat_counts: dict[str, int] = {}
    for q in QUESTIONS:
        c = cat_of[q["gold"]]
        cat_counts[c] = cat_counts.get(c, 0) + 1
    print("  12 题按 gold 类目：" + ", ".join(f"{k}={v}" for k, v in cat_counts.items()))
    print()

    def eval_subset(idx: list[int], top_k: int, filters: dict | None) -> tuple[float, float, int]:
        # 一律按同一个 top_k 重新检索 —— 拿 wide（top_k=10）的结果当 top_k=3 用，
        # 「平均返回」会变成 10.00，把一个显而易见的口径错误藏进表里。
        sub = []
        for i in idx:
            hits = store.search(qvecs[i], top_k=top_k, filters=filters).hits
            sub.append({"q": QUESTIONS[i]["q"], "ranked": [h.id for h in hits],
                        "gold": [QUESTIONS[i]["gold"]], "count": len(hits)})
        r = evaluate(sub, top_k)
        return r["recall_at_k"], sum(x["count"] for x in sub) / len(sub), sum(1 for x in sub if x["count"] == 0)

    print("| 题目集 | 过滤 | Recall@3 | 平均返回 | 落空题数 |")
    print("| --- | --- | ---: | ---: | ---: |")
    all_idx = list(range(len(QUESTIONS)))
    for label, idx in (("全部 12 题", all_idx), ("refund 类 3 题", refund_idx)):
        for flt_label, flt in (("无", None), ("refund", {"category": "refund"})):
            rec, avg, empty = eval_subset(idx, 3, flt)
            print(f"| {label} | {flt_label} | {rec:.3f} | {avg:.2f} | {empty} |")
    print()

    rec_refund, avg_refund, empty_refund = eval_subset(refund_idx, 3, {"category": "refund"})
    rec_all, avg_all, empty_all = eval_subset(all_idx, 3, {"category": "refund"})
    rec_none, avg_none, _ = eval_subset(all_idx, 3, None)
    check("refund 类题目单独评：过滤一点召回都不掉", rec_refund == 1.0, f"{rec_refund}")
    check("全量题 + refund 过滤：召回从 0.917 暴跌到 0.250（9 道无关题全灭）",
          abs(rec_all - 0.25) < 5e-3, f"{rec_all}")
    check("但落空题数是 0 —— 过滤后照样返回满 3 条，只是内容全错",
          empty_all == 0 and avg_all == 3.0,
          f"平均返回 {avg_all:.2f}，落空 {empty_all}")
    check("所以「返回条数变少」不是过滤生效的判据（候选池 5 段 ≥ top_k 3）",
          avg_all == avg_none, f"{avg_none:.2f} → {avg_all:.2f}")
    check("过滤后结果只剩 refund 类",
          all(h.meta.get("category") == "refund"
              for qv in qvecs for h in store.search(qv, top_k=3, filters={"category": "refund"}).hits),
          "逐条核对 meta.category")
    print()

    # ---------------- [6] 暴力扫描成本 ----------------
    print("[6] 暴力扫描成本（2000 条 × 512 维）")
    print()
    big_db = os.path.join(tmp, "big.db")
    big = VectorStore(big_db)
    big.clear()
    import random
    rnd = random.Random(19)
    rows = []
    for i in range(2000):
        rows.append({
            "id": f"big:{i}", "source": "bench", "page": 1, "category": "bench",
            "text": f"压测文本 {i}",
            "vector": [rnd.uniform(-1, 1) for _ in range(e.dim)],
        })
    t0 = time.perf_counter()
    big.upsert(rows)
    insert_ms = (time.perf_counter() - t0) * 1000
    qv = [rnd.uniform(-1, 1) for _ in range(e.dim)]
    times = []
    for _ in range(5):
        t = time.perf_counter()
        big.search(qv, top_k=5)
        times.append((time.perf_counter() - t) * 1000)
    med = statistics.median(times)
    print(f"| 项目 | 值 |")
    print(f"| --- | ---: |")
    print(f"| 入库 2000 条 | {insert_ms:.0f} ms |")
    print(f"| 单次检索（中位数 / 5 次） | {med:.0f} ms |")
    print(f"| 单次检索（最快 / 最慢） | {min(times):.0f} / {max(times):.0f} ms |")
    print()
    check("2000×512 暴力检索在 20–500ms 量级（Python 循环是瓶颈）",
          20 <= med <= 500, f"{med:.0f} ms")
    print()

    # ---------------- [7] 分数分布 ----------------
    scores = [d["top_score"] for d in wide]
    print(f"[7] Top-1 分数分布：min={min(scores):.3f} max={max(scores):.3f} "
          f"中位={statistics.median(scores):.3f}")
    print()
    check("Top-1 分数全部挤在 0~0.6（哈希向量的分数空间很窄）",
          max(scores) < 0.6, f"max={max(scores):.3f}")
    print()

    print(f"结果：{len(FAILS)} 项不符" if FAILS else "结果：全部与文档一致")
    for f in FAILS:
        print(f"  - {f}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
