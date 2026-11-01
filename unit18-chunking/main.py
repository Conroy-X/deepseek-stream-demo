"""Unit 18 · 切块策略实验台 —— 切法不同，召回率会差一倍。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8018
    打开 http://127.0.0.1:8018/

自检（离线，用 Unit 15 的本地哈希向量）：
    uv run python main.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import embedding as emb
from chunker import (
    STRATEGIES,
    ChunkError,
    chunk_document,
    coverage,
    sentence_spans,
    split_sentences,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 18 · 切块", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# 内置文档：6 个话题、每段 3–5 句。故意做得比 chunk_size 大得多，
# 否则切法之间根本看不出差别（一句话的文档三种切法都一样）。
DEMO_PAGES = [
    """员工手册 · 假期与考勤

年假：入职满一年享 5 天，满三年 10 天，满五年 15 天。年假需提前 3 个工作日在系统提交申请，由直属主管审批。跨年未休完的年假最多结转 5 天，结转部分须在次年 3 月底前用完。离职时未休年假按日工资折算补偿。

病假：需提供二级以上医院证明，全年累计不超过 30 天。超过 30 天的部分按事假处理，不计薪。连续病假超过 3 天须同步提交诊断证明原件。

迟到：每月累计迟到超过 60 分钟，按半天事假处理。因公共交通故障迟到的，凭运营方公告可申诉撤销。

加班：工作日加班按 1.5 倍调休，休息日 2 倍，法定节假日 3 倍。调休需在 3 个月内使用，逾期作废。
""",
    """费用报销与差旅

差旅报销需在出差结束后 10 个工作日内提交，附发票原件与行程单。超过 5000 元的报销需部门总监二次审批。住宿标准：一线城市每晚不超过 600 元，其他城市不超过 450 元。

交通：市内交通凭票实报，跨城优先选择高铁二等座。飞机经济舱需提前 7 天审批，临时改签产生的差价由个人承担一半。

招待费：单次不超过 800 元，须提前在系统登记客户名称与事由。月度招待总额超过 2 万元的，需总经理签批。
""",
    """退款政策

未发货订单可随时申请全额退款，1 个工作日内处理。已发货订单需先拒收或寄回，仓库签收后 3 个工作日内退款。

虚拟商品一经激活不支持退款，未激活可在 7 天内申请。会员类服务按已使用天数折算，剩余部分可退。

退款金额原路退回，到账时间以支付渠道为准，通常 3–5 个工作日。超时未到账请提供订单号与支付流水号，由客服向渠道发起查询。
""",
    """采购与合同审批

单笔采购金额低于 5000 元由部门负责人审批。5000 至 5 万元需采购部复核并比价三家。超过 5 万元须走招标流程，合同同时送法务审核。

合同用印须在系统发起用印申请，附合同终稿与审批记录。对方盖章回传后，原件由行政归档，扫描件上传至合同库。

供应商准入：需提供营业执照、开户许可证与近一年纳税证明。连续两次交付延期超过 15 天的供应商，暂停合作并复核。
""",
    """数据安全与账号

所有生产数据禁止下载到个人设备。确需导出的，须走数据导出审批，导出文件加水印并设置 7 天有效期。

密码要求 12 位以上且包含大小写与符号，每 90 天更换一次。连续 5 次登录失败锁定账号 30 分钟，解锁需联系 IT 服务台。

离职当天回收全部账号权限。转岗的按新岗位最小权限重新授权，原权限在 3 个工作日内清理完毕。
""",
    """发布流程与回滚

发布流程：提交变更单 → 代码评审通过 → 预发环境验证 → 灰度 10% → 全量。灰度期间核心指标异常超过阈值自动暂停发布。

回滚：回滚后先检查数据库迁移是否可逆，再确认缓存已清理。回滚脚本须在变更单里预先填写，不允许现场编写。

