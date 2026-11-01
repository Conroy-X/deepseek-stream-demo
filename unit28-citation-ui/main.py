"""Unit 28 · Citation UI —— 后端只负责「给对数据、说清状态」，点不点得准是前端的事。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8028
    打开 http://127.0.0.1:8028/

自检（离线）：
    uv run python main.py
"""

from __future__ import annotations

import os
import re
import sys

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from contract import (
    ALLOWED_SCHEMES,
    FIXTURE_CASES,
    LOCATOR_VERSION,
    RaceGuard,
    build_fixture,
    extract_markers,
    has_errors,
    jump_target,
    locate_in_page,
    page_count,
    page_text,
    payload_to_dict,
    render_answer,
    render_citation_text,
    validate_answer_payload,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 28 · Citation UI", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def _fixture(case: str):
    try:
        return build_fixture(case)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def _citation(payload, n: int):
    for c in payload.citations:
        if c.id == n:
            return c
    raise HTTPException(status_code=404, detail=f"这份响应里没有第 {n} 条引用")


@app.get("/health", summary="fixture 列表、协议白名单、定位器版本")
def health() -> dict:
    return {
        "ok": True,
        "cases": list(FIXTURE_CASES),
        "allowed_schemes": list(ALLOWED_SCHEMES),
        "locator_version": LOCATOR_VERSION,
        "note": "没有真 PDF：页码打在模拟文档上，但契约与跳转判定是真的",
    }


@app.get("/api/demo/answer", summary="一份带引用的问题响应 + 校验结果 + 渲染好的 HTML")
def demo_answer(case: str = Query(default="normal")) -> dict:
    p = _fixture(case)
    raw = payload_to_dict(p)
    problems = validate_answer_payload(raw)
    return {
        **raw,
        "problems": problems,
        "renderable": not has_errors(problems),
        # 有 error 时仍然给 HTML —— 让用户看见「坏成什么样」，比白屏更有教育意义
        "html": render_answer(p.answer, p.citations),
        "citations_view": [
            {
                **c.to_dict(),
                "snippet_html": render_citation_text(c.snippet or c.text),
                "jump": jump_target(c),
            }
            for c in p.citations
        ],
    }


@app.get("/api/jump", summary="这条引用能不能跳，跳不了说清原因")
def jump(case: str = Query(default="normal"), n: int = Query(ge=1)) -> dict:
    p = _fixture(case)
    return jump_target(_citation(p, n)).copy()


@app.get("/api/docs/{document_id}/pages/{page}", summary="模拟 PDF 的一页 + 片段高亮区间")
def doc_page(
    document_id: str,
    page: int,
    snippet: str = Query(default="", max_length=500),
) -> dict:
    text = page_text(document_id, page)
    if text is None:
        raise HTTPException(
            status_code=404,
            detail=f"{document_id} 没有第 {page} 页（共 {page_count(document_id)} 页）",
        )
    span = locate_in_page(text, snippet)
    return {
        "document_id": document_id,
        "page": page,
        "total_pages": page_count(document_id),
        "text": text,
        "highlight": span,
        # 页内高亮失败也要把整页给出去：用户至少还能自己找
        "degraded": not span["found"],
    }


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

    print("Unit 28 · 引用 UI 自检（离线）\n")

    print("  —— 契约校验：能拦下来的错 ——")
    base = payload_to_dict(build_fixture("normal"))
    check("normal 没有任何问题", validate_answer_payload(base) == [], str(validate_answer_payload(base)))

    dup = {**base, "citations": [dict(base["citations"][0]), dict(base["citations"][0])]}
    codes = [p["code"] for p in validate_answer_payload(dup)]
    check("重复 id 被拦下", "DUP_ID" in codes, str(codes))

    missing = {**base, "citations": [{k: v for k, v in base["citations"][0].items() if k != "text"}]}
    check("缺字段被拦下", "MISSING_FIELD" in [p["code"] for p in validate_answer_payload(missing)])

    bad_id = {**base, "citations": [{**base["citations"][0], "id": 0}]}
    check("id=0 被拦下（编号从 1 开始）", "BAD_ID" in [p["code"] for p in validate_answer_payload(bad_id)])
    bool_id = {**base, "citations": [{**base["citations"][0], "id": True}]}
    check("id=True 也被拦下（bool 是 int 的子类，别漏）",
          "BAD_ID" in [p["code"] for p in validate_answer_payload(bool_id)],
          str([p["code"] for p in validate_answer_payload(bool_id)]))

    bad_page = {**base, "citations": [{**base["citations"][0], "page": 0}]}
    check("page=0 被拦下", "BAD_PAGE" in [p["code"] for p in validate_answer_payload(bad_page)])

    js_url = {**base, "citations": [{**base["citations"][0], "source_url": "javascript:alert(1)"}]}
    check("javascript: 协议被拦下", "BAD_URL" in [p["code"] for p in validate_answer_payload(js_url)])
    data_url = {**base, "citations": [{**base["citations"][0], "source_url": "data:text/html,<b>x"}]}
    check("data: 协议被拦下", "BAD_URL" in [p["code"] for p in validate_answer_payload(data_url)])

    unknown = [p for p in validate_answer_payload({**base, "answer": base["answer"] + "见 [9]"})
               if p["code"] == "UNKNOWN_MARKER"]
    check("未知 [9] 被记下来", len(unknown) == 1, str(unknown))
    check("未知 [9] 是 warning 不是 error", unknown and unknown[0]["level"] == "warning", str(unknown))
    check("未被使用的引用记 warning",
          "UNUSED_CITATION" in [p["code"] for p in validate_answer_payload(
              {**base, "answer": "只有一句。[1]"})])
    check("空 request_id 被拦下",
          "BAD_REQUEST_ID" in [p["code"] for p in validate_answer_payload({**base, "request_id": ""})])
    check("answer 不是字符串被拦下",
          "BAD_ANSWER" in [p["code"] for p in validate_answer_payload({**base, "answer": None})])
    check("超长 answer 被拒绝渲染",
          "ANSWER_TOO_LONG" in [p["code"] for p in validate_answer_payload({**base, "answer": "字" * 9000})])

    mal = payload_to_dict(build_fixture("malicious"))
    mal_codes = [p["code"] for p in validate_answer_payload(mal)]
    check("malicious 同时抓到重复 id 与坏协议", "DUP_ID" in mal_codes and "BAD_URL" in mal_codes, str(mal_codes))
    check("malicious 的 renderable 为 False", has_errors(validate_answer_payload(mal)) is True)

    print("\n  —— 渲染：先转义再替换 ——")
    p = build_fixture("normal")
    h = render_answer(p.answer, p.citations)
    check("[1] 渲染成 sup 角标", '<sup class="cite" data-n="1">' in h, h[:90])
    check("三个角标都在", h.count('class="cite"') == 3, str(h.count('class="cite"')))
    check("markers 按出现顺序", extract_markers(p.answer) == [1, 2, 3], str(extract_markers(p.answer)))

    hu = render_answer("准。[4]", p.citations)
    check("未知编号渲染成 cite-bad", 'class="cite-bad" data-n="4"' in hu, hu)
    check("未知编号没被丢掉", "[4]" in hu)

    hm = render_answer('<img src=x onerror=alert(1)>年假[1]', p.citations)
    check("img 标签被转义", "<img" not in hm and "&lt;img" in hm, hm[:90])
    # onerror 作为**文本**留在页面上无害，有害的是它作为属性被解析。
    # 所以判据不是「字符串里有没有 onerror」，而是「除了我们自己生成的 sup，还有没有别的尖括号」
    rest = re.sub(r"</?sup\b[^>]*>", "", hm)
    check("除了自家 sup，没有任何未转义的尖括号", "<" not in rest, rest[:90])
    check("转义后角标仍然生成", '<sup class="cite" data-n="1">' in hm)

    sq = render_answer("引号\" onmouseover=\"alert(1)[1]", p.citations)
    check("答案里的引号被转义（拆不出新属性）",
          "&quot; onmouseover=&quot;" in sq, sq[:110])

    check("引用片段单独转义", "&lt;script&gt;" in render_citation_text("<script>x</script>"))
    check("引用片段超长会截断", render_citation_text("字" * 400).endswith("…"))

    print("\n  —— 跳转：能跳 / 不能跳 / 不许跳 ——")
    check("normal 的 [1] 可跳第 3 页", jump_target(p.citations[0])["status"] == "ok")
    check("跳转链接带 #page=", jump_target(p.citations[0])["url"].endswith("#page=3"))

    np = build_fixture("missing_page")
    j2 = jump_target(np.citations[1])
    check("没页码时 status=no_page", j2["status"] == "no_page", str(j2))
    check("no_page 仍然带着 citation_id（可以展开片段）", j2["citation_id"] == 2)
    check("no_page 不返回 url（不给假链接）", "url" not in j2)

    mb = build_fixture("malicious")
    jb = jump_target(mb.citations[0])
    check("坏协议 status=bad_url", jb["status"] == "bad_url", str(jb))
    check("bad_url 不给 url", "url" not in jb)

    print("\n  —— 竞态：只认最后一次点击 ——")
    g = RaceGuard()
    t1 = g.begin()
    t2 = g.begin()
    check("先发起的请求过期", g.accept(t1) is False)
    check("最后发起的请求被接受", g.accept(t2) is True)
    t3 = g.begin()
    check("新请求会让旧的失效", g.accept(t2) is False and g.accept(t3) is True)
    check("token 单调递增", g.current == 3, str(g.current))

    print("\n  —— 页内定位 ——")
    text = page_text("hr-handbook", 3)
    span = locate_in_page(text, "年假需提前 3 个工作日在系统提交申请")
    check("能在页文本里定位到片段", span["found"] is True, str(span))
    check("区间内就是原文", text[span["start"]:span["end"]].startswith("年假需提前"), text[span["start"]:span["end"]])
    check("找不到时不报错、只标记", locate_in_page(text, "完全不存在的句子")["found"] is False)
    check("空片段直接判未命中", locate_in_page(text, "   ")["found"] is False)

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    check("GET /health 返回 4 个 fixture", r.status_code == 200 and len(r.json()["cases"]) == 4)
    check("/health 暴露协议白名单", "/" in r.json()["allowed_schemes"])

    r = client.get("/api/demo/answer", params={"case": "normal"})
    d = r.json()
    check("GET /api/demo/answer 可渲染", r.status_code == 200 and d["renderable"] is True)
    check("返回 citations_view（带 jump）", len(d["citations_view"]) == 3 and "jump" in d["citations_view"][0])

    r = client.get("/api/demo/answer", params={"case": "malicious"})
    check("malicious 的 renderable 为 False", r.json()["renderable"] is False)
    check("malicious 的 html 里没有真 img", "<img" not in r.json()["html"])

    r = client.get("/api/jump", params={"case": "missing_page", "n": 2})
    check("GET /api/jump 返回 no_page", r.status_code == 200 and r.json()["status"] == "no_page")
    r = client.get("/api/jump", params={"case": "normal", "n": 9})
    check("不存在的引用 404", r.status_code == 404, str(r.status_code))

    r = client.get("/api/docs/hr-handbook/pages/3", params={"snippet": "年假需提前 3 个工作日"})
    check("GET 文档页返回正文", r.status_code == 200 and "休假管理" in r.json()["text"])
    check("文档页返回高亮区间", r.json()["highlight"]["found"] is True)
    r = client.get("/api/docs/hr-handbook/pages/99")
    check("不存在的页 404", r.status_code == 404, str(r.status_code))

    r = client.get("/api/demo/answer", params={"case": "nope"})
    check("未知 fixture 404", r.status_code == 404, str(r.status_code))
    r = client.get("/")
    check("GET / 返回页面", r.status_code == 200 and "引用 UI" in r.text)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
