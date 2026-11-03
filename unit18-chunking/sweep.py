"""Unit 18 参数扫描：chunk_size / overlap 怎么影响召回。

    uv run python sweep.py

`main.py` 的自检只验证"默认参数下三种策略都能跑"，这个脚本回答的是
README 里那句「把 size 拉到 400，recall 反而掉」到底成不成立。

输出四块：
  1. 策略 × chunk_size 的 recall@3
  2. 每块平均字数（递归策略的填充率代价）
  3. 语义阈值标定表（相邻句相似度分布 → 块数）
  4. 结论：每个策略的最佳档位
"""

from __future__ import annotations

import statistics

from chunker import STRATEGIES, chunk_document
from embedding import get_embedder
from main import DEMO_PAGES, QUESTIONS, _adjacent_sims, _recall

TOP_K = 3
SIZES = (100, 160, 240, 400, 800)


def evaluate(strategy: str, size: int, overlap: int, e, threshold: float = 0.05):
    """返回 (块数, recall@3 命中数, recall@3, 平均字数, recall@1)。

    加 recall@1 是因为 recall@3 太宽松：12 道题、块数只有几块时，
    前 3 块几乎总能兜住答案，区分度不够。recall@1 才看得出"排第一的准不准"。
    """
    chunks = chunk_document(DEMO_PAGES, strategy, size, overlap, threshold)
    hits = sum(_recall(chunks, q["q"], TOP_K, e)["hit"] for q in QUESTIONS)
    hits1 = sum(_recall(chunks, q["q"], 1, e)["hit"] for q in QUESTIONS)
    avg = sum(c.chars for c in chunks) / len(chunks) if chunks else 0
    return len(chunks), hits, hits / len(QUESTIONS), avg, hits1 / len(QUESTIONS)


def main() -> int:
    e = get_embedder()
    total_chars = sum(len(p) for p in DEMO_PAGES)

    print("=" * 78)
    print("Unit 18 · 切块参数扫描")
    print("=" * 78)
    print(f"语料：{len(DEMO_PAGES)} 页 / {total_chars} 字 / {len(QUESTIONS)} 道题")
    print(f"向量后端：{getattr(e, 'name', '?')}（{getattr(e, 'dim', '?')} 维，只认字面重叠）")
    print(f"口径：recall@{TOP_K}，命中 = 前 {TOP_K} 块里存在包含标准答案原文的块")

    print("\n[1] 策略 × chunk_size（overlap = size/5）")
    print()
    print("| 策略 | " + " | ".join(f"size={s}" for s in SIZES) + " |")
    print("| --- |" + " ---: |" * len(SIZES))
    best: dict[str, tuple[float, int]] = {}
    for s in STRATEGIES:
        cells = []
        for size in SIZES:
            n, hits, rec, _, _ = evaluate(s, size, max(0, size // 5), e)
            cells.append(f"{rec:.2f}（{hits}/{len(QUESTIONS)}，{n} 块）")
            if s not in best or rec > best[s][0]:
                best[s] = (rec, size)
        print(f"| {s} | " + " | ".join(cells) + " |")

    print("\n[2] 每块平均字数（递归策略为了在句末断，会牺牲填充率）")
    print()
    print("| 策略 | " + " | ".join(f"size={s}" for s in SIZES) + " |")
    print("| --- |" + " ---: |" * len(SIZES))
    for s in STRATEGIES:
        cells = []
        for size in SIZES:
            _, _, _, avg, _ = evaluate(s, size, max(0, size // 5), e)
            cells.append(f"{avg:.0f}")
        print(f"| {s} | " + " | ".join(cells) + " |")

    print("\n[2b] recall@1（更严格：排第一的块里必须就有答案）")
    print()
    print("| 策略 | " + " | ".join(f"size={s}" for s in SIZES) + " |")
    print("| --- |" + " ---: |" * len(SIZES))
    for s in STRATEGIES:
        cells = []
        for size in SIZES:
            _, _, _, _, r1 = evaluate(s, size, max(0, size // 5), e)
            cells.append(f"{r1:.2f}")
        print(f"| {s} | " + " | ".join(cells) + " |")

    print("\n[3] 语义阈值标定（相邻句相似度分布）")
    sims = _adjacent_sims(DEMO_PAGES, e)
    print(
        f"  样本 {len(sims)} 对 · 中位数 {statistics.median(sims):.3f} · "
        f"均值 {statistics.mean(sims):.3f} · 最大 {max(sims):.3f} · 最小 {min(sims):.3f}"
    )
    print()
    print("| 阈值 | 块数 | 平均字数 |")
    print("| ---: | ---: | ---: |")
    for th in (0.02, 0.05, 0.10, 0.20, 0.30):
        n, _, _, avg, _ = evaluate("semantic", 160, 30, e, th)
        print(f"| {th:.2f} | {n} | {avg:.0f} |")

    print("\n[4] 结论")
    for s, (rec, size) in best.items():
        print(f"  {s:10} 最佳 recall {rec:.2f} @ size={size}")
    print()
    print("  读第一张表：recall 并不是随着 size 变大一直上升。")
    print("  块变大确实能装下更完整的答案，但也会混进别的话题，向量被平均掉，")
    print("  于是某档之后开始掉——这就是「块大不等于好」的可测证据，不是玄学。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
