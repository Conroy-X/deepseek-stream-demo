"""Unit 21 · 引用校验 —— [n] 不是排版，是可核查性。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8021
    打开 http://127.0.0.1:8021/

自检（离线，不需要模型）：
    uv run python main.py
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from citation import (
    CITE_RE,
    FABRICATED_BELOW,
    NGRAM,
    Source,
    extract_refs,
    highlight,
    locate,
    render,
    split_sentences,
    strip_refs,
    support_score,
    validate,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 21 · 引用校验", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

DEMO_SOURCES = [
    Source(1, "年假需提前 3 个工作日在系统提交申请，由直属主管审批。入职满一年享 5 天年假。",
           "handbook.md", 1),
    Source(2, "跨年未休完的年假最多结转 5 天，结转部分须在次年 3 月底前用完。", "handbook.md", 1),
    Source(3, "退款金额原路退回，到账时间以支付渠道为准，通常 3–5 个工作日。", "policy.md", 2),
    Source(4, "连续 5 次登录失败锁定账号 30 分钟。密码要求 12 位以上，每 90 天更换一次。",
           "sec.md", 1),
]

DEMO_CASES = {
    "good": {
        "title": "正常：三句都带引用",
        "answer": "年假需提前 3 个工作日在系统提交申请，由直属主管审批。[1]"
                  "跨年未休完的年假最多结转 5 天。[2]"
                  "退款金额原路退回，到账时间以支付渠道为准，通常 3–5 个工作日。[3]",
    },
    "partial": {
        "title": "部分覆盖：第二句没标引用",
        "answer": "年假需提前 3 个工作日在系统提交申请，由直属主管审批。[1]"
                  "年假天数随司龄增长。",
    },
    "out_of_range": {
        "title": "越界：[9] 但只有 4 条来源",
        "answer": "年假需提前 3 个工作日在系统提交申请，由直属主管审批。[1]"
                  "具体细则见附录说明。[9]",
    },
    "fabricated": {
        "title": "伪造：编号对，内容来源里没有",
        "answer": "年假可以无限期结转，离职时按三倍工资补偿。[2]",
    },
    "year": {
        "title": "年份：[2026] 不是引用",
        "answer": "年假需提前 3 个工作日在系统提交申请（[2026] 版规定）。[1]",
    },
    "xss": {
        "title": "注入：答案里带 script 标签",
        "answer": "<img src=x onerror=alert(1)>年假需提前 3 个工作日在系统提交申请。[1]"
                  "<script>alert('xss')</script>",
    },
}


def _sources(payload: list[dict] | None) -> list[Source]:
    if not payload:
        return DEMO_SOURCES
    out = []
    for i, s in enumerate(payload, 1):
        text = (s.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail=f"第 {i} 条来源 text 为空")
        out.append(Source(i, text, str(s.get("source") or "unknown.md"), int(s.get("page") or 1)))
    return out


class CheckIn(BaseModel):
    answer: str = Field(min_length=1, max_length=8000)
    sources: list[dict] | None = None


class LocateIn(BaseModel):
    snippet: str = Field(min_length=1, max_length=2000)
    idx: int = Field(ge=1)


@app.get("/health", summary="来源条数、判定参数、内置案例")
def health() -> dict:
    return {
        "ok": True,
        "sources": len(DEMO_SOURCES),
        "demo_cases": sorted(DEMO_CASES),
        "ngram": NGRAM,
        "fabricated_below": FABRICATED_BELOW,
        "cite_pattern": CITE_RE.pattern,
        "note": "只认 1-2 位数字，[2026] 不会被当成引用",
    }


@app.post("/check", summary="校验一份答案：越界 / 覆盖率 / 伪造 / 未用来源")
def check(req: CheckIn) -> dict:
    return validate(req.answer, _sources(req.sources))


@app.post("/render", summary="把 [n] 渲染成可点击的 sup（先转义再替换）")
def render_route(req: CheckIn) -> dict:
    sources = _sources(req.sources)
    return {"html": render(req.answer, sources), "report": validate(req.answer, sources)}


@app.get("/demo", summary="跑一个内置案例")
def demo(case: str = Query(default="good")) -> dict:
    if case not in DEMO_CASES:
        raise HTTPException(status_code=404, detail=f"没有这个案例，可选：{sorted(DEMO_CASES)}")
    item = DEMO_CASES[case]
    return {
        "case": case,
        "title": item["title"],
        "answer": item["answer"],
        "report": validate(item["answer"], DEMO_SOURCES),
        "html": render(item["answer"], DEMO_SOURCES),
    }


@app.post("/locate", summary="把某句回答定位回来源原文，返回高亮片段")
def locate_route(req: LocateIn) -> dict:
    if not 1 <= req.idx <= len(DEMO_SOURCES):
        raise HTTPException(status_code=404, detail=f"没有第 {req.idx} 条来源")
    src = DEMO_SOURCES[req.idx - 1]
    span = locate(req.snippet, src.text)
    if span is None:
        return {
            "found": False,
            "idx": req.idx,
            "support": round(support_score(req.snippet, src.text), 3),
            "html": f'<del>{src.text}</del>',
            "text": src.text,
        }
    start, end = span
    return {
        "found": True,
        "idx": req.idx,
        "start": start,
        "end": end,
        "matched": src.text[start:end],
        "support": round(support_score(req.snippet, src.text), 3),
        "html": highlight(src.text, start, end),
        "source": src.source,
        "page": src.page,
    }


@app.get("/sources", summary="内置来源原文")
def sources() -> dict:
    return {"count": len(DEMO_SOURCES), "items": [s.to_dict() for s in DEMO_SOURCES]}


@app.get("/", include_in_schema=False)
def index():
    if os.path.exists(STATIC_INDEX):
        return FileResponse(STATIC_INDEX)
    return {"hint": "static/index.html 不存在，看 /docs"}


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 21 · 引用校验自检（离线）\n")
    S = DEMO_SOURCES

    print("  —— 解析：什么才算引用 ——")
    check("[1] 能解析", extract_refs("年假[1]") == [1], str(extract_refs("年假[1]")))
    check("[1][2] 解析成两个", extract_refs("甲[1]乙[2]") == [1, 2], str(extract_refs("甲[1]乙[2]")))
    check("连续 [1][2] 也解析", extract_refs("结论。[1][2]") == [1, 2], str(extract_refs("结论。[1][2]")))
    check("[2026] 不被当成引用", extract_refs("自 [2026] 年起") == [], str(extract_refs("自 [2026] 年起")))
    check("[123] 三位数也不是引用", extract_refs("见 [123] 号文") == [], str(extract_refs("见 [123] 号文")))
    check("重复引用去重", extract_refs("甲[1]乙[1]") == [1], str(extract_refs("甲[1]乙[1]")))
    check("顺序按出现保留", extract_refs("甲[2]乙[1]") == [2, 1], str(extract_refs("甲[2]乙[1]")))
    check("去掉引用后剩纯文本", strip_refs("结论。[1][2]") == "结论。", strip_refs("结论。[1][2]"))
    check("英文方括号数字同样识别", extract_refs("see [3]") == [3])

    print("\n  —— 覆盖率：分母是有内容的句子 ——")
    v = validate("甲句。[1]乙句。丙句。[2]", S)
    check("3 句里 2 句带引用 → 0.667", v["coverage"] == 0.6667, str(v["coverage"]))
    check("无引用的句子被列出来", len(v["no_citation"]) == 1, str(v["no_citation"]))
    check("句号后的 [1] 归到上一句（不是下一句）",
          split_sentences("甲句。[1]乙句。") == ["甲句。[1]", "乙句。"],
          str(split_sentences("甲句。[1]乙句。")))
    check("全部带引用 → 1.0", validate("甲。[1]乙。[2]", S)["coverage"] == 1.0)
    check("完全没引用 → 0.0", validate("甲。乙。", S)["coverage"] == 0.0)
    check("空答案不报错、覆盖率 0", validate("", S)["coverage"] == 0.0)
    check("只有标点的句子不计入分母", validate("。[1]", S)["coverage"] == 0.0)

    print("\n  —— 越界与未用来源 ——")
    v = validate("结论。[1][9]", S)
    check("[9] 被标为越界", v["out_of_range"] == [9], str(v["out_of_range"]))
    check("[0] 也被标为越界（编号从 1 开始）", validate("结论。[0]", S)["out_of_range"] == [0])
    check("越界时 ok = False", v["ok"] is False)
    check("未使用的来源被列出来", v["unused_sources"] == [2, 3, 4], str(v["unused_sources"]))
    check("只用了一条时 ok 仍可为 True", validate("年假需提前 3 个工作日在系统提交申请。[1]", S)["ok"] is True)

    print("\n  —— 定位：答案能不能回到原文 ——")
    span = locate("年假需提前 3 个工作日在系统提交申请，由直属主管审批。[1]", S[0].text)
    check("能定位到来源", span is not None, str(span))
    check("定位区间内就是原文",
          span is not None and S[0].text[span[0]:span[1]] == "年假需提前 3 个工作日在系统提交申请，由直属主管审批。",
          S[0].text[span[0]:span[1]] if span else "")
    check("定位失败返回 None", locate("今天天气不错适合出门散步", S[0].text) is None)
    check("改动几个字仍能定位到最接近的一段",
          locate("年假需提前 3 个工作日在系统提交申请", S[0].text) is not None)
    check("空片段返回 None", locate("", S[0].text) is None)

    print("\n  —— 伪造引用：编号对、内容错 ——")
    fake = "年假可以无限期结转，离职时按三倍工资补偿。[2]"
    vf = validate(fake, S)
    check("伪造引用被检出", len(vf["fabricated"]) == 1, str(vf["fabricated"]))
    check("伪造的支撑度低于阈值", vf["fabricated"][0]["support"] < FABRICATED_BELOW,
          str(vf["fabricated"][0]["support"]))
    check("伪造时 ok = False", vf["ok"] is False)
    good = validate("跨年未休完的年假最多结转 5 天。[2]", S)
    check("真实引用不会误报", good["fabricated"] == [], str(good["fabricated"]))
    # 逐字引用也拿不到 1.0：句尾的「。」在来源里是「，」，少了一个窗口
    check("逐字引用的支撑度 >= 0.9", good["sentences"][0]["support"] >= 0.9,
          str(good["sentences"][0]["support"]))
    check("支撑度：同义改写会掉到阈值以下",
          support_score("年假可以一直留着以后再用", S[1].text) < FABRICATED_BELOW,
          str(round(support_score("年假可以一直留着以后再用", S[1].text), 3)))

    print("\n  —— 渲染：先转义再替换 ——")
    h = render("结论。[1]", S)
    check("[1] 变成可点击 sup", '<sup class="cite"><a href="#src-1"' in h, h[:80])
    check("来源列表带 id 锚点", 'id="src-1"' in h)
    check("越界引用不生成链接", 'class="cite-bad"' in render("结论。[9]", S))
    check("越界引用保留了原始文本", "[9]" in render("结论。[9]", S))

    xss_ans = "<script>alert('xss')</script>年假需提前 3 个工作日。[1]"
    hx = render(xss_ans, S)
    check("script 标签被转义", "<script>" not in hx and "&lt;script&gt;" in hx, hx[:60])
    check("onerror 之类属性也进不了 HTML", "<img" not in hx)
    check("来源文本里的尖括号同样被转义",
          "<script>" not in render("结论。[1]", [Source(1, "<script>evil()</script>")]))
    check("转义后的 [1] 仍然能变成 sup（替换发生在转义之后）",
          '<a href="#src-1"' in render("<b>[1]</b>", S))
    hijack = render('[1]" onmouseover="alert(1)', S)
    check("答案里的引号被转义成 &quot;（拆不出新属性）",
          '" onmouseover="' not in hijack and "&quot; onmouseover=&quot;" in hijack, hijack[:140])

    hh = highlight("abcdef", 2, 4)
    check("高亮只包住命中区间", hh == "ab<mark>cd</mark>ef", hh)
    check("越界区间不高亮但也不崩", highlight("abc", 5, 9) == "abc")
    check("高亮前会转义", "&lt;" in highlight("<x>", 0, 1))

    print("\n  —— 内置案例走一遍 ——")
    for key, item in DEMO_CASES.items():
        rep = validate(item["answer"], S)
        print(f"       {key:12s} 覆盖 {rep['coverage']:.2f} · 引用 {rep['refs']} · "
              f"越界 {rep['out_of_range']} · 伪造 {len(rep['fabricated'])} · ok={rep['ok']}")
    check("good 案例全绿", validate(DEMO_CASES["good"]["answer"], S)["ok"] is True)
    check("partial 案例覆盖率 0.5", validate(DEMO_CASES["partial"]["answer"], S)["coverage"] == 0.5)
    check("out_of_range 案例能抓到 9", validate(DEMO_CASES["out_of_range"]["answer"], S)["out_of_range"] == [9])
    check("fabricated 案例能抓到 1 条", len(validate(DEMO_CASES["fabricated"]["answer"], S)["fabricated"]) == 1)
    check("year 案例的引用只有 [1]", validate(DEMO_CASES["year"]["answer"], S)["refs"] == [1],
          str(validate(DEMO_CASES["year"]["answer"], S)["refs"]))
    check("xss 案例渲染后没有可执行标签", "<img" not in render(DEMO_CASES["xss"]["answer"], S))

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    check("GET /health 返回案例与参数", r.status_code == 200 and len(r.json()["demo_cases"]) == 6)
    check("/health 暴露引用正则（能看出只认 1-2 位）", "1,2" in r.json()["cite_pattern"])

    r = client.post("/check", json={"answer": "年假需提前 3 个工作日。[1]另见附录。[9]"})
    check("POST /check 返回越界", r.status_code == 200 and r.json()["out_of_range"] == [9])
    check("/check 返回逐句明细", len(r.json()["sentences"]) == 2)

    r = client.post("/check", json={"answer": "结论。[1]", "sources": [{"text": "结论在这里。", "page": 3}]})
    check("POST /check 支持自定义来源", r.status_code == 200 and r.json()["sources"] == 1)

    r = client.post("/render", json={"answer": "年假需提前 3 个工作日。[1]"})
    check("POST /render 返回 HTML 片段", r.status_code == 200 and "<sup" in r.json()["html"])
    check("/render 同时返回报告", r.json()["report"]["coverage"] == 1.0)

    r = client.post("/locate", json={"snippet": "年假需提前 3 个工作日", "idx": 1})
    check("POST /locate 返回高亮片段", r.status_code == 200 and r.json()["found"] is True)
    check("/locate 返回字符区间", isinstance(r.json().get("start"), int))
    r = client.post("/locate", json={"snippet": "年假需提前 3 个工作日", "idx": 99})
    check("POST /locate 越界来源 404", r.status_code == 404, str(r.status_code))

    r = client.get("/demo", params={"case": "fabricated"})
    check("GET /demo 返回案例与 HTML", r.status_code == 200 and "citation-answer" in r.json()["html"])
    r = client.get("/demo", params={"case": "nope"})
    check("GET /demo 未知案例 404", r.status_code == 404, str(r.status_code))

    r = client.get("/sources")
    check("GET /sources 返回 4 条原文", r.status_code == 200 and r.json()["count"] == 4)
    r = client.post("/check", json={"answer": ""})
    check("空答案返回 422", r.status_code == 422, str(r.status_code))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
