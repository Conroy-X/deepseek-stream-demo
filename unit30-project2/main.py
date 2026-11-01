"""Unit 30 · 项目 2：企业知识库问答（把 15–29 串成一条真能跑的链路）。

链路就是这一行：

    文件 → 解析(17) → 切块(18) → 向量化+入库(24) → 检索(25) → 重排(26) → 带引用回答(20/21)

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8030
    打开 http://127.0.0.1:8030/

自检 + 交付检查（离线，不联网）：
    uv run python main.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import embedding as emb
from chunker import chunk_document
from parser import ParseError, parse_bytes
from reranker import Scored, mrr, ndcg_at_k, recall_at_k, rerank
from retriever import Retriever

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")
DB_PATH = os.path.join(BASE_DIR, "data", "project.db")
TENANT = "demo"

app = FastAPI(title="Unit 30 · 企业知识库", version="1.0.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

DEMO_DOCS = {
    "员工手册.md": """# 员工手册

## 年假
入职满一年享 5 天年假，满三年 10 天，满五年 15 天。年假需提前 3 个工作日在系统提交申请，由直属主管审批。

## 病假
病假需提供二级以上医院证明，全年累计不超过 30 天。

## 差旅
差旅报销需在出差结束后 10 个工作日内提交，附发票原件与行程单。一线城市住宿每晚不超过 600 元。
""",
    "退款政策.md": """# 退款政策

未发货订单可随时申请全额退款，1 个工作日内处理。

已发货订单需先拒收或寄回，仓库签收后 3 个工作日内退款。

虚拟商品一经激活不支持退款，未激活可在 7 天内申请。

退款金额原路退回，到账时间通常 3–5 个工作日。
""",
    "安全规范.md": """# 安全规范

密码要求 12 位以上，每 90 天更换一次。

连续 5 次登录失败锁定账号 30 分钟，解锁需联系 IT 服务台。

