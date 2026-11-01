"""Unit 23 · 文档处理流水线 —— 上传成功不等于可检索。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8023
    打开 http://127.0.0.1:8023/

自检（离线，SQLite 落盘到临时目录）：
    uv run python main.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from contextlib import contextmanager
from typing import Iterator

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import pipeline
from parser import parse_bytes

PORT = 8023

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")
DB_PATH = os.environ.get("PIPELINE_DB") or os.path.join(BASE_DIR, "data", "pipeline.db")

# /docs 是本单元的文档列表接口，所以把 swagger 挪走（否则会被 FastAPI 内置路由抢走）
app = FastAPI(title="Unit 23 · 文档流水线", version="0.1.0", docs_url="/swagger", redoc_url=None)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

# 内置语料：4 篇，每篇 550–800 字。故意比 chunk_size(200) 大好几倍，
# 否则每篇只切出 1 块，"流水线"看起来跟没跑一样。
DEMO_DOCS: dict[str, tuple[str, str]] = {
    "员工手册.md": (
        "md",
        """# 员工手册（2026 版）

## 一、年假

入职满一年享 5 天年假，满三年 10 天，满五年 15 天。年假需提前 3 个工作日在系统提交申请，
由直属主管审批，审批不通过要写明理由。跨年未休完的年假最多结转 5 天，结转部分须在次年
3 月底前用完，过期清零。离职时未休年假按日工资折算补偿，折算基数为离职前 12 个月的平均月薪，
随最后一个工资周期发放。

## 二、病假

病假需提供二级以上医院证明，全年累计不超过 30 天。超过 30 天的部分按事假处理，不计薪。
连续病假超过 3 天的，须同步提交诊断证明原件，缺件按事假计。病假期间工资按当地最低标准的
80% 发放，社保照常缴纳。

## 三、加班与调休

工作日加班按 1.5 倍调休，休息日 2 倍，法定节假日 3 倍。调休需在 3 个月内使用，逾期作废。
调休申请同样走系统审批，不接受口头补休。每月加班总时长超过 36 小时的，主管须书面说明原因，
并同步给 HRBP 备案。

## 四、费用报销

差旅报销需在出差结束后 10 个工作日内提交，附发票原件与行程单。超过 5000 元的报销需部门总监
二次审批。住宿标准：一线城市每晚不超过 600 元，其他城市不超过 450 元。市内交通凭票实报，
跨城优先选择高铁二等座。发票抬头必须是公司全称，个人抬头的发票一律退回。
""",
    ),
    "退款政策.txt": (
        "txt",
        """退款政策说明（2026 年 1 月修订）

一、退款条件

1. 未发货订单：可随时申请全额退款，1 个工作日内处理。
2. 已发货订单：需先拒收或寄回，仓库签收后 3 个工作日内退款。
3. 虚拟商品：一经激活不支持退款，未激活可 7 天内申请。
4. 会员类服务：按已使用天数折算，剩余部分可退，折算不足 1 元的按 1 元计。

二、到账时间

退款金额原路退回，到账时间以支付渠道为准，通常 3–5 个工作日。
超时未到账请提供订单号与支付流水号，由客服向支付渠道发起查询，查询周期不超过 5 个工作日。
单笔退款超过 5000 元的，需财务二次复核后才发起打款。

三、换货与发票

同款换尺码不收手续费，换不同商品按退货再下单处理。发票已开具的订单，退款需先退回发票原件，
电子发票请在系统里做红冲，否则财务无法入账。现金支付的订单只能退到银行卡，需提供开户行信息。
""",
    ),
    "数据安全.md": (
        "md",
        """# 数据安全与账号管理

## 数据导出

所有生产数据禁止下载到个人设备。确需导出的，须走数据导出审批，导出文件加水印并设置 7 天有效期。
审批由数据所有者与安全团队双签，缺一不可。导出记录保留 180 天，供事后审计。批量导出超过
1 万条记录的，必须说明用途与保留期限，并在用完后主动删除。

## 账号与密码

密码要求 12 位以上且包含大小写与符号，每 90 天更换一次。连续 5 次登录失败锁定账号 30 分钟，
解锁需联系 IT 服务台。禁止在多个系统复用同一密码，也禁止把密码写进代码仓库。生产环境的
数据库账号只允许通过跳板机访问，直连一律拒绝。

