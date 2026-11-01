"""Unit 26 · Reranker —— 召回 Top-20，重排 Top-5。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8026
    打开 http://127.0.0.1:8026/

自检（离线，不下载任何模型）：
    uv run python main.py
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import embedding as emb
from reranker import Scored, bm25_scores, mrr, ndcg_at_k, recall_at_k, rerank

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 26 · Reranker", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# 演示语料：23 条正常文档 + 2 条"关键词堆砌"的垃圾页。
# 堆砌页是故意放进去的 —— 向量召回会被它骗到，这正是重排要解决的问题。
DEMO = [
    ("hb-1", "员工手册.md", 1, "年假需提前 3 个工作日在系统提交申请，由直属主管审批。"),
    ("hb-2", "员工手册.md", 1, "病假需提供二级以上医院证明，全年累计不超过 30 天。"),
    ("hb-3", "员工手册.md", 2, "差旅报销需在出差结束后 10 个工作日内提交，附发票原件与行程单。"),
    ("hb-4", "员工手册.md", 2, "加班按 1.5 倍调休，调休需在 3 个月内使用，逾期作废。"),
    ("hb-5", "员工手册.md", 1, "每月累计迟到超过 60 分钟，按半天事假处理。"),
    ("rf-1", "退款政策.md", 1, "未发货订单可随时申请全额退款，1 个工作日内处理。"),
    ("rf-2", "退款政策.md", 2, "退款金额原路退回，到账时间通常 3–5 个工作日。"),
    ("rf-3", "退款政策.md", 1, "虚拟商品一经激活不支持退款，未激活可在 7 天内申请。"),
    ("rf-4", "退款政策.md", 1, "已发货订单需先拒收或寄回，仓库签收后 3 个工作日内退款。"),
    ("sc-1", "安全规范.md", 1, "密码要求 12 位以上，每 90 天更换一次。"),
    ("sc-2", "安全规范.md", 1, "连续 5 次登录失败锁定账号 30 分钟，解锁需联系 IT 服务台。"),
    ("sc-3", "安全规范.md", 2, "生产数据禁止下载到个人设备，导出需审批并加水印。"),
    ("pu-1", "采购制度.md", 1, "单笔采购低于 5000 元由部门负责人审批。"),
    ("pu-2", "采购制度.md", 1, "采购金额超过 5 万元须走招标流程，合同送法务审核。"),
    ("pu-3", "采购制度.md", 2, "供应商准入需营业执照、开户许可证与近一年纳税证明。"),
    ("rl-1", "发布规范.md", 1, "发布流程：变更单 → 代码评审 → 预发验证 → 灰度 10% → 全量。"),
    ("rl-2", "发布规范.md", 2, "回滚后先检查数据库迁移是否可逆，再确认缓存已清理。"),
    ("rl-3", "发布规范.md", 2, "P1 事故 24 小时内出初稿，5 个工作日内完成复盘会。"),
    ("tr-1", "差旅标准.md", 1, "一线城市住宿每晚不超过 600 元，其他城市不超过 450 元。"),
    ("tr-2", "差旅标准.md", 2, "招待费单次不超过 800 元，须提前登记客户与事由。"),
    ("tr-3", "差旅标准.md", 2, "跨城出行优先高铁二等座，飞机经济舱需提前 7 天审批。"),
    ("sp-1", "SEO垃圾页.md", 1, "退款 退款多久到账 退款流程 退款说明 退款查询 退款进度 退款到账 退款政策 退款客服 退款常见问题 退款注意事项 退款入口 退款链接 退款页面"),
    ("sp-2", "SEO垃圾页.md", 2, "年假 年假天数 年假申请 年假审批 年假流程 年假规定 年假政策 年假查询 年假剩余 年假结转 年假计算 年假入口"),
]

EVAL = [
    # 短问题：关键词集中，向量本来就能排对，重排不该把它搞坏
    {"q": "退款多久到账", "relevant": {"rf-2"}, "kind": "short"},
    {"q": "密码多久换一次", "relevant": {"sc-1"}, "kind": "short"},
    {"q": "回滚之后先看什么", "relevant": {"rl-2"}, "kind": "short"},
    # 长问题：一句话里混了四五个意图，向量被平均掉，这才是重排的用武之地
    {"q": "账号密码输错几次会被锁，锁了找谁解锁？", "relevant": {"sc-2"}, "kind": "long"},
    {"q": "客户签收后一周内想退款，要先寄回商品，仓库收到后几天能退钱？", "relevant": {"rf-4", "rf-2"}, "kind": "long"},
    {"q": "入职三年了年假有多少天，要提前几个工作日提交？", "relevant": {"hb-1"}, "kind": "long"},
]

EMBEDDER = emb.get_embedder()


def _scored_candidates(query: str) -> list[Scored]:
    """向量召回全部候选（本语料小，就当 Top-20 用）。"""
    qv = EMBEDDER._embed_one(query)
    out = [
        Scored(
            chunk_id=cid,
            text=text,
            score=0.0,
            vector_score=emb.cosine_similarity(qv, EMBEDDER._embed_one(text)),
        )
        for cid, _src, _page, text in DEMO
    ]
    out.sort(key=lambda c: -c.vector_score)
    return out


class RerankIn(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    recall_n: int = Field(default=20, ge=1, le=50)


@app.get("/health", summary="语料规模与重排器类型")
def health() -> dict:
    return {
        "ok": True,
        "chunks": len(DEMO),
        "stuffed": sum(1 for d in DEMO if d[1] == "SEO垃圾页.md"),
        "embedder": EMBEDDER.name,
        "reranker": "bm25(k1=1.5,b=0.75)",
        "eval_questions": len(EVAL),
    }


@app.post("/rerank", summary="先向量召回，再重排")
def do_rerank(req: RerankIn) -> dict:
    cands = _scored_candidates(req.query)[: req.recall_n]
    before = cands[: req.top_k]
    after = rerank(req.query, list(cands), req.top_k)
    return {
        "query": req.query,
        "recall_n": len(cands),
        "vector_top": [c.to_dict() for c in before],
        "reranked_top": [c.to_dict() for c in after],
    }


@app.post("/compare", summary="评测集：向量 vs 向量+重排（按短/长问题分组）")
def compare(top_k: int = 5) -> dict:
    rows = []
    for item in EVAL:
        cands = _scored_candidates(item["q"])[:20]
        vec_ids = [c.chunk_id for c in cands[:top_k]]
        rr = rerank(item["q"], list(cands), top_k)
        rr_ids = [c.chunk_id for c in rr]
        rows.append(
            {
                "q": item["q"],
                "kind": item["kind"],
                "relevant": sorted(item["relevant"]),
                "vector": vec_ids,
                "reranked": rr_ids,
                "mrr_before": round(mrr(vec_ids, item["relevant"]), 3),
                "mrr_after": round(mrr(rr_ids, item["relevant"]), 3),
                "ndcg_before": round(ndcg_at_k(vec_ids, item["relevant"], top_k), 3),
                "ndcg_after": round(ndcg_at_k(rr_ids, item["relevant"], top_k), 3),
                "recall_before": round(recall_at_k(vec_ids, item["relevant"], top_k), 3),
                "recall_after": round(recall_at_k(rr_ids, item["relevant"], top_k), 3),
            }
        )

    def avg(key: str, kind: str | None = None) -> float:
        subset = [r for r in rows if kind is None or r["kind"] == kind]
        return round(sum(r[key] for r in subset) / len(subset), 3) if subset else 0.0

    return {
        "top_k": top_k,
        "n": len(rows),
        "mrr_before": avg("mrr_before"),
        "mrr_after": avg("mrr_after"),
        "ndcg_before": avg("ndcg_before"),
        "ndcg_after": avg("ndcg_after"),
        "recall_before": avg("recall_before"),
        "recall_after": avg("recall_after"),
        "short": {"mrr_before": avg("mrr_before", "short"), "mrr_after": avg("mrr_after", "short")},
        "long": {"mrr_before": avg("mrr_before", "long"), "mrr_after": avg("mrr_after", "long")},
        "details": rows,
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

    print("Unit 26 · Reranker 自检（离线，不下载模型）\n")

    print("  —— BM25 本身的数学 ——")
    s = bm25_scores("退款", ["退款到账需要 3 天", "今天天气不错"])
    check("命中文档得分 > 0", s[0] > 0, str(s))
    check("不命中文档得分为 0", s[1] == 0.0, str(s))
    s2 = bm25_scores("退款", ["退款到账需要 3 天", "退款"])
    check("短文档得分更高（长度归一化）", s2[1] > s2[0], str(s2))
    s3 = bm25_scores("退款 退款", ["退款到账需要 3 天"])
    check("query 重复词不重复计分过多（tf 饱和）", s3[0] < 3 * s[0], f"{s3[0]:.3f} vs {s[0]:.3f}")
    check("空语料返回空", bm25_scores("退款", []) == [])
    check("空 query 全 0", bm25_scores("", ["退款到账"]) == [0.0])

    def _raises(fn) -> bool:
        try:
            fn()
        except ValueError:
            return True
        return False

    check("rerank top_k=0 抛 ValueError", _raises(lambda: rerank("退款", [Scored("a", "退款", 0.0)], 0)))

    print("\n  —— 关键词堆砌：字面重排也治不了（这是本单元的诚实结论）——")
    q = "退款多久到账"
    cands = _scored_candidates(q)
    vec_top3 = [c.chunk_id for c in cands[:3]]
    check("向量把堆砌页排进了前 3", "sp-1" in vec_top3, str(vec_top3))
    after = rerank(q, list(cands[:20]), 5)
    rr_top3 = [c.chunk_id for c in after[:3]]
    check("BM25 同样被堆砌页骗过（字面手段的边界）", "sp-1" in rr_top3[:2], str(rr_top3))
    check("重排保留向量排名，可追溯", all(c.vector_rank >= 1 for c in after))
    check("rerank_rank 从 1 连续", [c.rerank_rank for c in after] == list(range(1, len(after) + 1)))
    print("        ↑ 结论：BM25 有 IDF 和长度归一化，但堆砌页把 query 的每个 bigram 都塞满了，")
    print("          再好的字面打分也只能给高分。要治这个只有语义重排（bge-reranker / Cohere Rerank）。")

    print("\n  —— 重排能救什么、不能救什么 ——")
    q2 = "出差回来多久内要贴票"
    c2 = _scored_candidates(q2)[:20]
    vec2 = [c.chunk_id for c in c2[:5]]
    rr2 = [c.chunk_id for c in rerank(q2, list(c2), 5)]
    check("部分词重叠的改写，重排能救回来", "hb-3" in rr2 and rr2.index("hb-3") < (vec2.index("hb-3") if "hb-3" in vec2 else 9), f"{vec2[:3]} → {rr2[:3]}")

    print("\n  —— 评测集：重排到底赚了多少（按问题长度分组）——")
    rows = []
    for item in EVAL:
        cands = _scored_candidates(item["q"])[:20]
        v_ids = [c.chunk_id for c in cands[:5]]
        r_ids = [c.chunk_id for c in rerank(item["q"], list(cands), 5)]
        rows.append(
            {
                "kind": item["kind"],
                "q": item["q"],
                "v": v_ids,
                "r": r_ids,
                "mb": mrr(v_ids, item["relevant"]),
                "ma": mrr(r_ids, item["relevant"]),
            }
        )
        print(
            f"       [{rows[-1]['kind']:5s}] {item['q'][:16]:18s} 向量首条={v_ids[0]:6s} 重排首条={r_ids[0]:6s} "
            f"MRR {rows[-1]['mb']:.2f} → {rows[-1]['ma']:.2f}"
        )

    def group(kind: str) -> tuple[float, float]:
        g = [r for r in rows if r["kind"] == kind]
        return sum(r["mb"] for r in g) / len(g), sum(r["ma"] for r in g) / len(g)

    s_before, s_after = group("short")
    l_before, l_after = group("long")
    all_before = (s_before * 3 + l_before * 3) / 6
    all_after = (s_after * 3 + l_after * 3) / 6
    print(f"       短问题 MRR {s_before:.3f} → {s_after:.3f} · 长问题 MRR {l_before:.3f} → {l_after:.3f} · 合计 {all_before:.3f} → {all_after:.3f}")
    check("短问题重排不倒退", s_after >= s_before - 1e-9, f"{s_after:.3f} vs {s_before:.3f}")
    check("长问题重排有提升（向量被平均掉的那类）", l_after > l_before, f"{l_after:.3f} vs {l_before:.3f}")
    check("总体 MRR 提升", all_after > all_before, f"{all_after:.3f} vs {all_before:.3f}")

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    check("GET /health", r.status_code == 200 and r.json()["reranker"].startswith("bm25"))

    r = client.post("/rerank", json={"query": "退款多久到账", "top_k": 3})
    body = r.json()
    check("POST /rerank 返回前后两组", r.status_code == 200 and body["vector_top"] and body["reranked_top"])
    check(
        "POST /rerank 结果里同时保留向量排名与重排排名",
        all("vector_rank" in x and "rerank_rank" in x for x in body["reranked_top"]),
    )

    r = client.post("/compare")
    cb = r.json()
    check("POST /compare 返回前后指标", r.status_code == 200 and cb["mrr_after"] >= cb["mrr_before"])

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
