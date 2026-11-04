"""Unit 18 · 真评测：5 万字语料 × 80 题，三种切块策略 × 多档 size。

和 main.py 里 12 题的区别：
    12 题回答"哪个策略不差"，本评测回答"策略在难语料上差多少、差在哪类题"。
    变量全部放大：语料 5 万字（干扰项翻 16 倍）、74 题（三类）、size 扫 4 档。

题目三类，指标分开报（混在一起看不出来"为什么"）：
    fact         直接问法，答案串在语料里 → 记 recall@k
    para         同义改写问法，答案串不变 → 单独记 recall@k
                    （本地哈希 embedder 下这组偏低是正常现象——
                      它只认字面 bigram，测不出语义；切到 API embedder 才是真改写）
    unanswerable 语料里没有答案 → 检索仍会硬选 top-1，只能靠"top1 分数 < τ 拒答"
                    拒答 τ 不是拍的：对每档配置扫描 τ 网格，取准确率最高的点标定

用法：
    uv run python eval50.py                          # 默认 sizes 160,240,320,480
    uv run python eval50.py --sizes 160,240 --topk 3
    uv run python eval50.py --out eval_report.md     # 默认就写这个文件

输出：
    控制台逐配置进度 + 最终汇总表；eval_report.md 存完整报告（含丢题明细）。
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from collections import defaultdict
from pathlib import Path

import embedding as emb
from chunker import chunk_document
from eval_data import build

STRATEGIES = ("fixed", "recursive", "semantic")
DEFAULT_SIZES = (160, 240, 320, 480)
TAU_GRID = [round(0.05 * i, 2) for i in range(1, 19)]  # 0.05 ~ 0.90


def _order_by_sim(qv: list[float], cvecs: list[list[float]]) -> list[int]:
    """按与查询的余弦相似度降序返回 chunk 下标。"""
    sims = [emb.cosine_similarity(qv, cv) for cv in cvecs]
    return sorted(range(len(sims)), key=lambda i: -sims[i]), sims


def _answer_result(order: list[int], chunks, expected: str, top_k: int, sims: list[float]) -> dict:
    """答案题：前 top_k 里是否有包含答案串的块。附带真排名便于分析丢题原因。"""
    hit_rank = None
    for rank, idx in enumerate(order, 1):
        if expected in chunks[idx].text:
            hit_rank = rank
            break
    top1 = sims[order[0]]
    return {
        "hit": hit_rank is not None and hit_rank <= top_k,
        "rank": hit_rank,          # 答案所在块的真实排名（None = 全库都搜不到）
        "top1": top1,
    }


def evaluate_config(pages, questions, qvecs, strategy: str, size: int, overlap: int, top_k: int, embedder) -> dict:
    """跑一个 (策略, size) 配置，返回该配置的全部指标。"""
    chunks = chunk_document(pages, strategy, size, overlap)
    cvecs = asyncio.run(embedder.embed([c.text for c in chunks]))

    per_q = []
    for q, qv in zip(questions, qvecs):
        order, sims = _order_by_sim(qv, cvecs)
        if q["expected"] is not None:
            r = _answer_result(order, chunks, q["expected"], top_k, sims)
            per_q.append({**q, **r})
        else:
            per_q.append({**q, "hit": None, "rank": None, "top1": sims[order[0]]})

    def recall(cat: str, k: int) -> float:
        sub = [x for x in per_q if x["category"] == cat]
        return sum(1 for x in sub if x["hit"]) / len(sub) if sub else 0.0

    ans_top1 = [x["top1"] for x in per_q if x["category"] != "unanswerable"]
    unans_top1 = [x["top1"] for x in per_q if x["category"] == "unanswerable"]

    # 拒答阈值标定：答案题 top1≥τ 判"接受"，无答案题 top1<τ 判"拒答"，两件事都对才算分
    def _correct(x, tau: float) -> bool:
        if x["category"] == "unanswerable":
            return x["top1"] < tau      # 该拒答的，分数低于阈值 → 判对了
        return x["top1"] >= tau         # 该回答的，分数高于阈值 → 判对了

    best = max(
        ((tau, sum(1 for x in per_q if _correct(x, tau)) / len(per_q)) for tau in TAU_GRID),
        key=lambda t: t[1],
    )

    return {
        "strategy": strategy,
        "size": size,
        "chunks": len(chunks),
        "avg_chars": round(sum(c.chars for c in chunks) / len(chunks), 1) if chunks else 0,
        "fact_rk": recall("fact", top_k),
        "para_rk": recall("para", top_k),
        "_per_q": per_q,
        "_ans_top1": ans_top1,
        "_unans_top1": unans_top1,
        "best_tau": best[0],
        "refusal_acc": round(best[1], 3),
        "unans_median": round(statistics.median(unans_top1), 4) if unans_top1 else None,
        "ans_median": round(statistics.median(ans_top1), 4) if ans_top1 else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", default=",".join(map(str, DEFAULT_SIZES)))
    ap.add_argument("--overlap-ratio", type=float, default=0.2, help="overlap = size * ratio")
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--out", default="eval_report.md")
    args = ap.parse_args()
    sizes = [int(s) for s in args.sizes.split(",")]

    data = build()
    pages, questions = data["pages"], data["questions"]
    embedder = emb.get_embedder()
    print(f"语料 {data['stats']['chars']} 字 / {data['stats']['pages']} 篇；题目 {len(questions)} 道")
    print(f"embedder: {embedder.name}（local-hashing 只认字面重叠，para 组偏低是预期行为）")
    print(f"配置：strategy × size={sizes}，overlap=size×{args.overlap_ratio}，top_k={args.topk}\n")

    qvecs = asyncio.run(embedder.embed([q["q"] for q in questions]))

    rows = []
    for strategy in STRATEGIES:
        for size in sizes:
            overlap = max(1, int(size * args.overlap_ratio))
            r = evaluate_config(pages, questions, qvecs, strategy, size, overlap, args.topk, embedder)
            # overall = fact 与 para 合并算（60 道答案题）
            ans = [x for x in r["_per_q"] if x["category"] != "unanswerable"]
            r["overall_rk"] = round(sum(1 for x in ans if x["hit"]) / len(ans), 3)
            r["fact_rk"] = round(r["fact_rk"], 3)
            r["para_rk"] = round(r["para_rk"], 3)
            r["overlap"] = overlap
            rows.append(r)
            print(
                f"[{strategy:9s} size={size:4d}] 块 {r['chunks']:4d} | "
                f"fact {r['fact_rk']:.2f} para {r['para_rk']:.2f} 总 {r['overall_rk']:.2f} | "
                f"无答案 top1 中位 {r['unans_median']} | 拒答 τ*={r['best_tau']} acc {r['refusal_acc']}"
            )

    _write_report(rows, data, embedder, args, Path(args.out))
    print(f"\n报告已写入 {args.out}")


def _write_report(rows, data, embedder, args, path: Path) -> None:
    top_k = args.topk
    lines = [
        "# Unit 18 · 真评测报告（5 万字语料 × 80 题）",
        "",
        f"- embedder：`{embedder.name}`（local-hashing 只反映字面 bigram 重叠）",
        f"- 语料：{data['stats']['chars']} 字 / {data['stats']['pages']} 篇；题目 {len(data['questions'])} 道"
        f"（fact 33 / para 33 / unanswerable 14，动态生成）",
        f"- 参数：overlap = size × {args.overlap_ratio}，top_k = {top_k}，semantic threshold = 默认",
        "",
        "## 主表",
        "",
        "| 策略 | size | 块数 | 平均块长 | fact r@k | para r@k | 总 r@k | 无答案 top1 中位 | 答案题 top1 中位 | 最佳拒答阈值 τ* | 拒答准确率 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for r in rows:
        lines.append(
            f"| {r['strategy']} | {r['size']} | {r['chunks']} | {r['avg_chars']} | "
            f"{r['fact_rk']:.3f} | {r['para_rk']:.3f} | {r['overall_rk']:.3f} | "
            f"{r['unans_median']} | {r['ans_median']} | {r['best_tau']} | {r['refusal_acc']} |"
        )

    # 丢题明细：取第一个配置作代表（也可全量展开，报告会太长）
    # 代表配置：优先 recursive + 中等 size；只跑了 size 子集时退化为第一行。
    # （原来写死 size=240，用 --sizes 160 跑子集时会 StopIteration 崩掉）
    sizes = sorted({r["size"] for r in rows})
    rep_size = sizes[len(sizes) // 2]
    rep = next(
        (r for r in rows if r["strategy"] == "recursive" and r["size"] == rep_size),
        rows[0],
    )
    lines += [
        "",
        f"## 丢题明细（{rep['strategy']} / size={rep['size']} 为代表，r@{top_k} 口径）",
        "",
        "| 类别 | 问题 | 答案块真实排名 | top1 分数 |",
        "| --- | --- | ---: | ---: |",
    ]
    for x in rep["_per_q"]:
        if x["category"] == "unanswerable":
            continue
        if not x["hit"]:
            rank = x["rank"] if x["rank"] is not None else "不在库里（切块把答案切碎了）"
            lines.append(f"| {x['category']} | {x['q']} | {rank} | {x['top1']:.4f} |")
    if not any(not x["hit"] for x in rep["_per_q"] if x["category"] != "unanswerable"):
        lines.append("| （无） | 该配置下 60 道答案题全部命中 | | |")

    # 无答案题的 top1 分布：验证"拒答阈值"为什么可标定
    lines += [
        "",
        f"## 无答案题 top1 分布（{rep['strategy']} / size={rep['size']}）",
        "",
        "| 问题 | top1 分数 |",
        "| --- | ---: |",
    ]
    for x in sorted(rep["_per_q"], key=lambda x: -(x["top1"] if x["category"] == "unanswerable" else -9)):
        if x["category"] == "unanswerable":
            lines.append(f"| {x['q']} | {x['top1']:.4f} |")

    lines += [
        "",
        "## 怎么读这份报告",
        "",
        "1. **fact vs para 的落差** = 问法鲁棒性。哈希 embedder 下落差大是词袋本性；换真 embedding 后差距应收窄——收窄不了的就是切块在丢上下文。",
        "2. **无答案 top1 中位 vs 答案题 top1 中位** = 两团分数分得越开，拒答阈值越好定。τ* 就是网格上准确率最高的切点。",
        "3. **块数** = 世界的成本。recall 差不多时选块少的（省 embedding 和存储）。",
        "4. 12 题小评测的结论在这份报告里都可能翻盘——这正是把语料和题量做大的意义。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
