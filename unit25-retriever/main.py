"""Unit 25 · Retriever 模块 —— 检索只有一个入口，参数和日志都在这里。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8025
    打开 http://127.0.0.1:8025/

自检（离线，SQLite + 本地哈希向量）：
    uv run python main.py
"""

from __future__ import annotations

import os
import sys
import tempfile

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import embedding as emb
from retriever import (
    Filters,
    Retriever,
    RetrieverError,
    mrr,
    ndcg_at_k,
    recall_at_k,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")
DB_PATH = os.path.join(BASE_DIR, "data", "retriever.db")

app = FastAPI(title="Unit 25 · Retriever", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# 两个租户：acme 是"我们的"数据，beta 是对照组 —— 用来验证隔离不是空话
DEMO_CORPUS = {
    "acme": [
        {"id": "hb-1", "source": "员工手册.md", "page": 1, "text": "年假需提前 3 个工作日在系统提交申请，由直属主管审批。"},
        {"id": "hb-2", "source": "员工手册.md", "page": 1, "text": "病假需提供二级以上医院证明，全年累计不超过 30 天。"},
        {"id": "hb-3", "source": "员工手册.md", "page": 2, "text": "差旅报销需在出差结束后 10 个工作日内提交，附发票原件与行程单。"},
        {"id": "rf-1", "source": "退款政策.md", "page": 1, "text": "未发货订单可随时申请全额退款，1 个工作日内处理。"},
        {"id": "rf-2", "source": "退款政策.md", "page": 2, "text": "退款金额原路退回，到账时间通常 3–5 个工作日。"},
        {"id": "sc-1", "source": "安全规范.md", "page": 1, "text": "密码要求 12 位以上，每 90 天更换一次。"},
        {"id": "hb-4", "source": "员工手册.md", "page": 2, "text": "加班按 1.5 倍调休，调休需在 3 个月内使用，逾期作废。"},
        {"id": "hb-5", "source": "员工手册.md", "page": 1, "text": "每月累计迟到超过 60 分钟，按半天事假处理。"},
        {"id": "rf-3", "source": "退款政策.md", "page": 1, "text": "虚拟商品一经激活不支持退款，未激活可在 7 天内申请。"},
        {"id": "rf-4", "source": "退款政策.md", "page": 1, "text": "已发货订单需先拒收或寄回，仓库签收后 3 个工作日内退款。"},
        {"id": "rf-5", "source": "退款政策.md", "page": 3, "text": "超时未到账请提供订单号与支付流水号，由客服向渠道发起查询。"},
        {"id": "sc-2", "source": "安全规范.md", "page": 1, "text": "连续 5 次登录失败锁定账号 30 分钟，解锁需联系 IT 服务台。"},
        {"id": "sc-3", "source": "安全规范.md", "page": 2, "text": "生产数据禁止下载到个人设备，导出需审批并加水印。"},
        {"id": "pu-1", "source": "采购制度.md", "page": 1, "text": "单笔采购低于 5000 元由部门负责人审批。"},
        {"id": "pu-2", "source": "采购制度.md", "page": 1, "text": "采购金额超过 5 万元须走招标流程，合同送法务审核。"},
        {"id": "pu-3", "source": "采购制度.md", "page": 2, "text": "供应商准入需营业执照、开户许可证与近一年纳税证明。"},
        {"id": "rl-1", "source": "发布规范.md", "page": 1, "text": "发布流程：变更单 → 代码评审 → 预发验证 → 灰度 10% → 全量。"},
        {"id": "rl-2", "source": "发布规范.md", "page": 2, "text": "回滚后先检查数据库迁移是否可逆，再确认缓存已清理。"},
        {"id": "rl-3", "source": "发布规范.md", "page": 2, "text": "P1 事故 24 小时内出初稿，5 个工作日内完成复盘会。"},
        {"id": "tr-1", "source": "差旅标准.md", "page": 1, "text": "一线城市住宿每晚不超过 600 元，其他城市不超过 450 元。"},
        {"id": "tr-2", "source": "差旅标准.md", "page": 2, "text": "招待费单次不超过 800 元，须提前登记客户与事由。"},
        {"id": "tr-3", "source": "差旅标准.md", "page": 2, "text": "跨城出行优先高铁二等座，飞机经济舱需提前 7 天审批。"},
    ],
    "beta": [
        {"id": "b-1", "source": "beta手册.md", "page": 1, "text": "Beta 公司年假 20 天，无需审批。"},
    ],
}

# 评测集：relevant 是"应该被召回"的 chunk id，必须人工标。
# 分两组：literal 用原文措辞，paraphrase 换成用户真实会说的话。
# 分开统计，就是为了看见"字面检索在同义改写上会掉多少"。
EVAL_SET = [
    {"q": "年假提前几天申请", "relevant": {"hb-1"}, "kind": "literal"},
    {"q": "病假需要什么证明", "relevant": {"hb-2"}, "kind": "literal"},
    {"q": "差旅报销多久内提交", "relevant": {"hb-3"}, "kind": "literal"},
    {"q": "未发货订单怎么退款", "relevant": {"rf-1"}, "kind": "literal"},
    {"q": "退款几天到账", "relevant": {"rf-2"}, "kind": "literal"},
    {"q": "密码多久换一次", "relevant": {"sc-1"}, "kind": "literal"},
    {"q": "感冒了看病要开什么单据", "relevant": {"hb-2"}, "kind": "paraphrase"},
    {"q": "钱多久能退回来", "relevant": {"rf-2"}, "kind": "paraphrase"},
    {"q": "出差回来多久内要贴票", "relevant": {"hb-3"}, "kind": "paraphrase"},
]


def get_retriever() -> Retriever:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return Retriever(DB_PATH)


class SeedIn(BaseModel):
    reset: bool = False


class SearchIn(BaseModel):
    tenant: str = Field(min_length=1)
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    threshold: float = Field(default=0.0, ge=0, le=1)
    source: str | None = None
    page_min: int | None = None
    page_max: int | None = None


@app.get("/health", summary="库里有多少 chunk")
def health() -> dict:
    r = get_retriever()
    return {
        "ok": True,
        "embedder": r.embedder.name,
        "acme": r.count("acme"),
        "beta": r.count("beta"),
        "eval_questions": len(EVAL_SET),
    }


@app.post("/seed", summary="灌入演示语料")
def seed(req: SeedIn) -> dict:
    r = get_retriever()
    if req.reset:
        r.clear()
    n = sum(r.index(t, items) for t, items in DEMO_CORPUS.items())
    return {"indexed": n, "acme": r.count("acme"), "beta": r.count("beta")}


@app.post("/search", summary="检索（租户隔离 + 过滤 + 阈值）")
def search(req: SearchIn) -> dict:
    r = get_retriever()
    f = Filters(source=req.source, page_min=req.page_min, page_max=req.page_max)
    try:
        hits = r.search(req.tenant, req.query, req.top_k, req.threshold, f)
    except RetrieverError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "tenant": req.tenant,
        "query": req.query,
        "filters": f.to_dict(),
        "threshold": req.threshold,
        "hits": [h.to_dict() for h in hits],
    }


@app.get("/logs", summary="最近的检索日志")
def logs(tenant: str = Query(default="acme"), limit: int = Query(default=20, ge=1, le=200)) -> dict:
    return {"rows": get_retriever().logs(tenant, limit)}


@app.post("/eval", summary="跑评测集：按字面/改写分组看 Recall@K / MRR / nDCG")
def evaluate(top_k: int = Query(default=5, ge=1, le=20), threshold: float = Query(default=0.0, ge=0, le=1)) -> dict:
    r = get_retriever()
    if r.count("acme") == 0:
        raise HTTPException(status_code=409, detail="库里是空的，先调 POST /seed")

    details = []
    for item in EVAL_SET:
        hits = r.search("acme", item["q"], top_k, threshold, log=False)
        ids = [h.chunk_id for h in hits]
        details.append(
            {
                "q": item["q"],
                "kind": item["kind"],
                "retrieved": ids,
                "relevant": sorted(item["relevant"]),
                "recall": round(recall_at_k(ids, item["relevant"], top_k), 3),
                "mrr": round(mrr(ids, item["relevant"]), 3),
                "ndcg": round(ndcg_at_k(ids, item["relevant"], top_k), 3),
            }
        )

    def agg(rows: list[dict]) -> dict:
        if not rows:
            return {"n": 0, "recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
        return {
            "n": len(rows),
            "recall": round(sum(x["recall"] for x in rows) / len(rows), 3),
            "mrr": round(sum(x["mrr"] for x in rows) / len(rows), 3),
            "ndcg": round(sum(x["ndcg"] for x in rows) / len(rows), 3),
        }

    return {
        "top_k": top_k,
        "threshold": threshold,
        "literal": agg([x for x in details if x["kind"] == "literal"]),
        "paraphrase": agg([x for x in details if x["kind"] == "paraphrase"]),
        "overall": agg(details),
        "details": details,
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
        except RetrieverError as e:
            return True, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"抛了 {type(e).__name__}"
        return False, "没抛异常"

    print("Unit 25 · Retriever 自检（离线）\n")

    print("  —— 指标：先能手算，再谈调参 ——")
    check("recall@1 未命中 = 0", recall_at_k(["c", "a", "b"], {"a", "b"}, 1) == 0.0)
    check("recall@2 命中一半 = 0.5", recall_at_k(["c", "a", "b"], {"a", "b"}, 2) == 0.5)
    check("recall@3 全中 = 1.0", recall_at_k(["c", "a", "b"], {"a", "b"}, 3) == 1.0)
    check("MRR：第 2 位命中 = 0.5", abs(mrr(["c", "a", "b"], {"a", "b"}) - 0.5) < 1e-9)
    check("MRR：第 1 位命中 = 1.0", mrr(["a"], {"a"}) == 1.0)
    check("MRR：全不中 = 0", mrr(["x", "y"], {"a"}) == 0.0)
    check("nDCG：全中且有序 = 1.0", abs(ndcg_at_k(["a", "b"], {"a", "b"}, 2) - 1.0) < 1e-9)
    check("空 relevant 不除零", recall_at_k(["a"], set(), 3) == 0.0)

    tmp = tempfile.mkdtemp()
    r = Retriever(os.path.join(tmp, "t.db"))

    print("\n  —— 索引与基本检索 ——")
    n = sum(r.index(t, items) for t, items in DEMO_CORPUS.items())
    check("灌入 23 条 chunk", n == 23, str(n))
    check("acme 有 22 条", r.count("acme") == 22, str(r.count("acme")))

    hits = r.search("acme", "年假要提前几天申请", top_k=3)
    check("能召回年假相关块", hits and hits[0].chunk_id == "hb-1", str([h.chunk_id for h in hits]))
    check("分数降序", all(hits[i].score >= hits[i + 1].score for i in range(len(hits) - 1)))
    check("top_k 生效", len(hits) <= 3, str(len(hits)))
    check("空 query 被拒绝", raises(lambda: r.search("acme", "   "))[0])
    check("top_k=0 被拒绝", raises(lambda: r.search("acme", "年假", top_k=0))[0])
    check("threshold 越界被拒绝", raises(lambda: r.search("acme", "年假", threshold=1.5))[0])

    print("\n  —— 租户隔离（这是本单元最不能错的一条）——")
    beta_hits = r.search("acme", "Beta 公司年假 20 天", top_k=10)
    check("acme 搜不到 beta 的块", all(h.chunk_id != "b-1" for h in beta_hits), str([h.chunk_id for h in beta_hits]))
    check("beta 只能搜到自己的 1 条", len(r.search("beta", "年假", top_k=10)) == 1)
    check("空 tenant 直接报错", raises(lambda: r.search("", "年假"))[0])

    print("\n  —— 元数据过滤 ——")
    only_rf = r.search("acme", "退款", top_k=10, filters=Filters(source="退款政策.md"))
    check("按 source 过滤只返回该文件", {h.source for h in only_rf} == {"退款政策.md"}, str([h.source for h in only_rf]))
    p2 = r.search("acme", "报销 退款", top_k=10, filters=Filters(page_min=2))
    check("按页码过滤 page>=2", all(h.page >= 2 for h in p2) and p2, str([h.page for h in p2]))
    combo = r.search("acme", "差旅", top_k=10, filters=Filters(source_prefix="员工手册", page_max=1))
    check("组合过滤生效", all(h.source.startswith("员工手册") and h.page <= 1 for h in combo), str([(h.source, h.page) for h in combo]))

    print("\n  —— 阈值：宁可不答，也别答错 ——")
    low = r.search("acme", "数据库连接池怎么配", top_k=10)
    high = r.search("acme", "数据库连接池怎么配", top_k=10, threshold=0.5)
    check("高阈值把弱相关挡掉", len(high) <= len(low), f"{len(high)} vs {len(low)}")
    check("阈值生效时结果分数都达标", all(h.score >= 0.5 for h in high), str([round(h.score, 3) for h in high]))

    print("\n  —— 日志 ——")
    rows = r.logs("acme", 50)
    check("检索都记了日志", len(rows) >= 5, str(len(rows)))
    check("日志里有 filters", any(row["filters"] and row["filters"] != "{}" for row in rows))
    check("日志记录了 top_k 与耗时", rows[0]["top_k"] >= 1 and rows[0]["took_ms"] >= 0)
    check("日志按租户隔离", all(row["tenant_id"] == "acme" for row in rows))

    print("\n  —— 评测集（本单元的核心产出）——")
    per_q = []
    for item in EVAL_SET:
        ids = [h.chunk_id for h in r.search("acme", item["q"], 5, log=False)]
        per_q.append(
            (
                item["kind"],
                item["q"],
                ids,
                recall_at_k(ids, item["relevant"], 5),
                mrr(ids, item["relevant"]),
            )
        )
        print(f"       {item['kind']:10s} {item['q']:14s} recall@5={per_q[-1][3]:.2f} mrr={per_q[-1][4]:.2f}  {ids[:3]}")

    def avg(kind: str) -> tuple[float, float]:
        rows = [x for x in per_q if x[0] == kind]
        return (sum(x[3] for x in rows) / len(rows), sum(x[4] for x in rows) / len(rows))

    lit_r, lit_m = avg("literal")
    par_r, par_m = avg("paraphrase")
    print(f"       字面提问 recall@5={lit_r:.3f} MRR={lit_m:.3f} · 同义改写 recall@5={par_r:.3f} MRR={par_m:.3f}")
    check("字面提问几乎全中", lit_r >= 0.99, f"{lit_r:.3f}")
    check("同义改写明显掉分（字面向量的局限）", par_r < lit_r, f"{par_r:.3f} vs {lit_r:.3f}")
    check("改写场景的 MRR 也掉", par_m < lit_m, f"{par_m:.3f} vs {lit_m:.3f}")
    check("总体 recall@5 >= 0.75", (lit_r * 6 + par_r * 3) / 9 >= 0.75)

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r2 = client.post("/seed", json={"reset": True})
    check("POST /seed 重置并灌入", r2.status_code == 200 and r2.json()["acme"] == 22, str(r2.status_code))

    r2 = client.post("/search", json={"tenant": "acme", "query": "年假", "top_k": 3})
    check("POST /search 返回命中", r2.status_code == 200 and r2.json()["hits"], str(r2.status_code))

    r2 = client.post("/search", json={"tenant": "", "query": "年假"})
    check("POST /search 缺 tenant 返回 422/400", r2.status_code in (400, 422), str(r2.status_code))

    r2 = client.post("/eval")
    check("POST /eval 返回三项指标", r2.status_code == 200 and {"literal", "paraphrase", "overall"} <= set(r2.json()), str(r2.status_code))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
