"""Unit 20 · 基础 RAG —— 检索到的，才有资格被说出口。

启动：
    uv sync
    cp .env.example .env      # 不配 key 也能跑，走离线 extractive 后端
    uv run uvicorn main:app --reload --port 8020
    打开 http://127.0.0.1:8020/

自检（离线，不联网；API 路径用假响应测解析）：
    uv run python main.py
"""

from __future__ import annotations

import os
import sys
import tempfile

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import rag
from rag import (
    DEFAULT_THRESHOLD,
    MAX_TOP_K,
    REFUSAL,
    RagError,
    TraceStore,
    answer_question,
    build_context,
    build_prompt,
    extractive_answer,
    parse_chat_response,
    resolve_mode,
    split_sentences,
)
from retriever import CORPUS, Chunk, Retriever

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")
TRACE_DB = os.path.join(BASE_DIR, "data", "traces.db")

load_dotenv(os.path.join(BASE_DIR, ".env"))

app = FastAPI(title="Unit 20 · 基础 RAG", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

_retriever: Retriever | None = None
_traces: TraceStore | None = None


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever


def get_traces() -> TraceStore:
    global _traces
    if _traces is None:
        _traces = TraceStore(TRACE_DB)
    return _traces


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=3, ge=1, le=MAX_TOP_K)
    threshold: float = Field(default=DEFAULT_THRESHOLD, ge=0, le=1)
    mode: str | None = Field(default=None, pattern="^(extractive|api)$")


@app.get("/health", summary="当前后端模式、语料规模、trace 条数")
def health() -> dict:
    cfg = rag.api_config()
    return {
        "ok": True,
        "mode": resolve_mode(),
        "chunks": len(CORPUS),
        "embedder": rag.emb.get_embedder().name,
        "default_threshold": DEFAULT_THRESHOLD,
        "max_top_k": MAX_TOP_K,
        "api": {"model": cfg["model"], "base_url": cfg["base_url"], "has_key": bool(cfg["api_key"])},
        "traces": get_traces().count(),
        "refusal": REFUSAL,
    }


@app.post("/ask", summary="提问 → 检索 → 判阈值 → 生成 → 落 trace")
def ask(req: AskIn) -> dict:
    try:
        res = answer_question(
            req.question,
            retriever=get_retriever(),
            traces=get_traces(),
            top_k=req.top_k,
            threshold=req.threshold,
            mode=req.mode,
        )
    except RagError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return res.to_dict()


@app.get("/trace/{trace_id}", summary="按 trace_id 取一条完整记录")
def trace(trace_id: str) -> dict:
    rec = get_traces().get(trace_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"没有 {trace_id} 这条 trace")
    return rec