## 离职与转岗

离职当天回收全部账号权限。转岗的按新岗位最小权限重新授权，原权限在 3 个工作日内清理完毕。
季度权限复核由各部门负责人确认，未确认的权限默认回收。外包人员的账号有效期不超过合同期。

## 日志与审计

核心系统的操作日志保留 1 年，登录日志保留 180 天。日志里不允许出现明文密码、身份证号与
银行卡号，确需记录的要做掩码。安全团队每季度抽查一次日志完整性，缺失的按事故处理。
""",
    ),
    "发布流程.md": (
        "md",
        """# 发布流程与回滚

## 发布

提交变更单 → 代码评审通过 → 预发环境验证 → 灰度 10% → 全量。灰度期间核心指标异常超过阈值
自动暂停发布。变更单必须写明影响面与回滚方案，写"看情况"的一律打回。发布窗口默认在工作日
10:00–17:00，周五下午与节假日前一天不发布，除非有值班负责人签字。

## 回滚

回滚后先检查数据库迁移是否可逆，再确认缓存已清理。回滚脚本须在变更单里预先填写，
不允许现场编写。回滚完成需在群里同步一句结论，而不是只在小窗里说"好了"。
回滚后 24 小时内补一份简要说明，写清触发原因与后续动作。

## 事故复盘

P1 事故 24 小时内出初稿，5 个工作日内完成复盘会。复盘只谈机制，不追个人责任。
每条改进项必须有负责人与截止时间，否则下次复盘还要再提一遍。连续两次因同一原因出事故的，
升级为流程问题，由技术负责人牵头改流程。

## 值班

