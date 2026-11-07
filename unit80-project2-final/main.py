"""Unit 80 · 项目 2 最终版：企业知识库 RAG 的「交付级」打磨。

计划主题：打磨 UI、补文档、录 Demo。
本单元是把 Unit 15–30 的知识库能力收成一个可交付版本，交付物三件套：

  1. 能用的服务：/ask（检索 + 回答 + 引用）+ 静态 UI
  2. 能看的文档：README 里的「交付清单」与「录 Demo 脚本」
  3. 自检：离线验证服务行为与交付物齐全

启动：uv run uvicorn main:app --port 8080
自检：uv run python main.py
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent

# 小型企业知识库（项目 2 的语料）
KB = {
    1: ("policy.md", 1, "退款金额原路退回，通常 3-5 个工作日到账。"),
    2: ("policy.md", 2, "虚拟商品一经激活不支持退款，未激活 7 天内可退。"),
    3: ("handbook.md", 1, "年假需提前 3 个工作日在系统提交申请。"),
    4: ("ops.md", 1, "差旅报销需在出差结束后 10 个工作日内提交。"),
}

REFUSAL = "资料里没有相关内容，无法回答这个问题。"


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower()))


def retrieve(q: str, k: int = 2) -> list[tuple[int, float]]:
    q_terms = _terms(q)
    scored = []
    for doc_id, (_, _, text) in KB.items():
        d = _terms(text)
        overlap = len(q_terms & d) / max(1, len(q_terms))
        scored.append((doc_id, overlap))
    scored.sort(key=lambda kv: -kv[1])
    return scored[:k]


def answer(q: str, threshold: float = 0.4) -> dict:
    hits = retrieve(q)
    if not hits or hits[0][1] < threshold:
        return {"answer": REFUSAL, "refused": True, "citations": []}
    doc_id, score = hits[0]
    src, page, text = KB[doc_id]
    return {
        "answer": f"{text} [1]",
        "refused": False,
        "citations": [{"n": 1, "source": src, "page": page, "text": text}],
        "score": round(score, 4),
    }


app = FastAPI(title="Unit 80 · 项目2最终版：企业知识库", version="1.0.0")

if (HERE / "static").is_dir():
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


@app.get("/health", summary="交付级健康检查")
async def health() -> dict:
    return {"ok": True, "project": "企业知识库", "version": "1.0.0", "kb_size": len(KB)}


@app.get("/ask", summary="问答：带引用；无依据拒答")
async def ask(q: str) -> dict:
    return answer(q)


@app.get("/", include_in_schema=False)
async def index():
    ui = HERE / "static" / "index.html"
    if ui.exists():
        from fastapi.responses import HTMLResponse

        return HTMLResponse(ui.read_text(encoding="utf-8"))
    return JSONResponse({"hint": "static/index.html 不存在，看 /docs"})


# ---------------------------------------------------------------------------
# 独立自检：服务行为 + 交付物齐全
# ---------------------------------------------------------------------------
def run_selfcheck() -> int:
    from fastapi.testclient import TestClient

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 80 · 项目2最终版 自检\n")
    c = TestClient(app)

    # ---- 服务行为 ----
    r = c.get("/health")
    check("health 200", r.status_code == 200)
    check("health 报告知识库大小", r.json()["kb_size"] == len(KB))
    check("有依据的问题能回答", c.get("/ask", params={"q": "退款多久到账"}).json()["refused"] is False)
    r = c.get("/ask", params={"q": "今天天气怎么样"})
    check("无关问题拒答", r.json()["refused"] is True)
    check("拒答带固定话术", r.json()["answer"] == REFUSAL)
    r = c.get("/ask", params={"q": "退款多久到账"}).json()
    check("答案带引用 [1]", "[1]" in r["answer"])
    check("引用有来源和页码", r["citations"][0]["page"] >= 1 and r["citations"][0]["source"])

    # ---- 交付物：UI ----
    ui = HERE / "static" / "index.html"
    check("UI 文件存在", ui.exists())
    if ui.exists():
        html = ui.read_text(encoding="utf-8")
        check("UI 有输入框", "<input" in html or "<textarea" in html)
        check("UI 调用了 /ask", "/ask" in html)
        check("UI 有拒答样式（可区分）", "refused" in html or "拒答" in html)

    # ---- 交付物：README 录 Demo 脚本 ----
    rd = HERE / "README.md"
    check("README 存在", rd.exists())
    if rd.exists():
        md = rd.read_text(encoding="utf-8")
        check("README 有交付清单", "交付清单" in md)
        check("README 有录 Demo 脚本", "录 Demo" in md or "Demo 脚本" in md)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
