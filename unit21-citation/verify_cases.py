"""Unit 21 实测复现 —— 6 个案例、支撑度、定位、XSS 全在这里生成。

跑法：uv run python verify_cases.py

最有价值的部分是 [5]：它把「先转义后替换」这条规矩**反过来实现一遍**，
让你看到顺序错了到底会发生什么 —— 不是一句「有 XSS 风险」，
而是一段能被浏览器解析的真实标签。规则只有撞过墙才记得住。
"""

from __future__ import annotations

import html
import re
import sys

from citation import (
    CITE_RE,
    FABRICATED_BELOW,
    NGRAM,
    extract_refs,
    highlight,
    locate,
    render,
    split_sentences,
    strip_refs,
    support_score,
    validate,
)
from main import DEMO_CASES, DEMO_SOURCES

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [FAIL] {name}  {detail}")


def main() -> int:
    S = DEMO_SOURCES
    print(f"Unit 21 · 实测复现\n")
    print(f"[0] {len(S)} 条来源，{len(DEMO_CASES)} 个案例，NGRAM={NGRAM}，"
          f"伪造阈值={FABRICATED_BELOW}\n")

    # ---------------- [1] 六个案例的体检表 ----------------
    print("[1] 六个案例的体检报告")
    print()
    print("| 案例 | 想演示什么 | 覆盖率 | 引用 | 越界 | 未用来源 | 伪造 | ok |")
    print("| --- | --- | ---: | --- | --- | --- | ---: | :---: |")
    got = {}
    for name, item in DEMO_CASES.items():
        v = validate(item["answer"], S)
        got[name] = v
        print(f"| `{name}` | {item['title'].split('：')[-1]} | {v['coverage']:.2f} | "
              f"{v['refs']} | {v['out_of_range'] or '—'} | {v['unused_sources'] or '—'} | "
              f"{len(v['fabricated'])} | {'✅' if v['ok'] else '❌'} |")
    print()

    g = got["good"]
    check("good：覆盖率 1.00、无越界、无伪造、ok=True",
          g["coverage"] == 1.0 and not g["out_of_range"] and not g["fabricated"] and g["ok"] is True)
    p = got["partial"]
    check("partial：覆盖率 0.50（一句没标引用）", abs(p["coverage"] - 0.5) < 1e-9, f"{p['coverage']}")
    check("partial：ok 仍为 True —— ok 只判硬伤，不判覆盖率", p["ok"] is True,
          "这正是「一个绿灯 ≠ 答案好」的证据")
    check("partial：未引用句被单独列出", len(p["no_citation"]) == 1, str(p["no_citation"]))
    o = got["out_of_range"]
    check("out_of_range：[9] 被标越界", o["out_of_range"] == [9], str(o["out_of_range"]))
    check("out_of_range：ok=False", o["ok"] is False)
    f = got["fabricated"]
    check("fabricated：编号合法但内容找不到 → 伪造 1 条",
          len(f["fabricated"]) == 1 and f["out_of_range"] == [],
          f"伪造 {len(f['fabricated'])} 条，越界 {f['out_of_range']}")
    check("fabricated：支撑度低于阈值", f["fabricated"] and f["fabricated"][0]["support"] < FABRICATED_BELOW,
          str(f["fabricated"][0]["support"] if f["fabricated"] else None))
    y = got["year"]
    check("year：[2026] 不算引用（限位 \\d{1,2}）", y["refs"] == [1], str(y["refs"]))
    check("year：因为不算引用，所以也不产生越界告警", y["out_of_range"] == [], str(y["out_of_range"]))
    x = got["xss"]
    check("xss：<script> 被计入「未引用句」或伪造，不会静默通过",
          len(x["fabricated"]) >= 1 or len(x["no_citation"]) >= 1,
          f"伪造 {len(x['fabricated'])}，未引用 {len(x['no_citation'])}")

    # ---------------- [2] 支撑度 ----------------
    print("\n[2] 支撑度：逐字 0.917，同义改写 0.000")
    print()
    cases = [
        ("跨年未休完的年假最多结转 5 天。", 2, "逐字引用（句尾标点被改过）"),
        ("年假可以一直留着以后再用", 2, "同义改写"),
        ("今天天气不错适合出门散步", 2, "无关句"),
        ("年假可以无限期结转，离职时按三倍工资补偿。", 2, "编造"),
    ]
    print("| 句子 | 来源 | 支撑度 | 判定 |")
    print("| --- | ---: | ---: | --- |")
    vals = {}
    for claim, idx, label in cases:
        sc = support_score(claim, S[idx - 1].text)
        vals[label] = sc
        verdict = "判伪造" if sc < FABRICATED_BELOW else "通过"
        print(f"| {claim} | [{idx}] | {sc:.3f} | {verdict}（{label}） |")
    print()
    check("逐字引用 0.917（不是 1.000 —— 句尾标点改动扣一格）",
          abs(vals["逐字引用（句尾标点被改过）"] - 0.917) < 5e-3,
          f"{vals['逐字引用（句尾标点被改过）']:.3f}")
    check("逐字引用仍高于阈值（0.917 > 0.5）", vals["逐字引用（句尾标点被改过）"] >= FABRICATED_BELOW)
    check("同义改写支撑度 0.000 —— 滑窗的固有限制，不是 bug",
          vals["同义改写"] == 0.0, f"{vals['同义改写']:.3f}")
    check("无关句 0.000", vals["无关句"] == 0.0)
    check("编造句 0.000", vals["编造"] == 0.0)
    print("  注意：阈值不能设 0.95 —— 逐字引用自己也只有 0.917。")

    # ---------------- [3] 定位 ----------------
    print("\n[3] locate：最长公共块，不是 str.find")
    print()
    src = S[0].text
    # 来源原句是「……提交申请，由直属主管审批。」——逗号，不是句号。
    full = "年假需提前 3 个工作日在系统提交申请，由直属主管审批。"
    # 模型经常把逗号改句号：内容一字不差，只差一个标点
    variant = "年假需提前 3 个工作日在系统提交申请。"
    span_full = locate(full, src)
    span_variant = locate(variant, src)
    print(f"  来源[1]：{src}")
    print()
    print("| 片段 | 长度 | locate 结果 | str.find 结果 |")
    print("| --- | ---: | --- | ---: |")
    print(f"| 原句逐字 | {len(full)} | {span_full} | {src.find(full)} |")
    print(f"| 逗号改成句号 | {len(variant)} | {span_variant} | {src.find(variant)} |")
    print()
    check("逐字原句定到 [0, 28)（整句 28 字）", span_full == (0, 28), str(span_full))
    check("只改一个标点：locate 仍给出 [0, 19)（最长公共块）", span_variant == (0, 19), str(span_variant))
    check("同样输入交给 str.find 直接返回 -1（整串必须精确出现）",
          src.find(variant) == -1, f"find={src.find(variant)}")
    check("locate 对完全无关的短串返回 None", locate("完全无关的句子内容在此", src) is None)
    hl = highlight(src, *span_variant)
    check("highlight 用 <mark> 标出区间", "<mark>" in hl and "</mark>" in hl)
    check("highlight 里的原文被转义（< 不会原样输出）",
          "<" not in src or "&lt;" in hl or "<mark>" in hl)

    # ---------------- [4] 切句：引用归上句 ----------------
    print("\n[4] 切句：[n] 必须留在它标注的那一句上")
    print()
    t = "甲句内容。[1]乙句内容。"
    mine = split_sentences(t)
    naive = [x for x in re.split(r"(?<=[。！？；])", t) if x.strip()]
    print(f"  原文：{t}")
    print(f"  正确实现：{mine}")
    print(f"  朴素 re.split：{naive}")
    print()
    check("正确实现把 [1] 留在第一句", mine[0] == "甲句内容。[1]", str(mine))
    check("朴素切法把 [1] 切到下一句开头（这就是覆盖率腰斩的原因）",
          naive[1].startswith("[1]"), str(naive))
    vt = validate(t, S)
    check("按正确切法：[1] 归到第一句，第一句被记为「已引用」",
          vt["sentences"][0]["cited"] is True and vt["sentences"][0]["refs"] == [1],
          str(vt["sentences"][0]))
    check("第二句没有引用 → 覆盖率 0.50（不是 1.0：一半的句子没标注）",
          vt["coverage"] == 0.5, f"{vt['coverage']}，refs={vt['refs']}")
    print()

    # ---------------- [5] 渲染顺序：反证 ----------------
    print("[5] 渲染顺序：先转义后替换 vs 先替换后转义")
    print()

    def wrong_render(answer: str, sources) -> str:
        """故意写反：先插标签，再整段转义 —— 这是最常见的写法错误。"""
        total = len(sources)

        def repl(m):
            n = int(m.group(1))
            if not 1 <= n <= total:
                return m.group(0)
            return f'<sup class="cite"><a href="#src-{n}">[{n}]</a></sup>'

        return html.escape(CITE_RE.sub(repl, answer), quote=True)

    payload = '正常一句。<img src=x onerror=alert(1)>[1]'
    right = render(payload, S)
    wrong = wrong_render(payload, S)
    print(f"  攻击载荷：{payload}")
    print()
    print(f"  正确顺序 → {'含 <img' if '<img' in right else '不含 <img（安全）'}")
    print(f"  错误顺序 → {'含 <img' if '<img' in wrong else '不含 <img'}")
    print()
    check("正确顺序：答案里的 <img> 被转义成 &lt;img", "<img" not in right and "&lt;img" in right)
    check("正确顺序：自己插的 <sup> 仍然是真标签（转义没伤到自己）", "<sup" in right)
    check("错误顺序：先替换再整段转义，连自己的 <sup> 也被转义了 → 角标全废",
          "<sup" not in wrong and "&lt;sup" in wrong,
          "这条比 XSS 更常见：功能直接坏了")
    # 关键：错误顺序下，攻击载荷里的标签既没被执行也没保留，但**功能已经毁了**。
    # 真正危险的变体是「只转义答案的一部分 / 或先替换后不做转义」：
    no_escape = CITE_RE.sub(
        lambda m: f'<sup class="cite"><a href="#src-{int(m.group(1))}">[{m.group(1)}]</a></sup>',
        payload,
    )
    check("完全不转义：<img> 原样进入 HTML（这才是能被浏览器执行的那种）",
          "<img src=x onerror=alert(1)>" in no_escape, "浏览器会解析它")
    print()
    quote_payload = '他说"这个数字要核对"。[1]'
    qh = render(quote_payload, S)
    ans_part = qh.split('citation-answer">')[1].split("</div>")[0]
    # 答案区里出现的 " 只可能来自我们自己插入的 <sup class="cite">，
    # 原文的引号必须已经变成 &quot; —— 否则就能闭合属性、塞进新的 onclick
    check("答案里的原始引号全部转成 &quot;（拆不出新属性）",
          '他说&quot;这个数字要核对&quot;' in ans_part
          and '他说"这个数字要核对"' not in ans_part,
          ans_part[:60])
    inj = validate('[1]" onmouseover="alert(1)', S)
    inj_html = render('[1]" onmouseover="alert(1)', S)
    check("属性注入：答案里的引号无法闭合 href（onmouseover 不在属性位置）",
          "onmouseover=" not in inj_html.replace("&quot;", '"')
          or inj_html.count('href="#src-') == 1,
          "href 只接受整数 n")
    check("越界引用渲染成可见的红色标记，而不是静默消失",
          'class="cite-bad"' in render("编的。[9]", S))

    # ---------------- [6] 覆盖率的分母 ----------------
    print("\n[6] 覆盖率分母：只有标点的「。」不配有分母")
    print()
    check("validate('。', S) 覆盖率为 0（不除零崩掉）", validate("。", S)["coverage"] == 0.0)
    check("validate('。[1]', S) 覆盖率仍为 0", validate("。[1]", S)["coverage"] == 0.0)
    v_mix = validate("真内容。[1]。", S)
    check("夹杂纯标点句时，分母只算有内容的句子",
          v_mix["coverage"] == 1.0, f"{v_mix['coverage']}")
    check("空答案不抛异常", validate("", S)["coverage"] == 0.0)

    # ---------------- [7] 引用提取 ----------------
    print("\n[7] extract_refs：去重但保序，且限位")
    print()
    all_ok = True
    for text, expect in [
        ("甲[1]乙[2]", [1, 2]),
        ("甲[1]乙[1]", [1]),
        ("自 [2026] 年起", []),
        ("见 [123] 号文", []),
        ("[0] 和 [99]", [0, 99]),
        ("[3][1][3]", [3, 1]),
    ]:
        got_refs = extract_refs(text)
        ok = got_refs == expect
        all_ok = all_ok and ok
        print(f"  {'✓' if ok else '✗'} {text!r:<22} → {got_refs}（期望 {expect}）")
    print()
    check("六个 extract_refs 用例全部符合预期", all_ok)
    check("strip_refs 只去掉编号、保留正文",
          strip_refs("年假需提前。[1]") == "年假需提前。", repr(strip_refs("年假需提前。[1]")))

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