值班表按周轮换，交接时同步未关闭的告警与临时绕过。告警 5 分钟内响应，30 分钟内给出初步判断。
""",
    ),
}


@contextmanager
def db() -> Iterator["pipeline.sqlite3.Connection"]:
    """一个请求一个连接。别做成全局单例：sqlite 连接跨线程用会炸。"""
    with pipeline.connect(DB_PATH) as conn:
        yield conn


class TickIn(BaseModel):
    worker: str = Field(default="worker-1", max_length=40)
    lease_seconds: float = Field(default=pipeline.LEASE_SECONDS, gt=0, le=3600)


@app.get("/health", summary="库里有多少文档/任务/chunk，以及调度参数")
def health() -> dict:
    with db() as conn:
        st = pipeline.stats(conn)
    return {
        "ok": True,
        "port": PORT,
        "db": DB_PATH,
        "pipeline_version": pipeline.PIPELINE_VERSION,
        "max_attempts": pipeline.MAX_ATTEMPTS,
        "lease_seconds": pipeline.LEASE_SECONDS,
        "chunk": {
            "strategy": pipeline.CHUNK_STRATEGY,
            "size": pipeline.CHUNK_SIZE,
            "overlap": pipeline.CHUNK_OVERLAP,
        },
        "stats": st,
        "samples": sorted(DEMO_DOCS),
    }


@app.post("/docs", status_code=201, summary="上传文档 → 落库 pending + 建任务（还没解析）")
async def create_doc(
    file: UploadFile | None = File(default=None),
    name: str = Form(default=""),
    text: str = Form(default=""),
    fail_inject: bool = Form(default=False),
) -> dict:
    if file is not None:
        raw = await file.read()
        fname = (name or file.filename or "upload.bin").strip()
    elif text.strip():
        raw = text.encode("utf-8")
        fname = (name or "粘贴文本.txt").strip()
    else:
        raise HTTPException(status_code=400, detail="要么上传文件，要么给 text")
    if not raw:
        raise HTTPException(status_code=400, detail="文件是空的（0 字节）")

    with db() as conn:
        try:
            doc = pipeline.create_document(conn, fname, raw, fail_inject)
        except pipeline.PipelineError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        task = pipeline.enqueue(conn, doc["id"])
    return {
        "document": doc,
        "task": task,
        "note": "上传成功 ≠ 可检索：现在只是 pending，等 worker tick 才会 Parse→Chunk",
    }


@app.get("/docs", summary="文档列表（带最后一条任务的状态）")
def list_docs(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    with db() as conn:
        items = pipeline.list_documents(conn, limit)
    return {"count": len(items), "items": items}


@app.get("/docs/{doc_id}", summary="单个文档 + 切出来的 chunk")
def read_doc(doc_id: str) -> dict:
    with db() as conn:
        doc = pipeline.get_document(conn, doc_id)
        if doc is None:
            raise HTTPException(status_code=404, detail=f"没有文档 {doc_id}")
        chunks = pipeline.document_chunks(conn, doc_id)
        task = next(
            (t for t in pipeline.list_tasks(conn, 200) if t["document_id"] == doc_id), None
        )
    return {"document": doc, "task": task, "chunk_count": len(chunks), "chunks": chunks}


@app.post("/docs/{doc_id}/retry", summary="把失败的任务重置为 pending（配额重新给满）")
def retry_doc(doc_id: str) -> dict:
    with db() as conn:
        if pipeline.get_document(conn, doc_id) is None:
            raise HTTPException(status_code=404, detail=f"没有文档 {doc_id}")
        try:
            res = pipeline.retry_document(conn, doc_id)
        except pipeline.PipelineError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
    return res


@app.get("/tasks", summary="任务队列（新的在前）")
def list_tasks(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    with db() as conn:
        items = pipeline.list_tasks(conn, limit)
        st = pipeline.stats(conn)
    return {"count": len(items), "stats": st, "items": items}


@app.post("/worker/tick", summary="领一条任务并跑完（没有活就立刻返回）")
def worker_tick(req: TickIn | None = None) -> dict:
    body = req or TickIn()
    with db() as conn:
        res = pipeline.tick(conn, body.worker, body.lease_seconds)
        res["stats"] = pipeline.stats(conn)
    return res


@app.post("/worker/drain", summary="循环 tick 直到没有可领的任务（有上限）")
def worker_drain(
    req: TickIn | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    body = req or TickIn()
    with db() as conn:
        results = pipeline.drain(conn, body.worker, limit, body.lease_seconds)
        st = pipeline.stats(conn)
    return {"processed": len(results), "results": results, "stats": st}


@app.post("/demo/seed", status_code=201, summary="把内置文档全部入队（页面点一下就有东西看）")
def seed_demo() -> dict:
    with db() as conn:
        out = []
        for fname in sorted(DEMO_DOCS):
            _, content = DEMO_DOCS[fname]
            doc = pipeline.create_document(conn, fname, content.encode("utf-8"))
            task = pipeline.enqueue(conn, doc["id"])
            out.append({"document": doc, "task_id": task["id"]})
    return {"seeded": len(out), "items": out, "hint": "接着 POST /worker/drain 就能跑完"}


@app.get("/", include_in_schema=False)
def index():
    if os.path.exists(STATIC_INDEX):
        return FileResponse(STATIC_INDEX)
    return {"hint": "static/index.html 不存在，看 /swagger"}


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
        except pipeline.PipelineError as e:
            return True, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"抛了 {type(e).__name__}: {e}"
        return False, "没抛异常"

    print("Unit 23 · 文档流水线自检（离线，SQLite）\n")

    TMP = tempfile.mkdtemp(prefix="unit23-")
    DB_PATH = os.path.join(TMP, "pipeline.db")  # 不污染 data/，也不受上次运行影响
    cm = pipeline.connect(DB_PATH)
    conn = cm.__enter__()

    SAMPLE_MD = DEMO_DOCS["员工手册.md"][1].encode("utf-8")
    T = 1_000_000.0

    # ----------------------------------------------------------------- 1
    print("  —— 建文档与入队 ——")
    doc = pipeline.create_document(conn, "员工手册.md", SAMPLE_MD)
    check("新文档落 pending", doc["status"] == "pending", doc["status"])
    check("上传时还没解析（chars=0）", doc["chars"] == 0, str(doc["chars"]))

    task = pipeline.enqueue(conn, doc["id"])
    check("入队产生一条 pending 任务", task["status"] == "pending" and task["attempts"] == 0,
          str(task["status"]))
    again = pipeline.enqueue(conn, doc["id"])
    check("同一文档重复入队不新增任务", again["created"] is False and again["id"] == task["id"],
          str(again["created"]))
    check("tasks 表里这个文档只有 1 条",
          conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE document_id=?",
                       (doc["id"],)).fetchone()["n"] == 1)
    check("重复入队不改文档状态", pipeline.get_document(conn, doc["id"])["status"] == "pending")

    # ----------------------------------------------------------------- 2
    print("\n  —— 领取与租约 ——")
    t1 = pipeline.claim(conn, "w1", 30, now=T)
    check("claim 拿到任务并转 running", t1 is not None and t1["status"] == "running", str(t1))
    check("attempts 在 claim 时 +1（不是失败时）", t1["attempts"] == 1, str(t1["attempts"]))
    check("租约到期时间 = now + lease", t1["leased_until"] == T + 30, str(t1["leased_until"]))
    check("claim 同时把文档标成 running",
          pipeline.get_document(conn, doc["id"])["status"] == "running")
    check("租约未过期，别的 worker 抢不到", pipeline.claim(conn, "w2", 30, now=T + 10) is None)

    t2 = pipeline.claim(conn, "w2", 30, now=T + 31)
    check("租约过期可被接管", t2 is not None and t2["worker"] == "w2", str(t2 and t2["worker"]))
    check("接管后 attempts 继续累加（崩溃也算一次）", t2["attempts"] == 2, str(t2["attempts"]))

    # ----------------------------------------------------------------- 3
    print("\n  —— 处理：Parse → Chunk ——")
    res = pipeline.process(conn, t2, now=T + 32)
    check("处理完任务变 done", res["status"] == "done", str(res["status"]))
    check("文档状态变 done", pipeline.get_document(conn, doc["id"])["status"] == "done")
    n_chunks = pipeline.count_chunks(conn, doc["id"])
    check("chunk 真的写进去了", n_chunks > 1, str(n_chunks))
    check("chunk_count 与实际行数一致",
          pipeline.get_document(conn, doc["id"])["chunk_count"] == n_chunks, str(n_chunks))
    rows = pipeline.document_chunks(conn, doc["id"])
    check("每块不超过 chunk_size", all(r["chars"] <= pipeline.CHUNK_SIZE for r in rows),
          str(max(r["chars"] for r in rows)))
    check("idx 从 0 连续", [r["idx"] for r in rows] == list(range(n_chunks)))
    parsed_text = parse_bytes("员工手册.md", SAMPLE_MD).text
    check("每块文本都能在解析后的原文里找到（没编造）",
          all(parsed_text.find(r["text"]) >= 0 for r in rows))
    check("首块从第 1 页、偏移 0 开始", rows[0]["page"] == 1 and rows[0]["start"] == 0,
          str(rows[0]["start"]))
    check("解析出的 kind 是 md", res["kind"] == "md", str(res["kind"]))
    print(f"       员工手册.md：{res['chars']} 字 / {n_chunks} 块 / 平均 {res['avg_chars']} 字 / "
          f"{res['ms']} ms")

    # ----------------------------------------------------------------- 4
    print("\n  —— 幂等：重复执行不产生重复 chunk ——")
    check("done 的任务不会再被领取", pipeline.claim(conn, "w3", 30, now=T + 60) is None)
    check("tick 空队列返回 claimed=False",
          pipeline.tick(conn, "w3", 30, now=T + 60)["claimed"] is False)

    first_text = rows[0]["text"]
    counts = []
    for i in range(3):
        # 模拟"worker 干到一半被接管"：任务被打回 pending，再跑一遍
        with pipeline.tx(conn):
            conn.execute(
                "UPDATE tasks SET status='pending', attempts=0, leased_until=NULL WHERE id=?",
                (task["id"],),
            )
        r = pipeline.tick(conn, f"retry-w{i}", 30, now=T + 100 + i)
        counts.append((r["status"], r["chunks"], pipeline.count_chunks(conn, doc["id"])))
    check("重试 3 次每次都是 done", all(c[0] == "done" for c in counts), str(counts))
    check("重试 3 次后 chunk 数不变",
          len({c[2] for c in counts}) == 1 and counts[0][2] == n_chunks, str(counts))
    check("重试后块内容一致（首块没被改写）",
          pipeline.document_chunks(conn, doc["id"])[0]["text"] == first_text)
    dup = conn.execute(
        "SELECT COUNT(*) AS n, COUNT(DISTINCT idx) AS d FROM chunks WHERE document_id=?",
        (doc["id"],),
    ).fetchone()
    check("chunks 表没有 (document_id, idx) 重复", dup["n"] == dup["d"],
          f"{dup['n']} vs {dup['d']}")

    # ----------------------------------------------------------------- 5
    print("\n  —— 失败注入：可诊断、有配额、不卡 running ——")
    bad = pipeline.create_document(conn, "坏文件.pdf", b"%PDF-broken", fail_inject=True)
    pipeline.enqueue(conn, bad["id"])
    r1 = pipeline.tick(conn, "w1", 30, now=T + 200)
    check("第 1 次失败仍回 pending（还有配额）", r1["status"] == "pending", str(r1["status"]))
    check("失败时 error 有内容", bool(r1["error"]), str(r1["error"]))
    check("error 是单行（换行会毁掉任务列表）", "\n" not in str(r1["error"]), str(r1["error"]))
    check("失败后文档不是 running（不会卡住）",
          pipeline.get_document(conn, bad["id"])["status"] == "pending",
          pipeline.get_document(conn, bad["id"])["status"])
    r2 = pipeline.tick(conn, "w2", 30, now=T + 201)
    check("第 2 次失败 attempts=2", r2["attempts"] == 2 and r2["status"] == "pending",
          str(r2["attempts"]))
    r3 = pipeline.tick(conn, "w3", 30, now=T + 202)
    check("第 3 次失败转终态 failed", r3["status"] == "failed" and r3["final"] is True,
          str(r3["status"]))
    r4 = pipeline.tick(conn, "w4", 30, now=T + 203)
    check("failed 后不再被领取（不会无限重试）", r4["claimed"] is False, str(r4))
    check("文档也落到 failed 且有 error",
          pipeline.get_document(conn, bad["id"])["status"] == "failed"
          and bool(pipeline.get_document(conn, bad["id"])["error"]))
    check("failed 的任务没有残留 chunk", pipeline.count_chunks(conn, bad["id"]) == 0)

    # ----------------------------------------------------------------- 6
    print("\n  —— 手动重试与并发入队 ——")
    fixed = pipeline.retry_document(conn, bad["id"], now=T + 400)
    check("retry 把 failed 重置成 pending", fixed["task"]["status"] == "pending",
          str(fixed["task"]["status"]))
    check("retry 把 attempts 清零（配额重新给满）", fixed["task"]["attempts"] == 0,
          str(fixed["task"]["attempts"]))
    check("retry 后文档回到 pending",
          pipeline.get_document(conn, bad["id"])["status"] == "pending")
    ok, msg = raises(lambda: pipeline.retry_document(conn, doc["id"], now=T + 401))
    check("已完成的文档不能 retry", ok, msg)
    ok, msg = raises(lambda: pipeline.retry_document(conn, "doc_nope", now=T + 402))
    check("不存在的文档 retry 报错", ok, msg)

    import threading

    race = pipeline.create_document(conn, "并发.txt", "并发入队测试。".encode() * 40)
    ids: list[int] = []
    lock = threading.Lock()

    def race_worker() -> None:
        with pipeline.connect(DB_PATH) as c:
            t = pipeline.enqueue(c, race["id"], now=T + 500)
            with lock:
                ids.append(t["id"])

    threads = [threading.Thread(target=race_worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    check("4 个线程同时入队只产生 1 条任务",
          conn.execute("SELECT COUNT(*) AS n FROM tasks WHERE document_id=?",
                       (race["id"],)).fetchone()["n"] == 1, str(ids))
    check("并发入队都拿到同一个 task id", len(set(ids)) == 1, str(ids))

    # ----------------------------------------------------------------- 7
    # 把残留的 pending 跑完，否则下面 benchmark 会领到别人的任务
    guard = 0
    while pipeline.tick(conn, "cleanup", 30, now=T + 550)["claimed"] and guard < 10:
        guard += 1

    print("\n  —— 内置样例实测 ——")
    table = []
    for fname in sorted(DEMO_DOCS):
        kind, content = DEMO_DOCS[fname]
        d = pipeline.create_document(conn, fname, content.encode("utf-8"))
        pipeline.enqueue(conn, d["id"])
        out = pipeline.tick(conn, "bench", 30, now=T + 600)
        table.append((fname, kind, out["chars"], out["chunks"], out["avg_chars"], out["ms"]))
        print(f"       {fname:12s} kind={kind:4s} {out['chars']:4d} 字 → {out['chunks']} 块 "
              f"/ 平均 {out['avg_chars']:5.1f} 字 / {out['ms']} ms")
    check("4 个内置样例全部切出块", all(t[3] > 0 for t in table), str([t[3] for t in table]))
    check("样例块数各不相同（切块真按内容走）", len({t[3] for t in table}) > 1,
          str([t[3] for t in table]))
    check("平均块长都在 [50, chunk_size] 内",
          all(50 <= t[4] <= pipeline.CHUNK_SIZE for t in table), str([t[4] for t in table]))

    # ----------------------------------------------------------------- 8
    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    check("GET /health 返回调度参数", r.status_code == 200 and r.json()["max_attempts"] == 3,
          str(r.status_code))
    check("/health 带上 chunk 参数（前端要显示）", r.json()["chunk"]["size"] == pipeline.CHUNK_SIZE)

    r = client.post("/docs", files={"file": ("手册.md", SAMPLE_MD, "text/markdown")})
    check("POST /docs 上传文件返回 201 pending",
          r.status_code == 201 and r.json()["document"]["status"] == "pending", str(r.status_code))
    http_doc = r.json()["document"]["id"]
    check("上传立刻就有任务（不用等 worker）", r.json()["task"]["status"] == "pending")

    r = client.post("/worker/tick", json={"worker": "http-w"})
    body = r.json()
    check("POST /worker/tick 跑完并返回 chunk 数",
          r.status_code == 200 and body["status"] == "done" and body["chunks"] > 1, str(body)[:120])
    check("tick 之后再 tick 不会重复处理",
          client.post("/worker/tick", json={}).json()["claimed"] is False)

    r = client.get(f"/docs/{http_doc}")
    check("GET /docs/{id} 返回 chunk 列表",
          r.status_code == 200 and r.json()["chunk_count"] > 1, str(r.status_code))
    check("GET /docs 列表里有这条文档",
          any(i["id"] == http_doc for i in client.get("/docs").json()["items"]))
    check("GET /docs/不存在 返回 404", client.get("/docs/doc_nope").status_code == 404)
    check("POST /docs/{id}/retry 对 done 返回 409",
          client.post(f"/docs/{http_doc}/retry").status_code == 409)

    r = client.post("/docs", data={"text": "粘贴的正文。第二句。", "name": "粘贴.txt"})
    check("POST /docs 支持纯文本表单", r.status_code == 201, str(r.status_code))
    r = client.post("/docs", data={"text": "   "})
    check("空内容返回 400", r.status_code == 400, str(r.status_code))

    r = client.post("/docs", files={"file": ("木马.exe", b"MZ\x90\x00", "application/octet-stream")})
    exe_doc = r.json()["document"]["id"]
    check("不支持的扩展名上传也先接（这才是坑）", r.status_code == 201, str(r.status_code))
    client.post("/worker/drain", json={})
    d = client.get(f"/docs/{exe_doc}").json()["document"]
    check("worker 跑完才暴露 failed（上传成功 ≠ 可检索）", d["status"] == "failed", str(d["status"]))
    check("失败原因可读（写了扩展名不支持）", "扩展名" in (d["error"] or ""), str(d["error"]))

    r = client.post("/docs", data={"text": "注入故障。", "fail_inject": "1"})
    inj = r.json()["document"]["id"]
    client.post("/worker/drain", json={})
    check("fail_inject 走到 failed 而不是永远 running",
          client.get(f"/docs/{inj}").json()["document"]["status"] == "failed")

    r = client.get("/tasks")
    check("GET /tasks 返回队列与统计",
          r.status_code == 200 and r.json()["count"] > 0
          and r.json()["stats"]["by_task_status"]["pending"] == 0, str(r.status_code))

    r = client.post("/demo/seed")
    check("POST /demo/seed 一次塞进 4 篇", r.status_code == 201 and r.json()["seeded"] == 4,
          str(r.status_code))
    r = client.post("/worker/drain", json={})
    check("drain 把种子全部跑完", r.json()["processed"] == 4, str(r.json()["processed"]))
    check("drain 之后没有 pending 任务", r.json()["stats"]["by_task_status"]["pending"] == 0)

    cm.__exit__(None, None, None)
    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