生产数据禁止下载到个人设备，导出需审批并加水印。
""",
}

# 评测集：question + 答案里必须出现的片段 + 应召回的来源
EVAL = [
    {"q": "年假要提前几天申请", "must": "3 个工作日", "source": "员工手册.md"},
    {"q": "病假需要什么证明", "must": "二级以上医院证明", "source": "员工手册.md"},
    {"q": "差旅报销多久内提交", "must": "10 个工作日", "source": "员工手册.md"},
    {"q": "一线城市住宿标准", "must": "600 元", "source": "员工手册.md"},
    {"q": "未发货订单多久退款", "must": "1 个工作日", "source": "退款政策.md"},
    {"q": "虚拟商品能退款吗", "must": "不支持退款", "source": "退款政策.md"},
    {"q": "退款多久到账", "must": "3–5 个工作日", "source": "退款政策.md"},
    {"q": "密码多久换一次", "must": "90 天", "source": "安全规范.md"},
    {"q": "账号锁定多久", "must": "30 分钟", "source": "安全规范.md"},
    {"q": "生产数据能下载吗", "must": "禁止下载", "source": "安全规范.md"},
]

REFUSAL = "资料里没有相关内容，不能凭印象回答。"
CITE_RE = re.compile(r"\[(\d+)\]")

# 拒答阈值。实测（本地哈希向量、90 字切块）：
#   相关问题 top1 分数 0.177–0.260
#   无关问题 top1 分数 0.078–0.099
# 取 0.12 作为分界。换 embedding 模型必须重新测这个数 —— 它不是通用常数。
MIN_SCORE = 0.12


def _retriever() -> Retriever:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return Retriever(DB_PATH)


def ingest_bytes(name: str, data: bytes, r: Retriever, size: int = 90, overlap: int = 20) -> dict:
    """文件 → 解析 → 切块 → 入库。整条链路唯一的入口。"""
    doc = parse_bytes(name, data)
    chunks = chunk_document(doc.pages, "recursive", size, overlap)
    items = [
        {"id": f"{name}#{c.index}", "source": name, "page": c.page, "text": c.text}
        for c in chunks
    ]
    n = r.index(TENANT, items)
    return {
        "name": name,
        "kind": doc.kind,
        "pages": doc.page_count,
        "chars": doc.chars,
        "chunks": len(chunks),
        "indexed": n,
        "warnings": doc.warnings,
    }


def answer(question: str, r: Retriever, recall_n: int = 12, top_k: int = 4) -> dict:
    """检索 → 重排 → 拼接带编号的答案。没有依据就拒答。"""
    hits = r.search(TENANT, question, top_k=recall_n, log=False)
    if not hits or hits[0].score < MIN_SCORE:
        return {
            "answer": REFUSAL,
            "refused": True,
            "citations": [],
            "hits": [h.to_dict() for h in hits],
            "top_score": round(hits[0].score, 4) if hits else 0.0,
        }

    ranked = rerank(
        question,
        [
            # Retriever 的 Hit → 重排需要的 Scored（保留向量分数，便于对比）
            Scored(chunk_id=h.chunk_id, text=h.text, score=0.0, vector_score=h.score)
            for h in hits
        ],
        top_k,
    )

    # 离线生成：把命中最强的句子按顺序编号，不做"自由发挥"。
    # 有 key 时这里换成真实模型，prompt 与引用规则完全一样。
    picked, citations = [], []
    for i, c in enumerate(ranked, 1):
        sent = _best_sentence(question, c.text)
        picked.append(f"[{i}] {sent}")
        citations.append(
            {
                "id": i,
                "chunk_id": c.chunk_id,
                "source": next((h.source for h in hits if h.chunk_id == c.chunk_id), "?"),
                "page": next((h.page for h in hits if h.chunk_id == c.chunk_id), 1),
                "snippet": sent,
            }
        )

    body = "根据资料：" + " ".join(picked)
    return {
        "answer": body,
        "refused": False,
        "citations": citations,
        "top_score": round(ranked[0].score, 4),
        "hits": [h.to_dict() for h in hits[:recall_n]],
    }


def _best_sentence(question: str, text: str) -> str:
    """离线"生成"：挑与问题最像的那一句。真模型下这一步不需要。"""
    sents = [s.strip() for s in re.split(r"(?<=[。！？])", text) if s.strip()]
    if not sents:
        return text.strip()
    qv = emb.get_embedder()._embed_one(question)
    best = max(sents, key=lambda s: emb.cosine_similarity(qv, emb.get_embedder()._embed_one(s)))
    return best


class AskIn(BaseModel):
    question: str = Field(min_length=1)
    top_k: int = Field(default=4, ge=1, le=10)


@app.get("/health", summary="库里有多少块")
def health() -> dict:
    r = _retriever()
    return {"ok": True, "chunks": r.count(TENANT), "docs": len(DEMO_DOCS), "eval": len(EVAL)}


@app.post("/seed", summary="灌入三份演示文档")
def seed() -> dict:
    r = _retriever()
    r.clear(TENANT)
    rows = [ingest_bytes(name, text.encode("utf-8"), r) for name, text in DEMO_DOCS.items()]
    return {"docs": rows, "chunks": r.count(TENANT)}


@app.post("/upload", summary="上传自己的文档（pdf/docx/md/txt/html/csv）")
async def upload(file: UploadFile = File(...)) -> dict:
    r = _retriever()
    data = await file.read()
    try:
        return ingest_bytes(file.filename or "upload.bin", data, r)
    except ParseError as e:
        raise HTTPException(status_code=415, detail=str(e)) from e


@app.post("/ask", summary="问一句，拿带引用的回答")
def ask(req: AskIn) -> dict:
    r = _retriever()
    if r.count(TENANT) == 0:
        raise HTTPException(status_code=409, detail="知识库是空的，先 POST /seed")
    return answer(req.question, r, top_k=req.top_k)


@app.post("/eval", summary="跑 10 题评测：召回、排序、引用、拒答")
def evaluate() -> dict:
    r = _retriever()
    if r.count(TENANT) == 0:
        raise HTTPException(status_code=409, detail="知识库是空的，先 POST /seed")

    rows = []
    for item in EVAL:
        res = answer(item["q"], r)
        sources = [c["source"] for c in res["citations"]]
        rows.append(
            {
                "q": item["q"],
                "answer_ok": item["must"] in res["answer"],
                "source_ok": item["source"] in sources,
                "cited": bool(CITE_RE.search(res["answer"])),
                "refused": bool(res.get("refused")),
            }
        )

    # 拒答正确性：问一个库里没有的东西
    off = answer("公司年会抽奖有什么奖品", r)
    n = len(rows)
    return {
        "n": n,
        "answer_acc": round(sum(x["answer_ok"] for x in rows) / n, 3),
        "source_acc": round(sum(x["source_ok"] for x in rows) / n, 3),
        "citation_rate": round(sum(x["cited"] for x in rows) / n, 3),
        "refusal_correct": bool(off["refused"]),
        "details": rows,
    }


@app.get("/check", summary="交付检查：文件、指标、拒答")
def delivery_check() -> dict:
    required = [
        "README.md", "notes.md", "main.py", "parser.py", "chunker.py",
        "retriever.py", "reranker.py", "embedding.py", "static/index.html",
    ]
    missing = [f for f in required if not os.path.exists(os.path.join(BASE_DIR, f))]

    r = _retriever()
    checks = [
        {"name": "必需文件齐全", "pass": not missing, "detail": missing or "ok"},
        {"name": "知识库非空", "pass": r.count(TENANT) > 0, "detail": f"{r.count(TENANT)} chunks"},
    ]
    if r.count(TENANT) > 0:
        ev = evaluate()
        checks += [
            {"name": "答案准确率 >= 0.9", "pass": ev["answer_acc"] >= 0.9, "detail": ev["answer_acc"]},
            {"name": "来源命中率 >= 0.9", "pass": ev["source_acc"] >= 0.9, "detail": ev["source_acc"]},
            {"name": "引用覆盖率 = 1.0", "pass": ev["citation_rate"] >= 1.0, "detail": ev["citation_rate"]},
            {"name": "无依据时拒答", "pass": ev["refusal_correct"], "detail": ev["refusal_correct"]},
        ]
    else:
        checks.append({"name": "评测", "pass": False, "detail": "库是空的，先 /seed"})

    return {"pass": all(c["pass"] for c in checks), "checks": checks}


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

    print("Unit 30 · 项目 2 自检 + 交付检查（离线）\n")

    tmp = tempfile.mkdtemp()
    DB_PATH = os.path.join(tmp, "p.db")
    r = _retriever()

    print("  —— 链路：解析 → 切块 → 入库 ——")
    stats = [ingest_bytes(n, t.encode("utf-8"), r) for n, t in DEMO_DOCS.items()]
    total = sum(s["chunks"] for s in stats)
    check("三份文档都解析成功", all(s["chars"] > 50 for s in stats), str([s["chars"] for s in stats]))
    check("切块数 > 0", total > 3, str(total))
    check("入库数与切块数一致", r.count(TENANT) == total, f"{r.count(TENANT)} vs {total}")
    got_sources = {h.source for q in ("年假", "退款", "密码") for h in r.search(TENANT, q, top_k=10, log=False)}
    check("每块都带来源文件名", got_sources and all(s.endswith(".md") for s in got_sources), str(sorted(got_sources)))

    print("\n  —— 检索 → 重排 → 回答 ——")
    res = answer("年假要提前几天申请", r)
    check("答案非空且未拒答", res["answer"] and not res["refused"])
    check("答案直接引用原文关键片段", "3 个工作日" in res["answer"], res["answer"][:60])
    check("答案带 [1] 编号", bool(CITE_RE.search(res["answer"])), res["answer"][:40])
    check("citations 与编号一一对应", [c["id"] for c in res["citations"]] == list(range(1, len(res["citations"]) + 1)))
    check("citation 带 source 与 page", all(c["source"] and c["page"] for c in res["citations"]))
    check("citation 的 snippet 能在这条来源里找到", all(c["snippet"] in res["answer"] for c in res["citations"]))

    print("\n  —— 无依据必须拒答（最容易做错的一条）——")
    off = answer("公司年会抽奖有什么奖品", r)
    check("库里没有的问题被拒答", off["refused"], off["answer"][:40])
    check("拒答时不编引用", off["citations"] == [])

    print("\n  —— 评测集 ——")
    ev = evaluate()
    for row in ev["details"]:
        print(f"       {'✓' if row['answer_ok'] else '✗'} {row['q'][:16]:18s} 答案={row['answer_ok']} 来源={row['source_ok']} 引用={row['cited']}")
    print(f"       answer_acc={ev['answer_acc']} source_acc={ev['source_acc']} citation_rate={ev['citation_rate']} 拒答={ev['refusal_correct']}")
    check("答案准确率 >= 0.9", ev["answer_acc"] >= 0.9, str(ev["answer_acc"]))
    check("来源命中率 >= 0.9", ev["source_acc"] >= 0.9, str(ev["source_acc"]))
    check("引用覆盖率 = 1.0", ev["citation_rate"] >= 1.0, str(ev["citation_rate"]))
    check("无关问题被拒答", ev["refusal_correct"])

    print("\n  —— 交付检查 ——")
    dc = delivery_check()
    for c in dc["checks"]:
        print(f"       {'✓' if c['pass'] else '✗'} {c['name']}: {c['detail']}")
    check("交付检查整体通过", dc["pass"])

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    check("GET /health", client.get("/health").status_code == 200)
    check("POST /seed 重建知识库", client.post("/seed").status_code == 200)
    body = client.post("/ask", json={"question": "退款多久到账"}).json()
    check("POST /ask 返回带引用的答案", body["citations"] and CITE_RE.search(body["answer"]))
    check("GET /check 通过", client.get("/check").json()["pass"] is True)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