事故复盘：P1 事故 24 小时内出初稿，5 个工作日内完成复盘会。复盘只谈机制，不追个人责任。
""",
]

# 固定问题集：每题必须有 expected（答案原文片段），否则没法算命中
QUESTIONS = [
    {"q": "年假要提前几天申请", "expected": "提前 3 个工作日"},
    {"q": "病假需要什么证明", "expected": "二级以上医院证明"},
    {"q": "迟到超过多久算事假", "expected": "超过 60 分钟"},
    {"q": "加班调休多久内要用完", "expected": "3 个月内使用"},
    {"q": "一线城市住宿标准", "expected": "不超过 600 元"},
    {"q": "招待费单次上限", "expected": "不超过 800 元"},
    {"q": "未发货订单多久退款", "expected": "1 个工作日内处理"},
    {"q": "虚拟商品能退款吗", "expected": "一经激活不支持退款"},
    {"q": "退款多久到账", "expected": "3–5 个工作日"},
    {"q": "多少金额需要招标", "expected": "超过 5 万元"},
    {"q": "账号锁定多久", "expected": "锁定账号 30 分钟"},
    {"q": "回滚后先检查什么", "expected": "数据库迁移是否可逆"},
]


class ChunkIn(BaseModel):
    text: str | None = None
    strategy: str = Field(default="recursive")
    size: int = Field(default=160, ge=20, le=2000)
    overlap: int = Field(default=30, ge=0, le=500)
    threshold: float = Field(default=0.05, ge=0, le=1)


class CompareIn(BaseModel):
    size: int = Field(default=160, ge=20, le=2000)
    overlap: int = Field(default=30, ge=0, le=500)
    top_k: int = Field(default=3, ge=1, le=10)


@app.get("/health", summary="三种策略与内置语料")
def health() -> dict:
    return {
        "ok": True,
        "strategies": list(STRATEGIES),
        "demo_pages": len(DEMO_PAGES),
        "demo_chars": sum(len(p) for p in DEMO_PAGES),
        "questions": len(QUESTIONS),
        "embedder": emb.get_embedder().name,
    }


@app.post("/chunk", summary="切一份文本，看每块长什么样")
def chunk(req: ChunkIn) -> dict:
    pages = [req.text] if req.text else DEMO_PAGES
    try:
        chunks = chunk_document(pages, req.strategy, req.size, req.overlap, req.threshold)
    except ChunkError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    total = len("\n".join(pages))
    return {
        "strategy": req.strategy,
        "size": req.size,
        "overlap": req.overlap,
        "count": len(chunks),
        "total_chars": total,
        "coverage": round(coverage(chunks, total), 4),
        "avg_chars": round(sum(c.chars for c in chunks) / len(chunks), 1) if chunks else 0,
        "chunks": [c.to_dict() for c in chunks],
    }


def _recall(chunks, q: str, top_k: int, embedder) -> dict:
    """命中 = 前 top_k 个 chunk 里存在包含答案原文的那一块。"""
    if not chunks:
        return {"hit": False, "rank": None}
    vecs = [embedder._embed_one(c.text) for c in chunks]
    qv = embedder._embed_one(q)
    order = sorted(range(len(chunks)), key=lambda i: -emb.cosine_similarity(qv, vecs[i]))
    expected = next(x["expected"] for x in QUESTIONS if x["q"] == q)
    for rank, idx in enumerate(order[:top_k], 1):
        if expected in chunks[idx].text:
            return {"hit": True, "rank": rank}
    # 没命中时，报告"第一个包含答案的块"排第几，方便调参
    true_rank = next((r for r, idx in enumerate(order, 1) if expected in chunks[idx].text), None)
    return {"hit": False, "rank": true_rank}


def _orphan_heading(c) -> bool:
    """块尾最后一行是不是一个孤立的短标题。"""
    tail = c.text.rstrip().split("\n")[-1].strip()
    return 0 < len(tail) <= 30 and not any(ch in tail for ch in "。！？；，")


def _adjacent_sims(pages: list[str], embedder) -> list[float]:
    """相邻句相似度分布 —— 语义切分的阈值就该按这个分布定。"""
    spans = sentence_spans("\n".join(pages))
    vecs = [embedder._embed_one(s) for s, _, _ in spans]
    return [emb.cosine_similarity(vecs[i - 1], vecs[i]) for i in range(1, len(vecs))] or [0.0]


@app.post("/compare", summary="同一份文档、同一批问题，比三种策略的命中率")
def compare(req: CompareIn) -> dict:
    e = emb.get_embedder()
    rows = []
    for s in STRATEGIES:
        try:
            chunks = chunk_document(DEMO_PAGES, s, req.size, req.overlap)
        except ChunkError as ex:
            raise HTTPException(status_code=400, detail=str(ex)) from ex
        details = [{**{"q": q["q"]}, **_recall(chunks, q["q"], req.top_k, e)} for q in QUESTIONS]
        hit = sum(d["hit"] for d in details)
        rows.append(
            {
                "strategy": s,
                "chunks": len(chunks),
                "avg_chars": round(sum(c.chars for c in chunks) / len(chunks), 1) if chunks else 0,
                f"recall@{req.top_k}": round(hit / len(QUESTIONS), 3),
                "hits": hit,
                "details": details,
            }
        )
    return {"size": req.size, "overlap": req.overlap, "top_k": req.top_k, "rows": rows}


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
        except ChunkError as e:
            return True, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"抛了 {type(e).__name__}"
        return False, "没抛异常"

    print("Unit 18 · 切块自检（离线，本地哈希向量）\n")
    total = len("\n".join(DEMO_PAGES))

    print("  —— 参数校验 ——")
    ok, msg = raises(lambda: chunk_document(DEMO_PAGES, "fixed", 10, 0))
    check("chunk_size 过小被拒绝", ok, msg)
    ok, msg = raises(lambda: chunk_document(DEMO_PAGES, "fixed", 100, 100))
    check("overlap >= size 被拒绝（否则切不动）", ok, msg)
    ok, msg = raises(lambda: chunk_document(DEMO_PAGES, "nope", 100, 10))
    check("未知策略被拒绝", ok, msg)

    print("\n  —— 固定长度 ——")
    fx = chunk_document(DEMO_PAGES, "fixed", 100, 20)
    check("每块不超过 size", all(c.chars <= 100 for c in fx), str(max(c.chars for c in fx)))
    check("块数大于 1", len(fx) > 1, str(len(fx)))
    check(
        "相邻块真的重叠",
        fx[0].text[-20:] == fx[1].text[:20],
        f"{fx[0].text[-20:]!r} vs {fx[1].text[:20]!r}",
    )
    check("去重覆盖率 = 100%", abs(coverage(fx, total) - 1.0) < 1e-9, str(coverage(fx, total)))
    check("index 从 0 连续", [c.index for c in fx] == list(range(len(fx))))
    check("id 唯一", len({c.id for c in fx}) == len(fx))

    print("\n  —— 递归 ——")
    rc = chunk_document(DEMO_PAGES, "recursive", 100, 0)
    check("每块不超过 size", all(c.chars <= 100 for c in rc), str(max(c.chars for c in rc)))
    # 递归为了不断句，会牺牲填充率：块比固定切法更多、更短，这是代价不是 bug
    check("块数不少于固定切法（断句的代价）", len(rc) >= len(fx), f"{len(rc)} vs {len(fx)}")
    ended = sum(c.text.rstrip().endswith(("。", "！", "？", "；", "：", "）")) for c in rc)
    check("绝大多数块在句末/段落处结束", ended >= len(rc) * 0.8, f"{ended}/{len(rc)}")
    check(
        "小标题没被孤立在块尾（否则它谁也不代表）",
        not any(_orphan_heading(c) for c in rc),
        str([c.text[-14:] for c in rc if _orphan_heading(c)]),
    )
    check("去重覆盖率 = 100%", abs(coverage(rc, total) - 1.0) < 1e-9, str(coverage(rc, total)))

    print("\n  —— 语义 ——")
    sims = _adjacent_sims(DEMO_PAGES, e_local := emb.get_embedder())
    med = sorted(sims)[len(sims) // 2]
    print(f"       相邻句相似度：中位数 {med:.3f} · 最大 {max(sims):.3f}（本地哈希向量的字面基线）")
    check("相邻句相似度确实很低（说明阈值必须按模型标定）", med < 0.2, f"{med:.3f}")
    sm = chunk_document(DEMO_PAGES, "semantic", 160, 0, 0.05)
    check("每块不超过 size", all(c.chars <= 160 for c in sm), str(max(c.chars for c in sm)))
    check("话题切换处被断开（不是整篇一块）", 2 <= len(sm) <= 40, str(len(sm)))
    check(
        "年假块里不含退款内容",
        any("年假" in c.text and "退款" not in c.text for c in sm),
        str([c.text[:12] for c in sm]),
    )

    print("\n  —— 跨页与边界 ——")
    pages = ["年假政策。" * 30, "退款政策。" * 30]
    cross = chunk_document(pages, "fixed", 120, 0)
    check("跨页文档页号 > 1 出现", max(c.page for c in cross) == 2, str(max(c.page for c in cross)))
    check("第一块在第 1 页", cross[0].page == 1, str(cross[0].page))
    check("空文档返回空列表", chunk_document(["", "  "], "recursive", 100, 0) == [])
    check("单句文档也能切", len(chunk_document(["就一句话。"], "semantic", 100, 0)) == 1)
    check(
        "切句函数把标点留在前句",
        split_sentences("第一句。第二句！") == ["第一句。", "第二句！"],
        str(split_sentences("第一句。第二句！")),
    )

    print("\n  —— 固定问题集：三种策略对比（本单元的重点）——")
    e = emb.get_embedder()
    table = []
    for s in STRATEGIES:
        chunks = chunk_document(DEMO_PAGES, s, 160, 30)
        hits = sum(_recall(chunks, q["q"], 3, e)["hit"] for q in QUESTIONS)
        table.append((s, len(chunks), hits))
        print(f"       {s:10s} 块数={len(chunks):2d}  命中 {hits}/{len(QUESTIONS)}  recall@3={hits / len(QUESTIONS):.2f}")
    check("三种策略块数不完全相同", len({t[1] for t in table}) > 1, str(table))
    check("至少一种策略命中过半", max(t[2] for t in table) > len(QUESTIONS) / 2, str(table))

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    check("GET /health", r.status_code == 200 and "semantic" in r.json()["strategies"])

    r = client.post("/chunk", json={"strategy": "recursive", "size": 120, "overlap": 20})
    check("POST /chunk 返回块列表", r.status_code == 200 and r.json()["count"] > 1, str(r.status_code))

    r = client.post("/chunk", json={"strategy": "fixed", "size": 100, "overlap": 100})
    check("POST /chunk 非法参数返回 400", r.status_code == 400, str(r.status_code))

    r = client.post("/compare", json={"size": 160, "overlap": 30, "top_k": 3})
    check("POST /compare 返回三行", r.status_code == 200 and len(r.json()["rows"]) == 3)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
