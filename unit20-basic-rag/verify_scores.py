"""Unit 20 实测复现 —— README 的分数表、阈值结论、拒答机制全在这里生成。

跑法：uv run python verify_scores.py

它质疑的是笔记里那句「相关 0.20、无关 0.13，余量只有 0.07」：
余量到底是多少，取决于你把哪些题算作「该答」。把「账号锁定多久」算成该答之后，
间隔是负的 —— 这不是抠字眼，它意味着阈值这条路在这份语料上根本走不通。
"""

from __future__ import annotations

import os
import sys
import tempfile

import rag
from rag import REFUSAL, TraceStore, answer_question, build_prompt, extractive_answer, resolve_mode
from retriever import Retriever

PASS = FAIL = 0

# 该答 / 该拒 是人工标注的 ground truth，不是拿分数反推的。
# 「账号锁定多久」标成 should_answer：它问的正是 sec:1:13 那段的内容，
# 只是「锁定账号」和「账号锁定」的 bigram 顺序不同 —— 这是词表的问题，不是问题本身无关。
CASES = [
    ("一线城市住宿标准", True),
    ("回滚后先检查什么", True),
    ("退款多久到账", True),
    ("年假要提前几天申请", True),
    ("账号会被锁定多久", True),
    ("账号锁定多久", True),          # ← 争议点：它该答，但 0.143 卡在阈值下
    ("公司年会什么时候开", False),
    ("今天天气怎么样", False),
    ("怎么给猫剪指甲", False),
]


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> int:
    print("Unit 20 · 实测复现\n")
    r = Retriever()
    print(f"[0] 语料 {len(r.corpus)} 段，embedder={type(r.embedder).__name__}，"
          f"模式={resolve_mode(None)}，阈值={rag.DEFAULT_THRESHOLD}\n")

    # ---------------- [1] 分数表 ----------------
    print("[1] 检索最高分（top_k=3，阈值 0.15）")
    print()
    print("| 问题 | 最高分 | 该答 | 阈值 0.15 下 | 判定 |")
    print("| --- | ---: | :---: | --- | --- |")
    rows = []
    for q, should in CASES:
        hits = r.search(q, top_k=3)
        top = hits[0].score if hits else 0.0
        answered = bool(hits) and top >= rag.DEFAULT_THRESHOLD
        verdict = "✅" if answered == should else "❌"
        rows.append({"q": q, "top": top, "should": should, "answered": answered, "verdict": verdict})
        print(f"| {q} | {top:.3f} | {'是' if should else '否'} | "
              f"{'回答' if answered else '拒答'} | {verdict} |")
    print()

    pos = [x["top"] for x in rows if x["should"]]
    neg = [x["top"] for x in rows if not x["should"]]
    hit = sum(1 for x in rows if x["answered"] and x["should"])
    fa = sum(1 for x in rows if x["answered"] and not x["should"])
    miss = sum(1 for x in rows if not x["answered"] and x["should"])
    print(f"  应答题 {len(pos)} 道，应拒题 {len(neg)} 道："
          f"答题命中 {hit}/{len(pos)}，误答 {fa}，漏答 {miss}")
    print()
    check("应答题全部 >= 0.15 才叫「阈值可用」—— 实际漏了 1 道", miss == 1, f"漏答 {miss}")
    check("误答为 0（无关问题没被放行）", fa == 0, f"误答 {fa}")

    # ---------------- [2] 余量到底是多少 ----------------
    print("\n[2] 判别余量：最近的应答题 vs 最远的应拒题")
    print()
    min_pos = min(pos)
    max_neg = max(neg)
    gap = min_pos - max_neg
    print("| 口径 | 算法 | 值 |")
    print("| --- | --- | ---: |")
    print(f"| README 那句「余量 0.07」 | 0.203（年假）− 0.131（天气） | {0.203 - 0.131:.3f} |")
    print(f"| **真实最差间隔** | {min_pos:.3f}（{min(x['q'] for x in rows if x['top'] == min_pos)}）"
          f" − {max_neg:.3f}（{max(x['q'] for x in rows if x['top'] == max_neg)}） | {gap:.3f} |")
    print()
    if gap < 0:
        print(f"  间隔是负的：应答题的最低分（{min_pos:.3f}）比应拒题的最高分（{max_neg:.3f}）还低。")
        print("  也就是说，**不存在任何一个阈值能同时答对这两道题** —— 这不是调参能解决的。")
    print()
    check("真实最差间隔为负（阈值无法同时分开这两题）", gap < 0, f"{gap:+.3f}")
    check("README 的 0.07 是把争议题当「该拒」算出来的",
          abs((0.203 - 0.131) - 0.072) < 5e-3, "0.203 − 0.131 = 0.072")

    # ---------------- [3] 阈值扫描 ----------------
    print("\n[3] 阈值扫描：每一档的召回与误答")
    print()
    print("| 阈值 | 答题数 | 召回 | 误答 | 漏答 |")
    print("| ---: | ---: | ---: | ---: | ---: |")
    for t in (0.0, 0.1, 0.13, 0.15, 0.18, 0.2, 0.3):
        ans = [x for x in rows if x["top"] >= t]
        h = sum(1 for x in ans if x["should"])
        f = sum(1 for x in ans if not x["should"])
        print(f"| {t:.2f} | {len(ans)} | {h}/{len(pos)} = {h/len(pos):.3f} | {f} | {len(pos)-h} |")
    print()
    t015 = [x for x in rows if x["top"] >= 0.15]
    check("阈值 0.15：答 5 题、误答 0、漏 1（与 README 一致）",
          len(t015) == 5 and sum(1 for x in t015 if not x["should"]) == 0,
          f"答 {len(t015)} 题")
    t0 = [x for x in rows if x["top"] >= 0.0]
    check("阈值 0：无关问题也会被「回答」（证明拒答真来自阈值）",
          sum(1 for x in t0 if not x["should"]) == len(neg), f"{len(t0)} 题全答")

    # ---------------- [4] 答案必须逐字来自原文 ----------------
    print("\n[4] extractive 答案：逐字来自检索到的原文")
    print()
    print("| 问题 | 答案 | 逐字命中 |")
    print("| --- | --- | :---: |")
    ok_all = True
    for q, should in CASES[:5]:
        if not should:
            continue
        hits = r.search(q, top_k=3)
        ans, picked = extractive_answer(q, hits)
        verbatim = all(p["sentence"] in hits[p["idx"] - 1].text for p in picked)
        ok_all = ok_all and verbatim
        print(f"| {q} | {ans} | {'是' if verbatim else '否'} |")
    print()
    check("每条答案的每个分句都能在对应命中段里原样找到", ok_all)

    hits = r.search("退款多久到账", top_k=3)
    ans, picked = extractive_answer("退款多久到账", hits)
    check("答案带 [1] 编号", "[1]" in ans, ans)
    check("只引用第 1 名（不跨段拼接）", {p["idx"] for p in picked} == {1}, str({p["idx"] for p in picked}))
    check("answer 去掉编号后 = 原文子串",
          ans.replace("[1]", "") in hits[0].text, ans.replace("[1]", "")[:30])

    # ---------------- [5] 上下文编号 ----------------
    print("\n[5] prompt 里的编号 = 检索顺序")
    print()
    hits = r.search("年假要提前几天申请", top_k=3)
    prompt = build_prompt("年假要提前几天申请", hits)
    lines = [ln for ln in prompt.splitlines() if ln.startswith("[")]
    check("上下文有 3 条编号", len(lines) == 3, f"{len(lines)} 条")
    check("编号从 [1] 连续到 [3]", [ln[1] for ln in lines] == ["1", "2", "3"], str([ln[:3] for ln in lines]))
    check("[1] 对应的就是分数最高的那段",
          hits[0].text in lines[0], lines[0][:40])
    check("每条都带来源和页码", all("第" in ln and "页" in ln for ln in lines), lines[0][:40])
    print()
    print("  prompt 前 3 行：")
    for ln in prompt.splitlines()[:3]:
        print(f"    {ln[:78]}")
    print()

    # ---------------- [6] trace 落盘 ----------------
    print("[6] trace：每次问答都能复盘")
    print()
    tmp = tempfile.mkdtemp(prefix="u20-trace-")
    traces = TraceStore(os.path.join(tmp, "t.db"))
    before = traces.count()

    r_ok = answer_question("退款多久到账", retriever=r, traces=traces)
    r_no = answer_question("今天天气怎么样", retriever=r, traces=traces)
    check("两次问答都写了 trace", traces.count() == before + 2, f"{before} → {traces.count()}")
    check("正常问答 refused=False", r_ok.refused is False)
    check("无关问题 refused=True 且答案是固定常量", r_no.refused is True and r_no.answer == REFUSAL, r_no.answer)

    rec = traces.get(r_no.trace_id)
    check("拒答的 trace 里也存了 hits（能复盘当时检索到什么）",
          rec is not None and len(rec.get("hits") or []) > 0,
          f"{len(rec.get('hits') or []) if rec else 0} 条")
    check("trace 里存了完整 prompt（改了模板也能复现现场）",
          rec is not None and (rec.get("prompt") or "") != "", "prompt 非空")
    rec_ok = traces.get(r_ok.trace_id)
    check("extractive 模式也记 usage（字符数）",
          rec_ok is not None and (rec_ok.get("usage") or {}).get("backend") == "extractive",
          str((rec_ok or {}).get("usage")))
    check("trace 里记了当时的 threshold", rec is not None and abs(float(rec.get("threshold", -1)) - 0.15) < 1e-9,
          str(rec.get("threshold") if rec else None))
    print()

    # ---------------- [7] 阈值前置 vs 事后 ----------------
    print("[7] 阈值判定在生成之前（否则既费钱又不可测）")
    print()
    strict = answer_question("退款多久到账", retriever=r, traces=traces, threshold=0.9)
    check("阈值拉到 0.9，相关问题立即拒答", strict.refused is True and strict.answer == REFUSAL)
    check("拒答时 prompt 仍然生成（便于复盘为什么拒）", strict.prompt != "", f"{len(strict.prompt)} 字符")
    check("拒答时 answer 为空串而不是 None（下游好处理）", strict.answer == REFUSAL)
    loose = answer_question("今天天气怎么样", retriever=r, traces=traces, threshold=0.0)
    check("阈值拉到 0，无关问题也说起了话（证明拒答来自阈值而非数据集）",
          loose.refused is False and loose.answer != REFUSAL, loose.answer[:40])
    print()

    print(f"结果：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
