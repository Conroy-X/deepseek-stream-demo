"""Unit 27 · RAG 流式输出 —— 检索 → 拼上下文 → SSE 边生成边发。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8027
    打开 http://127.0.0.1:8027/

自检（不需要 key，不联网）：
    uv run python main.py

没配 OPENAI_API_KEY 时走 extractive 后端：从检索结果里拼答案。
配了就流式调 OpenAI 兼容接口，协议、预算、截断逻辑完全一样。
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from store import Hit, Store, StoreError
from streaming import (
    EVENTS,
    AnswerStream,
    Citation,
    StreamError,
    build_citations,
    build_messages,
    check_markers,
    encode_sse,
    estimate_tokens,
    extractive_answer,
    extractive_producer,
    llm_producer,
    make_snippet,
    parse_frames,
    pick_backend,
    select_chunks,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")
DB_PATH = os.path.join(BASE_DIR, "data", "stream.db")

app = FastAPI(title="Unit 27 · RAG 流式输出", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

DEMO_CORPUS = [
    {"id": "rf-1", "document_id": "doc-refund", "source": "退款政策.md", "page": 1,
     "text": "未发货订单可随时申请全额退款，1 个工作日内处理。"},
    {"id": "rf-2", "document_id": "doc-refund", "source": "退款政策.md", "page": 2,
     "text": "退款金额原路退回，到账时间以支付渠道为准，通常 3–5 个工作日。"},
    {"id": "rf-3", "document_id": "doc-refund", "source": "退款政策.md", "page": 1,
     "text": "虚拟商品一经激活不支持退款，未激活可在 7 天内申请。"},
    {"id": "rf-4", "document_id": "doc-refund", "source": "退款政策.md", "page": 2,
     "text": "已发货订单需先拒收或寄回，仓库签收后 3 个工作日内退款。"},
    {"id": "hb-1", "document_id": "doc-hr", "source": "员工手册.md", "page": 1,
     "text": "年假需提前 3 个工作日在系统提交申请，由直属主管审批。"},
    {"id": "hb-2", "document_id": "doc-hr", "source": "员工手册.md", "page": 1,
     "text": "入职满一年享 5 天年假，满三年 10 天，满五年 15 天。"},
    {"id": "hb-3", "document_id": "doc-hr", "source": "员工手册.md", "page": 2,
     "text": "病假需提供二级以上医院证明，全年累计不超过 30 天。"},
    {"id": "hb-4", "document_id": "doc-hr", "source": "员工手册.md", "page": 2,
     "text": "加班按 1.5 倍调休，调休需在 3 个月内使用，逾期作废。"},
    {"id": "dp-1", "document_id": "doc-deploy", "source": "发布规范.md", "page": 1,
     "text": "发布流程：变更单 → 代码评审 → 预发验证 → 灰度 10% → 全量。"},
    {"id": "dp-2", "document_id": "doc-deploy", "source": "发布规范.md", "page": 2,
     "text": "回滚后先检查数据库迁移是否可逆，再确认缓存已清理。"},
    {"id": "sc-1", "document_id": "doc-security", "source": "安全规范.md", "page": 1,
     "text": "密码要求 12 位以上，每 90 天更换一次。"},
    {"id": "sc-2", "document_id": "doc-security", "source": "安全规范.md", "page": 1,
     "text": "连续 5 次登录失败锁定账号 30 分钟，解锁需联系 IT 服务台。"},
]


def get_store() -> Store:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return Store(DB_PATH)


def seed(store: Store, reset: bool = False) -> int:
    if reset:
        store.clear()
    return store.index(DEMO_CORPUS)


class SeedIn(BaseModel):
    reset: bool = False


class QueryIn(BaseModel):
    question: str = Field(min_length=1, max_length=200)
    top_k: int = Field(default=4, ge=1, le=10)
    max_context_tokens: int = Field(default=1200, ge=100, le=8000)
    max_output_tokens: int = Field(default=300, ge=20, le=2000)
    debug_fail_after: int | None = Field(default=None, ge=1, le=100)


@app.get("/health", summary="后端、语料、事件类型")
def health() -> dict:
    s = get_store()
    return {
        "ok": True,
        "backend": pick_backend(),
        "events": list(EVENTS),
        "chunks": s.count(),
        "db": os.path.relpath(DB_PATH, BASE_DIR),
    }


@app.post("/seed", summary="灌入演示语料")
def seed_route(req: SeedIn) -> dict:
    s = get_store()
    n = seed(s, req.reset)
    return {"indexed": n, "chunks": s.count()}


@app.post("/v1/rag/stream", summary="流式回答（SSE：meta → token* → done）")
async def rag_stream(req: QueryIn) -> StreamingResponse:
    request_id = "req_" + uuid.uuid4().hex[:12]
    s = get_store()
    if s.count() == 0:
        seed(s)  # 首次访问自动灌语料，别让用户先去翻文档

    try:
        hits = s.search(req.question, req.top_k)
        selected, dropped, used = select_chunks(req.question, hits, req.max_context_tokens)
    except (StoreError, StreamError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    citations = build_citations(selected)
    producer = (
        llm_producer(req.question, citations)
        if pick_backend() == "openai"
        else extractive_producer(req.question, citations)
    )
    stream = AnswerStream(
        request_id,
        req.question,
        citations,
        producer,
        max_output_tokens=req.max_output_tokens,
        fail_after=req.debug_fail_after,
    )

    async def body():
        try:
            async for ev in stream.events():
                yield encode_sse(ev["event"], ev["data"], event_id=request_id).encode("utf-8")
        except Exception as e:  # noqa: BLE001
            # 响应头已经发出去了，HTTP 状态码改不了，只能用 error 事件收尾
            yield encode_sse(
                "error",
                {
                    "code": "GENERATION_FAILED",
                    "message": f"生成中断：{type(e).__name__}",
                    "request_id": request_id,
                    "retryable": True,
                },
                event_id=request_id,
            ).encode("utf-8")

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "X-Request-Id": request_id,
            "Cache-Control": "no-cache",
            # nginx 默认会缓冲响应体，关掉才看得到逐字输出
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/preview", summary="不开流也能看一次完整答案（调试用）")
def preview(q: str = "退款多久到账", top_k: int = 3) -> dict:
    s = get_store()
    if s.count() == 0:
        seed(s)
    hits = s.search(q, top_k)
    citations = build_citations(hits)
    answer = extractive_answer(q, citations)
    return {
        "question": q,
        "answer": answer,
        "citations": [c.to_dict() for c in citations],
        "invalid_markers": check_markers(answer, len(citations)),
        "messages": build_messages(q, citations),
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

    async def collect(stream: AnswerStream, stop_after: int | None = None) -> list[dict]:
        out: list[dict] = []
        async for ev in stream.events():
            out.append(ev)
            if stop_after and len(out) >= stop_after:
                break
        return out

    def frames_of(stream: AnswerStream, limit: int | None = None) -> list[dict]:
        return asyncio.run(collect(stream, limit))

    print("Unit 27 · RAG 流式输出自检（离线，extractive 后端）\n")

    print("  —— SSE 帧格式 ——")
    frame = encode_sse("token", {"text": "年假"}, event_id="req_1")
    check("帧里有 event 行", "event: token" in frame)
    check("帧以空行结束", frame.endswith("\n\n"), repr(frame[-4:]))
    check("中文不转成 \\u 转义", "年假" in frame and "\\u" not in frame)
    parsed = parse_frames(frame)
    check("能被解析回一条事件", len(parsed) == 1 and parsed[0]["data"]["text"] == "年假", str(parsed))
    check("event:/data: 成对出现", frame.count("event:") == 1 and frame.count("data:") == 1)

    multi = encode_sse("token", {"text": "第一行\n第二行"})
    check("换行被 JSON 转义，不破坏帧", multi.count("\n\n") == 1, repr(multi))
    check("转义后仍能还原原文", parse_frames(multi)[0]["data"]["text"] == "第一行\n第二行")

    try:
        encode_sse("chunk", {"x": 1})
        check("未知事件类型被拒绝", False, "没抛异常")
    except StreamError as e:
        check("未知事件类型被拒绝", "未知事件类型" in str(e), str(e))
    check("四种事件类型固定", EVENTS == ("meta", "token", "done", "error"))

    print("\n  —— token 估算与上下文预算 ——")
    check("估算对空串为 0", estimate_tokens("") == 0)
    check("中文按字计", 18 <= estimate_tokens("退款金额原路退回，到账时间以支付渠道为准") <= 22,
          str(estimate_tokens("退款金额原路退回，到账时间以支付渠道为准")))
    check("估算随长度单调不减", estimate_tokens("年假" * 20) > estimate_tokens("年假" * 10))

    huge = Hit("big", "doc-x", "长文档.md", 1, "长" * 2000, 0.9)
    small = Hit("small", "doc-y", "短文档.md", 1, "短" * 20, 0.5)
    sel, dropped, used = select_chunks("问题", [huge, small], 300)
    check("装不下的长块被丢弃", dropped == ["big"], str(dropped))
    check("丢弃长块后仍收入短块", [h.chunk_id for h in sel] == ["small"], str([h.chunk_id for h in sel]))
    check("已用 token 不超过预算", used <= 300, str(used))
    try:
        select_chunks("问题", [small], 50)
        check("预算过小被拒绝", False, "没抛异常")
    except StreamError:
        check("预算过小被拒绝", True)

    print("\n  —— 引用与答案 ——")
    hits = [
        Hit("rf-2", "doc-refund", "退款政策.md", 2, "退款金额原路退回，到账时间通常 3–5 个工作日。", 0.42),
        Hit("rf-4", "doc-refund", "退款政策.md", 2, "已发货订单需先拒收或寄回，仓库签收后 3 个工作日内退款。", 0.31),
    ]
    citations = build_citations(hits)
    check("引用编号从 1 连续", [c.id for c in citations] == [1, 2], str([c.id for c in citations]))
    check("引用带 chunk_id 和页码", citations[0].chunk_id == "rf-2" and citations[0].page == 2)
    check("长文本被截成 snippet", make_snippet("字" * 200).endswith("…") and len(make_snippet("字" * 200)) == 121)

    answer = extractive_answer("退款多久到账", citations)
    check("答案里每段都有 [n]", all(f"[{c.id}]" in answer for c in citations))
    check("编号与引用数量一致", check_markers(answer, len(citations)) == [], str(answer))
    check("越界编号能被发现", check_markers("结论见 [7]", 2) == [7])
    check("没有资料时明确说无法回答", "无法回答" in extractive_answer("随便问问", []))

    print("\n  —— 事件流：顺序、引用只发一次、拼接还原 ——")
    full = AnswerStream(
        "req_t1", "退款多久到账", citations, extractive_producer("退款多久到账", citations),
        max_output_tokens=5000,
    )
    frames = frames_of(full)
    kinds = [f["event"] for f in frames]
    check("首帧是 meta", kinds[0] == "meta", str(kinds[:3]))
    check("末帧是 done", kinds[-1] == "done", str(kinds[-3:]))
    check("中间全是 token", set(kinds[1:-1]) == {"token"}, str(set(kinds[1:-1])))
    check("citations 只在 meta 里发一次", sum(k["data"].get("citations") is not None for k in frames) == 1)
    joined = "".join(f["data"]["text"] for f in frames if f["event"] == "token")
    check("token 顺序拼接 == 完整答案", joined == answer, f"{joined[:20]!r} vs {answer[:20]!r}")
    check("没有截断时 finish_reason=stop", frames[-1]["data"]["finish_reason"] == "stop")
    check("done 里的 output_tokens == 答案 token 数",
          frames[-1]["data"]["usage"]["output_tokens"] == estimate_tokens(answer))
    check("token 帧里不带引用字段", all("citations" not in f["data"] for f in frames if f["event"] == "token"))
    print(f"       共 {len(frames)} 帧：1 meta + {len(frames) - 2} token + 1 done")

    print("\n  —— 输出预算与截断 ——")
    short = AnswerStream(
        "req_t2", "退款多久到账", citations, extractive_producer("退款多久到账", citations),
        max_output_tokens=20,
    )
    s_frames = frames_of(short)
    done = s_frames[-1]["data"]
    check("超预算时标 truncated", done["truncated"] is True, str(done))
    check("超预算时 finish_reason=length", done["finish_reason"] == "length", str(done["finish_reason"]))
    check("截断后 output_tokens 不超上限", done["usage"]["output_tokens"] <= 20, str(done["usage"]))
    truncated_text = "".join(f["data"]["text"] for f in s_frames if f["event"] == "token")
    check("截断内容仍是完整答案的前缀", answer.startswith(truncated_text) and truncated_text, repr(truncated_text[:12]))
    check("截断后仍会发 done（不留悬空流）", s_frames[-1]["event"] == "done")

    print("\n  —— 断连与故障 ——")
    dis = AnswerStream("req_t3", "退款多久到账", citations,
                       extractive_producer("退款多久到账", citations), max_output_tokens=5000)

    async def break_early() -> bool:
        agen = dis.events()
        await agen.__anext__()   # meta
        await agen.__anext__()   # 1 个 token
        await agen.aclose()      # 模拟客户端断开
        return dis.disconnected

    check("生成器被关闭时能感知断连", asyncio.run(break_early()) is True)
    check("断连时不会把流标成完成", dis.completed is False and dis.disconnected is True)

    boom = AnswerStream("req_t4", "退款多久到账", citations,
                        extractive_producer("退款多久到账", citations),
                        max_output_tokens=5000, fail_after=2)

    async def drain(s: AnswerStream) -> tuple[list[dict], Exception | None]:
        out: list[dict] = []
        try:
            async for ev in s.events():
                out.append(ev)
        except Exception as e:  # noqa: BLE001
            return out, e
        return out, None

    b_frames, b_err = asyncio.run(drain(boom))
    check("故障会被抛到路由层（由它转成 error 事件）", isinstance(b_err, RuntimeError), str(b_err))
    check("故障后不再发 done", all(f["event"] != "done" for f in b_frames), str([f["event"] for f in b_frames]))

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    check("GET /health 返回事件类型", r.status_code == 200 and r.json()["events"] == list(EVENTS))

    r = client.post("/seed", json={"reset": True})
    check("POST /seed 灌入 12 条", r.status_code == 200 and r.json()["chunks"] == 12, str(r.json()))

    r = client.post("/v1/rag/stream", json={"question": "退款多久到账", "top_k": 3})
    check("流式接口返回 text/event-stream",
          r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream"),
          r.headers.get("content-type", ""))
    http_frames = parse_frames(r.text)
    check("HTTP 层能解析出完整事件序列",
          http_frames[0]["event"] == "meta" and http_frames[-1]["event"] == "done",
          str([f["event"] for f in http_frames[:3]]))

    rid = r.headers["x-request-id"]
    check("响应头带 X-Request-Id", rid.startswith("req_"), rid)
    check("响应头 request_id 与 meta 一致", http_frames[0]["data"]["request_id"] == rid)
    check("HTTP 层引用非空且带页码",
          len(http_frames[0]["data"]["citations"]) > 0
          and http_frames[0]["data"]["citations"][0]["page"] >= 1)

    r = client.post("/v1/rag/stream", json={"question": "退款多久到账", "debug_fail_after": 2})
    fail_frames = parse_frames(r.text)
    check("故障注入后以 error 收尾", fail_frames[-1]["event"] == "error", str(fail_frames[-1]))
    check("error 事件带 code 与 request_id",
          fail_frames[-1]["data"]["code"] == "GENERATION_FAILED"
          and fail_frames[-1]["data"]["request_id"].startswith("req_"))

    r = client.post("/v1/rag/stream", json={"question": ""})
    check("空问题返回 422", r.status_code == 422, str(r.status_code))

    r = client.get("/")
    check("GET / 返回页面", r.status_code == 200 and "<html" in r.text.lower())

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