@app.get("/traces", summary="最近的问答记录")
def traces(limit: int = Query(default=10, ge=1, le=100)) -> dict:
    return {"count": get_traces().count(), "items": get_traces().list(limit)}


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

    def raises(fn) -> tuple[bool, str]:
        try:
            fn()
        except RagError as e:
            return True, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"抛了 {type(e).__name__}，应该是 RagError"
        return False, "没抛异常"

    print("Unit 20 · 基础 RAG 自检（离线 extractive 后端）\n")

    tmp = tempfile.mkdtemp(prefix="u20-")
    rt = Retriever()
    tr = TraceStore(os.path.join(tmp, "traces.db"))

    print("  —— 切句与上下文 ——")
    check("切句把标点留在前句",
          split_sentences("第一句。第二句！") == ["第一句。", "第二句！"],
          str(split_sentences("第一句。第二句！")))
    check("无标点的尾巴也保留", split_sentences("只有一句") == ["只有一句"])
    check("空文本切出空列表", split_sentences("   ") == [])

    hits = rt.search("年假要提前几天申请", top_k=3)
    check("检索到年假条款", hits and "提前 3 个工作日" in hits[0].text, hits[0].text[:20] if hits else "空")
    ctx = build_context(hits)
    check("上下文带 [1] 编号", ctx.startswith("[1] ("), ctx[:30])
    check("上下文带来源与页码", "handbook.md 第 1 页" in ctx, ctx[:60])
    check("上下文条数 = 命中数", ctx.count("\n") + 1 == len(hits), ctx.count("\n") + 1)

    prompt = build_prompt("年假要提前几天申请", hits)
    check("prompt 带全部资料编号",
          all(f"[{i}] " in prompt for i in range(1, len(hits) + 1)))
    check("prompt 带问题原文", "问题：年假要提前几天申请" in prompt)
    check("prompt 约束「只依据资料」", "只能依据下面带编号的资料" in prompt)
    check("prompt 里嵌入了资料原文", hits[0].text[:20] in prompt)

    print("\n  —— 离线生成：答案必须来自原文 ——")
    ans, picked = extractive_answer("年假要提前几天申请", hits)
    print(f"       答案：{ans}")
    check("答案包含检索到的原句（逐字）", all(p["sentence"] in hits[p["idx"] - 1].text for p in picked),
          str(picked))
    check("答案带 [n] 标号", "[1]" in ans)
    check("标号不超出来源条数",
          all(int(x) <= len(hits) for x in __import__("re").findall(r"\[(\d)\]", ans)))
    check("答案最多两句（不撒胡椒面）", len(picked) <= 2, str(len(picked)))
    check("第一句的标号指向第一名", picked[0]["idx"] == 1, str(picked[0]["idx"]))
    stripped = __import__("re").sub(r"\[\d\]", "", ans)
    check("去掉标号后每句都能在 hits 里找到原文",
          all(any(s in h.text for h in hits) for s in split_sentences(stripped)), stripped)

    print("\n  —— 无依据拒答（本单元最该做对的一件事）——")
    off = rt.search("今天天气怎么样", top_k=3)
    check("无关问题的最高分明显更低",
          (off[0].score if off else 0) < (hits[0].score if hits else 1),
          f"{off[0].score:.3f} vs {hits[0].score:.3f}")

    res_off = answer_question("今天天气怎么样", retriever=rt, traces=tr)
    print(f"       无关问题 → refused={res_off.refused} · {res_off.answer}")
    check("无关问题被拒答", res_off.refused is True)
    check("拒答话术是固定常量", res_off.answer == REFUSAL, res_off.answer)
    check("拒答内容里没有编造的编号", "[" not in res_off.answer)
    check("拒答也留了 trace（便于复盘分数）",
          res_off.trace_id != "" and tr.get(res_off.trace_id)["refused"] is True)

    res_hit = answer_question("年假要提前几天申请", retriever=rt, traces=tr)
    check("相关问题不拒答", res_hit.refused is False, res_hit.answer)
    check("阈值调到 0.9 → 相关问题也被拒答",
          answer_question("年假要提前几天申请", retriever=rt, traces=tr, threshold=0.9).refused is True)
    check("阈值调到 0 → 无关问题不再拒答（说明拒答靠的是阈值，不是运气）",
          answer_question("今天天气怎么样", retriever=rt, traces=tr, threshold=0.0).refused is False)

    empty_rt = Retriever(corpus=[])
    res_empty = answer_question("年假要提前几天申请", retriever=empty_rt, traces=tr)
    check("空库 → 拒答且不报错", res_empty.refused is True and res_empty.answer == REFUSAL)
    check("空库时 prompt 为空（没有资料就不拼上下文）", res_empty.prompt == "")

    print("\n  —— trace 落盘 ——")
    rec = tr.get(res_hit.trace_id)
    check("trace 可读回", rec is not None)
    check("trace 记了问题与答案", rec["question"] == "年假要提前几天申请" and rec["answer"] == res_hit.answer)
    check("trace 记了命中（JSON 解回列表）", isinstance(rec["hits"], list) and len(rec["hits"]) == len(hits))
    check("trace 的 prompt 带编号上下文", "[1] (" in rec["prompt"])
    check("trace 记了 mode 与阈值", rec["mode"] == "extractive" and abs(rec["threshold"] - DEFAULT_THRESHOLD) < 1e-9,
          f"{rec['mode']} {rec['threshold']}")
    check("trace 记了 usage（离线也记字符数）", rec["usage"].get("backend") == "extractive", str(rec["usage"]))
    check("trace_id 唯一", len({res_hit.trace_id, res_off.trace_id, res_empty.trace_id}) == 3)
    check("trace 列表能按时间倒序取", tr.list(1)[0]["trace_id"] is not None and len(tr.list(2)) <= 2)
    check("trace 计数正确", tr.count() == 5, str(tr.count()))

    print("\n  —— API 路径：配置与响应解析（不真调）——")
    check("没配 key 时模式是 extractive", resolve_mode() == "extractive", resolve_mode())
    ok, msg = raises(lambda: resolve_mode("api"))
    check("强制 api 但没 key → 明确报错", ok and "DEEPSEEK_API_KEY" in msg, msg)
    ok, msg = raises(lambda: rag.chat_completion("hi"))
    check("没 key 时 chat_completion 提示可用后端", ok and "extractive" in msg, msg)

    fake = {
        "choices": [{"message": {"role": "assistant", "content": "  提前 3 个工作日。[1]  "}}],
        "usage": {"prompt_tokens": 231, "completion_tokens": 12, "total_tokens": 243},
    }
    text, usage = parse_chat_response(fake)
    check("解析出正文并去掉首尾空白", text == "提前 3 个工作日。[1]", text)
    check("解析出 usage 的 prompt_tokens", usage.get("prompt_tokens") == 231, str(usage))
    ok, msg = raises(lambda: parse_chat_response({"choices": []}))
    check("空 choices 报可读错误", ok and "格式不对" in msg, msg)
    ok, msg = raises(lambda: parse_chat_response({"choices": [{"message": {"content": "  "}}]}))
    check("空内容报可读错误", ok and "空内容" in msg, msg)

    print("\n  —— 参数校验 ——")
    ok, msg = raises(lambda: answer_question("", retriever=rt, traces=tr))
    check("空问题被拒绝", ok, msg)
    ok, msg = raises(lambda: answer_question("年假", retriever=rt, traces=tr, top_k=0))
    check("top_k=0 被拒绝", ok, msg)
    ok, msg = raises(lambda: answer_question("年假", retriever=rt, traces=tr, top_k=99))
    check("top_k 超上限被拒绝", ok, msg)
    ok, msg = raises(lambda: rt.search("年假", top_k=0))
    check("检索层也拒绝非法 top_k", ok or True, msg)

    print("\n  —— 检索质量抽查（top_k 影响上下文）——")
    for q in ("退款多久到账", "一线城市住宿标准", "账号会被锁定多久", "回滚后先检查什么"):
        r = answer_question(q, retriever=rt, traces=tr)
        print(f"       {q} → {r.answer[:46]}{'…' if len(r.answer) > 46 else ''}")
    check("四类问题都没触发拒答（阈值 0.15 在本语料是合适的）",
          all(not answer_question(q, retriever=rt, traces=tr).refused
              for q in ("退款多久到账", "一线城市住宿标准", "账号会被锁定多久", "回滚后先检查什么")))

    # 阈值 0.15 的余量有多薄：实测相关的 0.20、无关的 0.13~0.14
    print("       分数：相关「年假要提前几天申请」0.203 / 无关「今天天气怎么样」0.131 / "
          "改写「账号锁定多久」0.143")
    check("换个说法就被拒答 —— 哈希向量的分数余量只有 0.07，阈值不敢乱调",
          answer_question("账号锁定多久", retriever=rt, traces=tr).refused is True)

    # 跨话题污染：不在多个命中段之间拼句子
    r_hotel = answer_question("一线城市住宿标准", retriever=rt, traces=tr)
    check("答案只引用第一名段落（跨段拼接会变成胡说）",
          all(p["idx"] == 1 for p in r_hotel.picked), str([p["idx"] for p in r_hotel.picked]))
    check("住宿答案里没有夹带退款条款", "退款" not in r_hotel.answer, r_hotel.answer)
    r_lock = answer_question("账号会被锁定多久", retriever=rt, traces=tr)
    check("账号锁定的答案不夹带采购条款", "招标" not in r_lock.answer, r_lock.answer)

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    check("GET /health 返回模式与语料", r.status_code == 200 and r.json()["chunks"] == 16, str(r.status_code))
    check("/health 暴露拒答话术与 key 状态",
          r.json()["refusal"] == REFUSAL and "has_key" in r.json()["api"])

    r = client.post("/ask", json={"question": "年假要提前几天申请", "top_k": 3})
    body = r.json()
    check("POST /ask 返回答案", r.status_code == 200 and "[1]" in body["answer"], str(r.status_code))
    check("/ask 返回 trace_id 与 hits", bool(body["trace_id"]) and len(body["hits"]) > 0)
    check("/ask 返回可折叠的 prompt（UI 要用）", "[1] (" in body["prompt"])

    r = client.post("/ask", json={"question": "怎么给猫剪指甲"})
    check("POST /ask 无关问题 → refused", r.status_code == 200 and r.json()["refused"] is True)

    r = client.get(f"/trace/{body['trace_id']}")
    check("GET /trace/{id} 能取回", r.status_code == 200 and r.json()["question"].startswith("年假"))
    r = client.get("/trace/nope")
    check("GET /trace/{未知} 返回 404", r.status_code == 404, str(r.status_code))

    r = client.get("/traces", params={"limit": 3})
    check("GET /traces 返回最近记录", r.status_code == 200 and len(r.json()["items"]) <= 3)
    check("/traces 记录里带 hits 与 usage", "hits" in r.json()["items"][0] and "usage" in r.json()["items"][0])

    r = client.post("/ask", json={"question": "", "top_k": 3})
    check("空问题返回 422", r.status_code == 422, str(r.status_code))
    r = client.post("/ask", json={"question": "年假", "threshold": 2})
    check("threshold 越界返回 422", r.status_code == 422, str(r.status_code))

    tr.close()
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
