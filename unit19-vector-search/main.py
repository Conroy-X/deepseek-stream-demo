"""Unit 19 · 向量检索 —— 能查到不算本事，能说清召回率才算。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8019
    打开 http://127.0.0.1:8019/

自检（离线，本地哈希向量，不联网）：
    uv run python main.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import embedding as emb
from metrics import evaluate, recall_at_k, reciprocal_rank
from store import (
    FILTERABLE,
    MAX_TOP_K,
    SearchError,
    VectorStore,
    pack_vector,
    unpack_vector,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")
DB_PATH = os.path.join(BASE_DIR, "data", "vectors.db")

app = FastAPI(title="Unit 19 · 向量检索", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# ---------------------------------------------------------------------------
# 语料：16 段，来自 5 份文档。每段一道题，gold 是「答案原文 + 该段 id」。
# 不切成 40 字的碎块，因为太碎的块里一句话都放不下，测不出检索差别。
# ---------------------------------------------------------------------------
CORPUS = [
    {"id": "handbook:1:01", "source": "handbook.md", "page": 1, "category": "hr",
     "text": "年假：入职满一年享 5 天，满三年 10 天，满五年 15 天。年假需提前 3 个工作日在系统提交申请，由直属主管审批。"},
    {"id": "handbook:1:02", "source": "handbook.md", "page": 1, "category": "hr",
     "text": "跨年未休完的年假最多结转 5 天，结转部分须在次年 3 月底前用完。离职时未休年假按日工资折算补偿。"},
    {"id": "handbook:2:03", "source": "handbook.md", "page": 2, "category": "hr",
     "text": "病假需提供二级以上医院证明，全年累计不超过 30 天。超过 30 天的部分按事假处理，不计薪。"},
    {"id": "handbook:2:04", "source": "handbook.md", "page": 2, "category": "hr",
     "text": "迟到：每月累计迟到超过 60 分钟，按半天事假处理。因公共交通故障迟到的，凭运营方公告可申诉撤销。"},
    {"id": "handbook:3:05", "source": "handbook.md", "page": 3, "category": "hr",
     "text": "加班：工作日加班按 1.5 倍调休，休息日 2 倍，法定节假日 3 倍。调休需在 3 个月内使用，逾期作废。"},
    {"id": "policy:1:06", "source": "policy.md", "page": 1, "category": "refund",
     "text": "未发货订单可随时申请全额退款，1 个工作日内处理。已发货订单需先拒收或寄回，仓库签收后 3 个工作日内退款。"},
    {"id": "policy:1:07", "source": "policy.md", "page": 1, "category": "refund",
     "text": "虚拟商品一经激活不支持退款，未激活可在 7 天内申请。会员类服务按已使用天数折算，剩余部分可退。"},
    {"id": "policy:2:08", "source": "policy.md", "page": 2, "category": "refund",
     "text": "退款金额原路退回，到账时间以支付渠道为准，通常 3–5 个工作日。超时未到账请提供订单号与支付流水号。"},
    {"id": "ops:1:09", "source": "ops.md", "page": 1, "category": "ops",
     "text": "差旅报销需在出差结束后 10 个工作日内提交，附发票原件与行程单。超过 5000 元的报销需部门总监二次审批。"},
    {"id": "ops:1:10", "source": "ops.md", "page": 1, "category": "ops",
     "text": "住宿标准：一线城市每晚不超过 600 元，其他城市不超过 450 元。市内交通凭票实报，跨城优先选择高铁二等座。"},
    {"id": "ops:2:11", "source": "ops.md", "page": 2, "category": "ops",
     "text": "单笔采购金额低于 5000 元由部门负责人审批。5000 至 5 万元需采购部复核并比价三家。超过 5 万元须走招标流程。"},
    {"id": "ops:2:12", "source": "ops.md", "page": 2, "category": "ops",
     "text": "合同用印须在系统发起用印申请，附合同终稿与审批记录。对方盖章回传后，原件由行政归档，扫描件上传至合同库。"},
    {"id": "sec:1:13", "source": "sec.md", "page": 1, "category": "sec",
     "text": "密码要求 12 位以上且包含大小写与符号，每 90 天更换一次。连续 5 次登录失败锁定账号 30 分钟。"},
    {"id": "sec:1:14", "source": "sec.md", "page": 1, "category": "sec",
     "text": "所有生产数据禁止下载到个人设备。确需导出的，须走数据导出审批，导出文件加水印并设置 7 天有效期。"},
    {"id": "rel:1:15", "source": "rel.md", "page": 1, "category": "release",
     "text": "发布流程：提交变更单 → 代码评审通过 → 预发环境验证 → 灰度 10% → 全量。核心指标异常超过阈值自动暂停发布。"},
    {"id": "rel:1:16", "source": "rel.md", "page": 1, "category": "release",
     "text": "回滚后先检查数据库迁移是否可逆，再确认缓存已清理。回滚脚本须在变更单里预先填写，不允许现场编写。"},
]

# 干扰段：和问题共享大量字词，但**不含答案原文**。
# 没有它们的话，12 题在 Top-1 全命中，Recall 和 MRR 都是 1.0 ——
# 那种数字只能证明语料太干净，证明不了检索好用。
DISTRACTORS = [
    {"id": "faq:1:17", "source": "faq.md", "page": 1, "category": "hr",
     "text": "年假结转申请需在次年 1 月 15 日前提交，逾期视为放弃。年假天数随司龄增长，入职当月按比例折算，年假不可预支。"},
    {"id": "faq:1:18", "source": "faq.md", "page": 1, "category": "hr",
     "text": "病假期间工资按基本工资的 80% 发放，绩效部分不计。长期病假需每月复核一次，复核材料交 HR 备案。"},
    {"id": "faq:1:19", "source": "faq.md", "page": 1, "category": "hr",
     "text": "迟到记录每月 5 日汇总，可在考勤系统查看。忘记打卡需当天补卡，每月最多补 3 次。"},
    {"id": "faq:2:20", "source": "faq.md", "page": 2, "category": "hr",
     "text": "加班需提前在系统提交加班申请，事后补提需主管确认。法定节假日加班优先安排调休，不再另行支付加班费。"},
    {"id": "faq:2:21", "source": "faq.md", "page": 2, "category": "refund",
     "text": "退款申请可在 App 内「我的订单」发起，客服审核通过后进入财务流程。退款进度以订单详情页为准。"},
    {"id": "faq:2:22", "source": "faq.md", "page": 2, "category": "refund",
     "text": "到账时间受银行处理时效影响，遇节假日顺延。若超过 15 个工作日仍未到账，请提交工单核查。"},
    {"id": "faq:3:23", "source": "faq.md", "page": 3, "category": "ops",
     "text": "住宿发票须为增值税普通发票，抬头填写公司全称。超标部分由个人承担，报销时需附超标说明。"},
    {"id": "faq:3:24", "source": "faq.md", "page": 3, "category": "ops",
     "text": "招标公告在内部采购平台公示 3 个工作日，投标文件需密封提交。评标由采购部与需求部门共同参与。"},
    {"id": "faq:3:25", "source": "faq.md", "page": 3, "category": "sec",
     "text": "账号权限每季度复核一次，由部门负责人确认。离职人员账号当日禁用，权限回收记录留存一年。"},
    {"id": "faq:4:26", "source": "faq.md", "page": 4, "category": "release",
     "text": "回滚窗口建议安排在业务低峰期。回滚后 30 分钟内重点观察错误率与接口延迟，异常则二次回滚。"},
    {"id": "faq:4:27", "source": "faq.md", "page": 4, "category": "release",
     "text": "发布窗口为每周二、周四 20:00 之后，节假日前后一天禁止发布。紧急修复需值班经理审批。"},
]

ALL_CHUNKS = CORPUS + DISTRACTORS

# 12 道固定问题。expected 是「答案原文片段」，用来反查命中块是否真的带了答案，
# 防止「id 对了但内容被换掉」这种假命中。
QUESTIONS = [
    {"q": "年假要提前几天申请", "gold": "handbook:1:01", "expected": "提前 3 个工作日"},
    {"q": "没休完的年假能攒到明年吗", "gold": "handbook:1:02", "expected": "最多结转 5 天"},
    {"q": "病假需要什么证明", "gold": "handbook:2:03", "expected": "二级以上医院证明"},
    {"q": "迟到超过多久按半天事假处理", "gold": "handbook:2:04", "expected": "超过 60 分钟"},
    {"q": "加班调休多久内要使用", "gold": "handbook:3:05", "expected": "3 个月内使用"},
    {"q": "未发货订单多久退款", "gold": "policy:1:06", "expected": "1 个工作日内处理"},
    {"q": "虚拟商品能退款吗", "gold": "policy:1:07", "expected": "一经激活不支持退款"},
    {"q": "退款多久到账", "gold": "policy:2:08", "expected": "3–5 个工作日"},
    {"q": "住宿费一晚最多报多少", "gold": "ops:1:10", "expected": "不超过 600 元"},
    {"q": "多少金额需要走招标流程", "gold": "ops:2:11", "expected": "超过 5 万元须走招标流程"},
    {"q": "账号会被锁定多久", "gold": "sec:1:13", "expected": "锁定账号 30 分钟"},
    {"q": "回滚后先检查什么", "gold": "rel:1:16", "expected": "数据库迁移是否可逆"},
]


# ---------------------------------------------------------------------------
# 入库（首次请求时懒加载，避免 import 就卡住）
# ---------------------------------------------------------------------------
def _vectorize(texts: list[str]) -> list[list[float]]:
    """同步入口：自检和入库都要用，所以包一层 asyncio.run。"""
    return asyncio.run(emb.get_embedder().embed(texts))


def seed_store(path: str = DB_PATH) -> VectorStore:
    e = emb.get_embedder()
    store = VectorStore(path)
    st = store.stats()
    if st["count"] != len(ALL_CHUNKS) or st["dim"] != e.dim:
        store.clear()
        vecs = _vectorize([c["text"] for c in ALL_CHUNKS])
        store.upsert([{**c, "vector": v} for c, v in zip(ALL_CHUNKS, vecs)])
    return store


_store: VectorStore | None = None


def get_store() -> VectorStore:
    global _store
    if _store is None:
        _store = seed_store()
    return _store


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=5, ge=1, le=MAX_TOP_K)
    min_score: float | None = Field(default=None, ge=-1, le=1)
    filters: dict[str, str | int] = Field(default_factory=dict)


@app.get("/health", summary="语料规模、维度、可过滤字段")
def health() -> dict:
    store = get_store()
    st = store.stats()
    return {
        "ok": True,
        "chunks": st["count"],
        "dim": st["dim"],
        "chars": st["chars"],
        "sources": store.sources(),
        "embedder": emb.get_embedder().name,
        "filterable": sorted(FILTERABLE),
        "max_top_k": MAX_TOP_K,
        "questions": len(QUESTIONS),
    }


@app.post("/search", summary="Top-K 检索：阈值 + metadata 过滤")
async def search(req: SearchIn) -> dict:
    qv = (await emb.get_embedder().embed([req.query]))[0]
    try:
        res = get_store().search(qv, req.top_k, req.min_score, req.filters)
    except SearchError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"query": req.query, **res.to_dict()}


@app.get("/corpus", summary="库里的全部 chunk（调过滤时对着看）")
def corpus() -> dict:
    store = get_store()
    rows = store.conn.execute(
        "SELECT id, source, page, text, meta FROM chunks ORDER BY id"
    ).fetchall()
    return {
        "count": len(rows),
        "items": [
            {
                "id": r["id"],
                "source": r["source"],
                "page": r["page"],
                "text": r["text"],
                "category": json.loads(r["meta"]).get("category", ""),
            }
            for r in rows
        ],
    }


def _ranked_rows(store: VectorStore, qvecs: list[list[float]], top_k: int,
                 min_score: float | None, filters: dict | None = None) -> list[dict]:
    """每题跑一次检索，留下有序 id 列表 —— 指标全从这一份明细算，避免两套判定。"""
    rows = []
    for q, qv in zip(QUESTIONS, qvecs):
        res = store.search(qv, top_k=top_k, min_score=min_score, filters=filters)
        rows.append(
            {
                "q": q["q"],
                "ranked": [h.id for h in res.hits],
                "gold": [q["gold"]],
                "count": len(res.hits),
                "top_score": round(res.hits[0].score, 4) if res.hits else 0.0,
                "expected": q["expected"],
                "text_ok": any(q["expected"] in h.text for h in res.hits),
            }
        )
    return rows


@app.get("/eval", summary="12 题评测：Recall@K、MRR、阈值与 top_k 的影响")
def eval_route(
    top_k: int = Query(default=3, ge=1, le=MAX_TOP_K),
    min_score: float | None = Query(default=None, ge=-1, le=1),
) -> dict:
    store = get_store()
    qvecs = _vectorize([q["q"] for q in QUESTIONS])

    main_rows = _ranked_rows(store, qvecs, top_k, min_score)
    main = evaluate(main_rows, top_k)

    # 一次取 10 条，再按 k 截断 —— 比每题跑 4 次便宜，也保证各 K 看到的是同一份排序
    wide = _ranked_rows(store, qvecs, 10, min_score)
    sweep = [evaluate(wide, k) for k in (1, 3, 5, 10)]
    sweep = [{"k": s["k"], "recall": s["recall_at_k"], "mrr": s["mrr"], "hits": s["hits"]}
             for s in sweep]

    # 阈值扫描：看清「加阈值」到底换来了什么（少噪声 vs 少召回）
    thr = []
    for t in (None, 0.1, 0.15, 0.2):
        rows = _ranked_rows(store, qvecs, top_k, t)
        r = evaluate(rows, top_k)
        thr.append(
            {
                "min_score": t,
                "recall": r["recall_at_k"],
                "mrr": r["mrr"],
                "avg_returned": round(sum(x["count"] for x in rows) / len(rows), 2),
                "empty": sum(1 for x in rows if x["count"] == 0),
            }
        )

    return {
        "top_k": top_k,
        "min_score": min_score,
        "recall_at_k": main["recall_at_k"],
        "mrr": main["mrr"],
        "hits": main["hits"],
        "questions": main["questions"],
        "sweep": sweep,
        "thresholds": thr,
        "details": [
            {k: v for k, v in d.items() if k != "rr"} | {"rr": round(d["rr"], 4)}
            for d in main["details"]
        ],
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

    def raises(fn) -> tuple[bool, str]:
        try:
            fn()
        except SearchError as e:
            return True, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"抛了 {type(e).__name__}，应该是 SearchError"
        return False, "没抛异常"

    print("Unit 19 · 向量检索自检（离线，本地哈希向量）\n")

    # 维度 4 的小向量，手算可验证：余弦 = 点积 / (|a||b|)
    v = [0.6, 0.8, 0.0, 0.0]
    base = [
        {"id": "a", "text": "年假申请流程", "vector": [1.0, 0.0, 0.0, 0.0],
         "source": "handbook.md", "page": 1, "meta": {"category": "hr"}},
        {"id": "b", "text": "退款到账时间", "vector": [0.0, 1.0, 0.0, 0.0],
         "source": "policy.md", "page": 1, "meta": {"category": "refund"}},
        {"id": "c", "text": "退款原路退回", "vector": [0.0, 0.9, 0.1, 0.0],
         "source": "policy.md", "page": 2, "meta": {"category": "refund"}},
    ]

    print("  —— 序列化往返 ——")
    back = unpack_vector(pack_vector(v))
    check("struct 往返误差 < 1e-6", max(abs(x - y) for x, y in zip(v, back)) < 1e-6, str(back))
    json_size = len("[0.6000000238418579, 0.800000011920929, 0.0, 0.0]".encode())
    blob_size = len(pack_vector(v))
    check("struct 比 JSON 小（这是选它的唯一理由）", blob_size < json_size, f"{blob_size} vs {json_size}")
    ok, msg = raises(lambda: unpack_vector(b"\x00\x01\x02"))
    check("字节数不是 4 的倍数时报错", ok, msg)

    print("\n  —— 写入 / 读取 ——")
    store = VectorStore(":memory:", dim=4)
    check("空库 count = 0", store.count() == 0)
    store.upsert(base)
    check("写入 3 条", store.count() == 3)
    got = store.get("a")
    check("get 往返字段一致", got is not None and got.text == "年假申请流程" and got.page == 1)
    check("meta 往返有效（JSON 存、dict 出）", got is not None and got.meta.get("category") == "hr",
          str(got.meta if got else None))
    store.upsert([{"id": "a", "text": "年假申请流程（改）", "vector": [1.0, 0.0, 0.0, 0.0],
                   "source": "handbook.md", "page": 1, "meta": {"category": "hr"}}])
    check("同 id 再写是覆盖不是新增", store.count() == 3, str(store.count()))
    check("覆盖后内容已更新", store.get("a").text.endswith("（改）"))

    print("\n  —— 排序与 top_k ——")
    r = store.search([1.0, 0.0, 0.0, 0.0], top_k=3)
    check("返回条数 = top_k", len(r.hits) == 3, str(len(r.hits)))
    check("分数严格降序", all(r.hits[i].score >= r.hits[i + 1].score for i in range(len(r.hits) - 1)),
          str([h.score for h in r.hits]))
    check("查询向量与 a 完全同向 → score ≈ 1", abs(r.hits[0].score - 1.0) < 1e-6, str(r.hits[0].score))
    r2 = store.search([1.0, 0.0, 0.0, 0.0], top_k=1)
    check("top_k=1 只返回 1 条", len(r2.hits) == 1)
    check("scanned 报告的是过滤后的行数", r2.scanned == 3, str(r2.scanned))
    r3 = store.search([1.0, 0.0, 0.0, 0.0], top_k=50)
    check("top_k 大于库容量时不报错、返回全部", len(r3.hits) == 3, str(len(r3.hits)))

    print("\n  —— 阈值 ——")
    r4 = store.search([0.0, 1.0, 0.0, 0.0], top_k=3, min_score=0.995)
    check("阈值把低分项全部剪掉", len(r4.hits) == 1, str([h.score for h in r4.hits]))
    check("留下的项分数确实 >= 阈值", all(h.score >= 0.995 for h in r4.hits))
    r5 = store.search([0.0, 1.0, 0.0, 0.0], top_k=3, min_score=None)
    check("不带阈值时返回更多", len(r5.hits) > len(r4.hits), f"{len(r5.hits)} vs {len(r4.hits)}")

    print("\n  —— metadata 过滤 ——")
    rf = store.search([0.0, 1.0, 0.0, 0.0], top_k=5, filters={"source": "policy.md"})
    check("按 source 过滤生效", {h.source for h in rf.hits} == {"policy.md"}, str([h.source for h in rf.hits]))
    check("过滤后只剩 2 条", len(rf.hits) == 2, str(len(rf.hits)))
    rp = store.search([0.0, 1.0, 0.0, 0.0], top_k=5, filters={"page": 2})
    check("按 page 过滤生效", [h.id for h in rp.hits] == ["c"], str([h.id for h in rp.hits]))
    rc = store.search([0.0, 1.0, 0.0, 0.0], top_k=5, filters={"category": "refund"})
    check("按 meta.category 过滤生效", len(rc.hits) == 2, str(len(rc.hits)))
    rcombo = store.search([0.0, 1.0, 0.0, 0.0], top_k=5, min_score=0.0,
                          filters={"category": "refund", "page": 1})
    check("过滤 + 阈值可叠加", [h.id for h in rcombo.hits] == ["b"], str([h.id for h in rcombo.hits]))
    rnone = store.search([0.0, 1.0, 0.0, 0.0], top_k=5, filters={"category": "nope"})
    check("过滤无结果返回空列表（不报错）", rnone.hits == [])

    print("\n  —— 参数校验（拒绝要比静默出错强）——")
    ok, msg = raises(lambda: store.search([0.0, 1.0, 0.0, 0.0], top_k=0))
    check("top_k=0 被拒绝", ok, msg)
    ok, msg = raises(lambda: store.search([0.0, 1.0, 0.0, 0.0], top_k=999))
    check("top_k 超上限被拒绝", ok, msg)
    ok, msg = raises(lambda: store.search([0.0, 1.0, 0.0, 0.0], filters={"tenant; DROP": "x"}))
    check("未知 filter 字段被拒绝（白名单）", ok and "不支持" in msg, msg)
    ok, msg = raises(lambda: store.search([1.0, 0.0]))
    check("查询维度不匹配被拒绝", ok, msg)
    ok, msg = raises(lambda: store.search([]))
    check("空查询向量被拒绝", ok, msg)
    ok, msg = raises(lambda: VectorStore(":memory:", dim=4).upsert(
        [{"id": "x", "text": "t", "vector": [1.0, 0.0]}]))
    check("入库维度与库不一致被拒绝", ok, msg)

    empty = VectorStore(":memory:", dim=4)
    re_ = empty.search([1.0, 0.0, 0.0, 0.0])
    check("空库查询返回空 + scanned=0", re_.hits == [] and re_.scanned == 0, str(re_.to_dict()))

    print("\n  —— 指标：手算可验证的小例子 ——")
    ranked, gold = ["a", "b", "c"], ["b"]
    check("gold 排第 2 → Recall@1 = 0", recall_at_k(ranked, gold, 1) == 0.0)
    check("gold 排第 2 → Recall@2 = 1", recall_at_k(ranked, gold, 2) == 1.0)
    check("gold 排第 2 → RR = 1/2", abs(reciprocal_rank(ranked, gold) - 0.5) < 1e-9)
    check("gold 不在结果里 → RR = 0", reciprocal_rank(["a", "c"], ["b"]) == 0.0)
    check("gold 排第 1 → RR = 1", reciprocal_rank(["b", "a"], ["b"]) == 1.0)
    check("Recall 随 K 单调不减",
          recall_at_k(ranked, gold, 1) <= recall_at_k(ranked, gold, 2) <= recall_at_k(ranked, gold, 3))
    try:
        recall_at_k(ranked, gold, 0)
        check("K <= 0 被拒绝", False, "没抛异常")
    except ValueError:
        check("K <= 0 被拒绝", True)

    print("\n  —— 真实语料：12 题评测 ——")
    st = seed_store(os.path.join(tempfile.mkdtemp(prefix="u19-"), "v.db"))
    check("入库 27 段（16 段正文 + 11 段干扰）", st.count() == len(ALL_CHUNKS), str(st.count()))
    check("维度与 embedder 一致", st.stats()["dim"] == emb.get_embedder().dim, str(st.stats()["dim"]))
    check("6 份来源、页号覆盖到 4", len(st.sources()) == 6 and max(s["p2"] for s in st.sources()) == 4,
          str(st.sources())[:80])

    qvecs = _vectorize([q["q"] for q in QUESTIONS])
    wide = _ranked_rows(st, qvecs, 10, None)
    check("拿到的命中都真的包含答案原文", all(r["text_ok"] for r in wide if r["ranked"]),
          str([r["q"] for r in wide if r["ranked"] and not r["text_ok"]]))
    sweep = {k: evaluate(wide, k) for k in (1, 3, 5, 10)}
    for k in (1, 3, 5, 10):
        print(f"       K={k:<2d} recall@{k} = {sweep[k]['recall_at_k']:.3f}   MRR = {sweep[k]['mrr']:.3f}   "
              f"命中 {sweep[k]['hits']}/{len(QUESTIONS)}")
    check("Recall@1 >= 0.8（本地哈希向量的字面匹配基线）", sweep[1]["recall_at_k"] >= 0.8,
          str(sweep[1]["recall_at_k"]))
    check("Recall@3 >= 0.9", sweep[3]["recall_at_k"] >= 0.9, str(sweep[3]["recall_at_k"]))
    check("Recall@10 = 1.0（排在后面的也还在库里）", sweep[10]["recall_at_k"] == 1.0,
          str(sweep[10]["recall_at_k"]))
    ranks = {d["q"]: d["rank"] for d in evaluate(wide, 10)["details"]}
    late = {q: r for q, r in ranks.items() if r is None or r > 3}
    print(f"       Top-3 之外：{late or '无'}")
    check("改写过的问法掉出 Top-3（字面匹配的天花板，真 Embedding 才救得回来）",
          ranks.get("住宿费一晚最多报多少", 0) is not None and ranks["住宿费一晚最多报多少"] > 3,
          str(ranks.get("住宿费一晚最多报多少")))
    check("Recall 单调：@1 <= @3 <= @5 <= @10",
          sweep[1]["recall_at_k"] <= sweep[3]["recall_at_k"] <= sweep[5]["recall_at_k"] <= sweep[10]["recall_at_k"],
          str({k: v["recall_at_k"] for k, v in sweep.items()}))
    check("MRR 在 0.5..1 之间（Top-1 命中居多但不是全部）", 0.5 <= sweep[10]["mrr"] <= 1.0,
          str(sweep[10]["mrr"]))
    check("MRR 随 K 单调不减", sweep[1]["mrr"] <= sweep[3]["mrr"] <= sweep[10]["mrr"],
          str([sweep[k]["mrr"] for k in (1, 3, 10)]))
    check("评测明细逐题带 rank 字段", all("rank" in d for d in evaluate(wide, 3)["details"]))

    print("\n  —— 阈值与 top_k 的影响（这才是调参的依据）——")
    no_thr = _ranked_rows(st, qvecs, 3, None)
    stats_thr = {}
    for t in (0.1, 0.15, 0.2):
        rows = _ranked_rows(st, qvecs, 3, t)
        e = evaluate(rows, 3)
        avg = sum(r["count"] for r in rows) / len(rows)
        stats_thr[t] = (e, sum(r["count"] for r in rows), sum(1 for r in rows if not r["ranked"]))
        print(f"       min_score={t}  recall@3={e['recall_at_k']:.3f}  MRR={e['mrr']:.3f}  "
              f"平均返回 {avg:.2f} 条  完全空 {stats_thr[t][2]} 题")
    base_total = sum(r["count"] for r in no_thr)
    check("阈值 0.1 不损失召回（只是把明显无关的剪掉）",
          stats_thr[0.1][0]["recall_at_k"] == evaluate(no_thr, 3)["recall_at_k"],
          str(stats_thr[0.1][0]["recall_at_k"]))
    check("阈值 0.15 仍保住 Recall@3（且返回条数更少）",
          stats_thr[0.15][0]["recall_at_k"] >= 0.9 and stats_thr[0.15][1] < base_total,
          f"{stats_thr[0.15][0]['recall_at_k']} · {stats_thr[0.15][1]} vs {base_total}")
    check("阈值升到 0.2 开始误伤召回（阈值不是越高越好）",
          stats_thr[0.2][0]["recall_at_k"] < stats_thr[0.15][0]["recall_at_k"],
          str(stats_thr[0.2][0]["recall_at_k"]))
    check("平均返回条数随阈值单调不增",
          base_total >= stats_thr[0.1][1] >= stats_thr[0.15][1] >= stats_thr[0.2][1],
          str([base_total] + [stats_thr[t][1] for t in (0.1, 0.15, 0.2)]))
    check("阈值 0.2 下已有 2 题完全空 —— 这就是 Unit 20 拒答的触发点",
          stats_thr[0.2][2] >= 2, str(stats_thr[0.2][2]))
    r_top10 = st.search(qvecs[0], top_k=10)
    check("top_k=10 返回 10 条（库里有 16 条）", len(r_top10.hits) == 10, str(len(r_top10.hits)))
    check("top_k=10 的前 3 名与 top_k=3 完全一致",
          [h.id for h in r_top10.hits[:3]] == [h.id for h in st.search(qvecs[0], top_k=3).hits],
          "排序不稳定")

    print("\n  —— 持久化 ——")
    tmp = tempfile.mkdtemp(prefix="u19-p-")
    p = os.path.join(tmp, "v.db")
    s1 = VectorStore(p, dim=4)
    s1.upsert(base)
    s1.close()
    s2 = VectorStore(p)
    check("关掉再打开数据还在", s2.count() == 3, str(s2.count()))
    check("重新打开后向量可解、分数一致", abs(s2.search([1.0, 0, 0, 0], 1).hits[0].score - 1.0) < 1e-6)
    s2.close()

    print("\n  —— 暴力扫描的代价（自己看一眼再决定要不要上向量库）——")
    big = VectorStore(":memory:", dim=512)
    e_local = emb.get_embedder()
    vec512 = e_local._embed_one("年假申请流程，需提前三个工作日提交")
    big.upsert([{"id": f"x{i}", "text": f"占位文本 {i}", "vector": vec512,
                 "source": "synthetic.md", "page": 1, "meta": {}} for i in range(2000)])
    t0 = time.perf_counter()
    for _ in range(5):
        big.search(vec512, top_k=5)
    ms = (time.perf_counter() - t0) / 5 * 1000
    print(f"       2000 条 × 512 维：每次 {ms:.1f} ms（纯 Python 全量扫描，含 BLOB 解码）")
    check("2000 条全量扫描单次 < 500ms（几万条以内暴力够用）", ms < 500, f"{ms:.1f} ms")
    check("大库检索结果仍然正确（分数都是 1.0）",
          all(abs(h.score - 1.0) < 1e-5 for h in big.search(vec512, 5).hits))
    big.close()
    st.close()

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    check("GET /health 返回语料规模", r.status_code == 200 and r.json()["chunks"] == 27, str(r.status_code))
    check("/health 暴露 filterable 白名单", "category" in r.json()["filterable"])

    r = client.post("/search", json={"query": "退款多久到账", "top_k": 3})
    check("POST /search 返回命中和分数", r.status_code == 200 and r.json()["hits"][0]["score"] > 0,
          str(r.status_code))
    check("/search 结果带 source/page（Citation 要用）",
          all({"source", "page"} <= set(h) for h in r.json()["hits"]))

    r = client.post("/search", json={"query": "退款", "top_k": 3, "filters": {"category": "refund"}})
    check("POST /search 支持 metadata 过滤", r.status_code == 200 and
          all(h["meta"]["category"] == "refund" for h in r.json()["hits"]))

    r = client.post("/search", json={"query": "退款", "filters": {"nope": "x"}})
    check("非法 filter 返回 400", r.status_code == 400, str(r.status_code))
    r = client.post("/search", json={"query": "退款", "top_k": 0})
    check("top_k=0 返回 422（Pydantic 先拦住）", r.status_code == 422, str(r.status_code))

    r = client.get("/eval", params={"top_k": 3})
    check("GET /eval 返回 recall 与 mrr",
          r.status_code == 200 and 0 <= r.json()["recall_at_k"] <= 1 and 0 <= r.json()["mrr"] <= 1)
    check("/eval 带 K 扫描与阈值扫描",
          len(r.json()["sweep"]) == 4 and len(r.json()["thresholds"]) == 4)
    check("/eval 明细逐题可见", len(r.json()["details"]) == len(QUESTIONS))

    r = client.get("/corpus")
    check("GET /corpus 返回 27 段", r.status_code == 200 and r.json()["count"] == 27)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
