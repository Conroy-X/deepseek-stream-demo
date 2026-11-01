"""Unit 24 · Embedding 流水线 —— 批量、限流重试、断点续跑、成本台账。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8024
    打开 http://127.0.0.1:8024/

自检（离线，本地哈希向量，不需要 key）：
    uv run python main.py
"""

from __future__ import annotations

import os
import sys
import time

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import pipeline as pl

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 24 · Embedding 流水线", version="0.1.0")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


class SeedIn(BaseModel):
    n: int = Field(default=100, ge=1, le=1000)
    reset: bool = True


class RunIn(BaseModel):
    batch_size: int = Field(default=16, ge=1, le=512)
    max_retries: int = Field(default=2, ge=0, le=5)
    token_budget: int = Field(default=2000, ge=20, le=100_000)
    inject_failures: int = Field(default=0, ge=0, le=10, description="前 N 次调用注入 429")
    max_chunks: int = Field(default=0, ge=0, le=1000, description=">0 时只跑前 N 条，模拟崩溃")
    fail_on: list[str] = Field(default_factory=list, description="模拟这些 id 落库失败")


class ResumeIn(BaseModel):
    batch_size: int = Field(default=16, ge=1, le=512)
    max_retries: int = Field(default=2, ge=0, le=5)


def _open():
    conn = pl._conn()
    pl.init_db(conn)
    return conn


def _run_once(payload: RunIn) -> dict:
    conn = _open()
    try:
        provider = pl.HashingProvider(dim=pl.DEFAULT_DIM, inject_failures=payload.inject_failures)
        report = pl.run_pipeline(
            conn,
            provider,
            batch_size=payload.batch_size,
            max_retries=payload.max_retries,
            max_chunks=payload.max_chunks,
            token_budget=payload.token_budget,
            retry_delay=0.2,  # 线上退避，别把 429 打成死循环
            fail_on=set(payload.fail_on) or None,
        )
        return {"report": report, "status": pl.read_status(conn)}
    finally:
        conn.close()


@app.get("/health", summary="模型、维度、单价、库里有多少条")
def health() -> dict:
    conn = _open()
    try:
        st = pl.read_status(conn)
    finally:
        conn.close()
    return {
        "ok": True,
        "port": pl.PORT,
        "model": st["model"],
        "dim": st["dim"],
        "price_per_mtok": pl.PRICE_PER_MTOK,
        "db": os.path.relpath(pl.DB_PATH, BASE_DIR).replace("\\", "/"),
        "chunks": st["total"],
        "demo_clauses": len(pl.DEMO_CLAUSES),
        "provider": "local-hashing（离线、免费、确定性；换 API 只改 provider 一个类）",
    }


@app.post("/seed", summary="灌演示 chunk（默认 100 条，全部 pending）")
def seed(req: SeedIn) -> dict:
    if req.reset:
        pl.reset_db()
    conn = _open()
    try:
        n = pl.seed_chunks(conn, pl.demo_texts(req.n))
        st = pl.read_status(conn)
    finally:
        conn.close()
    return {"seeded": n, "status": st}


@app.post("/run", summary="跑一批：取待办 → 组批 → 重试 → 逐批落库")
def run(req: RunIn) -> dict:
    return _run_once(req)


@app.post("/resume", summary="断点续跑：只处理还没 done 的，不重算已完成的")
def resume(req: ResumeIn) -> dict:
    return _run_once(RunIn(batch_size=req.batch_size, max_retries=req.max_retries))


@app.get("/status", summary="完成/待处理/失败计数 + 累计 token 与成本")
def status_route() -> dict:
    conn = _open()
    try:
        st = pl.read_status(conn)
        recent = [
            dict(r)
            for r in conn.execute("SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 8")
        ]
    finally:
        conn.close()
    return {"status": st, "runs": recent}


@app.get("/vectors/{chunk_id}", summary="查一条向量：维度、范数、自比余弦")
def vector(chunk_id: str) -> dict:
    conn = _open()
    try:
        v = pl.look_vector(conn, chunk_id)
    finally:
        conn.close()
    if v is None:
        raise HTTPException(status_code=404, detail=f"没有 {chunk_id} 这条")
    return v


@app.post("/bench", summary="同一批 100 条，batch=1 与 batch=16 的调用次数对比")
def bench(n: int = 100, batch_size: int = 16) -> dict:
    """每次都重置再灌，两个 arm 的数据才可比。跑完库里停在最后一个 arm 的状态。"""
    arms = []
    for bs in (1, batch_size):
        pl.reset_db()
        conn = _open()
        try:
            pl.seed_chunks(conn, pl.demo_texts(n))
            provider = pl.HashingProvider(dim=pl.DEFAULT_DIM)
            t0 = time.perf_counter()
            rep = pl.run_pipeline(conn, provider, batch_size=bs, retry_delay=0.0)
            elapsed = (time.perf_counter() - t0) * 1000
            arms.append(
                {
                    "batch_size": bs,
                    "chunks": n,
                    "calls": rep["calls"],
                    "done": rep["done"],
                    "act_tokens": rep["act_tokens"],
                    "est_tokens": rep["est_tokens"],
                    "cost_usd": rep["cost_usd"],
                    "elapsed_ms": round(elapsed, 1),
                }
            )
        finally:
            conn.close()
    return {
        "chunks": n,
        "arms": arms,
        "note": "本地没有网络 RTT，但「一条一发」= 100 个事务。本机每次 commit 的 fsync ≈ 97ms，"
                "所以 batch=1 多出来的那 9 秒几乎全花在落库，不是算向量。线上还要再乘一次 100–300ms RTT。"
                "**调用次数和事务数，都是被限流和计费的单位。**",
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

    def raises(fn, exc=Exception) -> tuple[bool, str]:
        try:
            fn()
        except exc as e:
            return True, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"抛了 {type(e).__name__}"
        return False, "没抛异常"

    def fresh(n: int = 100):
        pl.reset_db()
        conn = pl._conn()
        pl.init_db(conn)
        pl.seed_chunks(conn, pl.demo_texts(n))
        return conn

    print("Unit 24 · Embedding 流水线自检（离线，本地哈希向量）\n")

    # -----------------------------------------------------------------------
    print("  —— 批量：调用次数是限流和计费的单位 ——")
    conn = fresh(100)
    p1 = pl.HashingProvider()
    r1 = pl.run_pipeline(conn, p1, batch_size=1, retry_delay=0.0)
    conn.close()

    conn = fresh(100)
    p16 = pl.HashingProvider()
    r16 = pl.run_pipeline(conn, p16, batch_size=16, retry_delay=0.0)
    print(f"       100 条：batch=1 → {r1['calls']} 次调用；batch=16 → {r16['calls']} 次调用")
    check("batch=1 时 100 条 = 100 次调用", r1["calls"] == 100, str(r1["calls"]))
    check("batch=16 时 100 条 = 7 次调用（6×16 + 4）", r16["calls"] == 7, str(r16["calls"]))
    check("批量把调用次数压到 1/14", r16["calls"] * 14 <= r1["calls"], f"{r16['calls']} vs {r1['calls']}")
    check("两种批量都写完 100 条", r1["done"] == 100 and r16["done"] == 100, f"{r1['done']}/{r16['done']}")
    check("送出去的文本条数一样（不多不少）", p1.sent == p16.sent == 100, f"{p1.sent}/{p16.sent}")
    check("token 消耗与批量无关（同样的钱）", p1.tokens == p16.tokens, f"{p1.tokens} vs {p16.tokens}")

    rows = pl.pending_rows(conn)
    check("没有待办时组批返回空列表", pl.plan_batches([]) == [], str(pl.plan_batches([])))
    ok, msg = raises(lambda: pl.plan_batches(rows, batch_size=0), ValueError)
    check("batch_size=0 直接报错（不能默默变慢）", ok, msg)

    small = [{"id": f"x{i}", "text": "年假需提前三个工作日申请（第 %d 条）" % i} for i in range(10)]
    tight = pl.plan_batches(small, batch_size=16, token_budget=60)
    check("token 预算把批次切碎（条数没到上限也得分批）", len(tight) > 1, str([len(b) for b in tight]))
    check(
        "每批估算 token 不超预算",
        all(sum(pl.estimate_tokens(r["text"]) for r in b) <= 60 for b in tight),
        str([sum(pl.estimate_tokens(r['text']) for r in b) for b in tight]),
    )
    huge = [{"id": "big", "text": "长" * 400}, {"id": "s1", "text": "短"}]
    hb = pl.plan_batches(huge, batch_size=16, token_budget=50)
    check("超预算的单条独占一批（不拆文本）", len(hb) == 2 and len(hb[0]) == 1, str([len(b) for b in hb]))
    conn.close()  # 用完立刻还回去：连接不 close，下一次 DROP TABLE 就可能抢不到写锁

    # -----------------------------------------------------------------------
    print("\n  —— 连接：连跑两次 reset 都不许被锁 ——")
    # 这一组专门复现"跑两次挂一次"：如果上一个连接没 close（或者上一轮的事务没收掉），
    # 下一次 reset_db 的 DROP TABLE 就会 sqlite3.OperationalError: database is locked。
    # 所以连着 reset + 灌 + 跑两轮，证明 reset_db 的「先 commit 再 DROP + 有界重试」真管用。
    ok, detail = True, ""
    for i in range(2):
        try:
            c = fresh(8)
            pl.run_pipeline(c, pl.HashingProvider(), batch_size=8, retry_delay=0.0)
            c.close()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"第 {i + 1} 轮 {type(e).__name__}: {e}"
            break
    check("连续两轮 reset + 灌 + 跑，不抛 database is locked", ok, detail)

    c = pl._conn()
    try:
        st_c = pl.read_status(c)
    finally:
        c.close()
    check("第二轮 reset 清掉了第一轮的台账（只剩本轮 1 次运行）", st_c["runs"] == 1, str(st_c["runs"]))

    # -----------------------------------------------------------------------
    print("\n  —— 限流重试：429 是常态，不是异常 ——")
    conn = fresh(32)
    pr = pl.HashingProvider(inject_failures=2)
    rr = pl.run_pipeline(conn, pr, batch_size=16, max_retries=2, retry_delay=0.01)
    b0, b1 = rr["batches"][0], rr["batches"][1]
    print(f"       第 1 批：{b0['attempts']} 次尝试后成功，退避 {b0['delays']}；第 2 批：{b1['attempts']} 次")
    check("前 2 次 429，第 3 次成功", b0["ok"] and b0["attempts"] == 3, str(b0))
    check("退避严格等比翻倍 [0.01, 0.02]", b0["delays"] == [0.01, 0.02], str(b0["delays"]))
    check("第二批没受牵连（一次就成）", b1["attempts"] == 1, str(b1["attempts"]))
    check("重试后 32 条全部落库", rr["done"] == 32 and rr["failed"] == 0, f"{rr['done']}/{rr['failed']}")
    att_db = conn.execute("SELECT attempts FROM chunks WHERE id='c000'").fetchone()["attempts"]
    check("attempts 写进 chunk 行（第一批 = 3）", att_db == 3, str(att_db))
    check(
        "失败的那几次不计 token（只有成功那次算钱）",
        rr["act_tokens"] == sum(pl.count_tokens(t) for t in pl.demo_texts(32)),
        str(rr["act_tokens"]),
    )

    again = pl.HashingProvider()
    rr2 = pl.run_pipeline(conn, again, batch_size=16, retry_delay=0.0)
    check("全部 done 之后再跑：0 次调用", rr2["calls"] == 0, str(rr2["calls"]))
    check("0 次调用时给出说明而不是报错", "没有待处理" in rr2["note"], rr2["note"])
    conn.close()

    conn = fresh(16)
    pf = pl.HashingProvider(inject_failures=9)
    rf = pl.run_pipeline(conn, pf, batch_size=16, max_retries=1, retry_delay=0.0)
    check("重试次数用尽 → 该批标 failed，不静默丢弃", rf["failed"] == 16 and rf["done"] == 0,
          f"{rf['done']}/{rf['failed']}")
    check("failed 的条不是 done（续跑还能捡回来）",
          conn.execute("SELECT COUNT(*) n FROM chunks WHERE status='failed'").fetchone()["n"] == 16)
    err0 = conn.execute("SELECT error FROM chunks LIMIT 1").fetchone()["error"] or ""
    check("错误信息里带着 429 原文", "429" in err0, err0)
    ok, msg = raises(
        lambda: pl.run_pipeline(conn, pl.HashingProvider(), batch_size=16, max_retries=-1), ValueError
    )
    check("max_retries 为负直接报错", ok, msg)
    conn.close()

    # -----------------------------------------------------------------------
    print("\n  —— 断点续跑：只处理没 done 的 ——")
    conn = fresh(100)
    p_a = pl.HashingProvider()
    ra = pl.run_pipeline(conn, p_a, batch_size=16, max_chunks=40, retry_delay=0.0)
    st_a = pl.read_status(conn)
    print(f"       跑到第 40 条中断：done={st_a['done']} pending={st_a['pending']} 调用 {ra['calls']} 次")
    check("只跑了前 40 条（模拟崩溃）", st_a["done"] == 40 and st_a["pending"] == 60,
          f"{st_a['done']}/{st_a['pending']}")
    check("40 条 = 3 次调用（16+16+8）", ra["calls"] == 3, str(ra["calls"]))

    p_b = pl.HashingProvider()
    rb = pl.run_pipeline(conn, p_b, batch_size=16, retry_delay=0.0)
    print(f"       续跑：投喂 {rb['texts']} 条，{rb['calls']} 次调用，本轮完成 {rb['done']} 条")
    check("第二次只投喂剩下的 60 条", rb["texts"] == 60, str(rb["texts"]))
    check("第二次调用数 = ceil(60/16) = 4，不是 7", rb["calls"] == 4, str(rb["calls"]))
    check("第二次补完 60 条", rb["done"] == 60, str(rb["done"]))
    check("续跑不重算已完成（3+4=7 次 = 全量一次跑完）",
          ra["calls"] + rb["calls"] == 7, f"{ra['calls']}+{rb['calls']}")

    p_c = pl.HashingProvider()
    rc = pl.run_pipeline(conn, p_c, batch_size=16, retry_delay=0.0)
    check("第三次：0 次调用、0 条文本", rc["calls"] == 0 and rc["texts"] == 0, f"{rc['calls']}/{rc['texts']}")
    check("第三次不产生新成本", rc["cost_usd"] == 0.0, str(rc["cost_usd"]))
    check("续跑锚点是 status 而不是 offset", len(pl.pending_rows(conn)) == 0, str(len(pl.pending_rows(conn))))

    # -----------------------------------------------------------------------
    print("\n  —— 向量与版本 ——")
    v = pl.look_vector(conn, "c000")
    check("维度 = 512 且与向量长度一致", v["dim"] == 512 and v["vec_len"] == 512, f"{v['dim']}/{v['vec_len']}")
    check("归一化后范数 ≈ 1", abs(v["norm"] - 1.0) < 1e-6, str(v["norm"]))
    check("余弦自比 = 1.0", abs(v["cosine_self"] - 1.0) < 1e-6, str(v["cosine_self"]))
    check("model 落库（换模型必须能看出来）", v["model"] == pl.DEFAULT_MODEL, v["model"])
    ndim = conn.execute("SELECT COUNT(DISTINCT dim) n FROM chunks WHERE status='done'").fetchone()["n"]
    check("所有向量维度一致", ndim == 1, str(ndim))
    mp = pl.HashingProvider()
    va, _ = mp.embed(["年假需提前三个工作日申请"])
    vb, _ = mp.embed(["年假需提前三个工作日申请"])
    check("同一文本两次嵌入完全一致（否则续跑会写出脏向量）", va == vb)
    check("空文本返回全零向量、不崩", pl.HashingProvider().embed([""])[0][0] == [0.0] * 512)

    # -----------------------------------------------------------------------
    print("\n  —— 成本台账：估算 vs 实际 ——")
    st = pl.read_status(conn)
    dev = abs(st["est_tokens"] - st["act_tokens"]) / st["act_tokens"]
    print(f"       100 条：估算 {st['est_tokens']} token · 实际 {st['act_tokens']} token · "
          f"偏差 {dev * 100:.1f}% · 累计成本 ${st['cost_usd']:.6f}")
    check("估算 token > 0", st["est_tokens"] > 0, str(st["est_tokens"]))
    check("估算与实际偏差 < 20%（否则预算不该被信任）", dev < 0.20, f"{dev:.4f}")
    check("成本 = token / 1M × 单价",
          abs(st["cost_usd"] - pl.cost_of(st["act_tokens"])) < 1e-9,
          f"{st['cost_usd']} vs {pl.cost_of(st['act_tokens'])}")
    full_once = pl.HashingProvider().embed(pl.demo_texts(100))[1]
    check("重跑不重复计费：累计 token 只等于一次全量", st["act_tokens"] == full_once,
          f"{st['act_tokens']} vs {full_once}")
    check("台账累计 3 次运行（中断 + 续跑 + 空跑）", st["runs"] == 3, str(st["runs"]))
    conn.close()

    # -----------------------------------------------------------------------
    print("\n  —— 写库失败：算完了但没落下去，不能算 done ——")
    conn = fresh(32)
    ps = pl.HashingProvider()
    pl.run_pipeline(conn, ps, batch_size=16, retry_delay=0.0, fail_on={"c003"})
    st_f = pl.read_status(conn)
    print(f"       注入 c003 落库失败：done={st_f['done']} failed={st_f['failed']}")
    check("写库失败的那批标 failed（不静默丢）", st_f["failed"] == 16, str(st_f["failed"]))
    st3 = conn.execute("SELECT status FROM chunks WHERE id='c003'").fetchone()["status"]
    check("失败那条不是 done", st3 == "failed", st3)
    st0 = conn.execute("SELECT status FROM chunks WHERE id='c000'").fetchone()["status"]
    check("同批其他条也没被标 done（事务回滚，原子性）", st0 == "failed", st0)
    nvec = conn.execute("SELECT COUNT(*) n FROM chunks WHERE vector IS NOT NULL").fetchone()["n"]
    check("没留下写了一半的向量", nvec == 16, str(nvec))
    ndone = conn.execute("SELECT COUNT(*) n FROM chunks WHERE status='done'").fetchone()["n"]
    check("另一半批次正常完成", ndone == 16, str(ndone))

    pr2 = pl.HashingProvider()
    rr3 = pl.run_pipeline(conn, pr2, batch_size=16, retry_delay=0.0)
    check("修好之后重跑补齐剩下的 16 条", rr3["done"] == 16 and rr3["calls"] == 1,
          f"{rr3['done']}/{rr3['calls']}")
    check("补齐后库里 32 条全 done",
          conn.execute("SELECT COUNT(*) n FROM chunks WHERE status='done'").fetchone()["n"] == 32)
    conn.close()

    # -----------------------------------------------------------------------
    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    check("GET /health 返回模型与维度", r.status_code == 200 and r.json()["dim"] == 512, str(r.status_code))
    check("/health 报出端口 8024", r.json()["port"] == 8024, str(r.json().get("port")))

    r = client.post("/seed", json={"n": 24})
    check("POST /seed 灌 24 条全 pending", r.status_code == 200 and r.json()["status"]["pending"] == 24)

    r = client.post("/run", json={"batch_size": 16})
    check("POST /run 跑完 24 条", r.status_code == 200 and r.json()["report"]["done"] == 24,
          str(r.json().get("report", {}).get("done")))
    check("/run 返回 2 个批次", len(r.json()["report"]["batches"]) == 2)

    r = client.post("/resume", json={"batch_size": 16})
    check("POST /resume 全部 done 时 0 次调用", r.status_code == 200 and r.json()["report"]["calls"] == 0)

    r = client.get("/status")
    check("GET /status 给出成本与进度", r.status_code == 200 and r.json()["status"]["cost_usd"] > 0)

    r = client.get("/vectors/c000")
    check("GET /vectors/{id} 返回范数≈1", r.status_code == 200 and abs(r.json()["norm"] - 1) < 1e-6)
    r = client.get("/vectors/nope")
    check("不存在的 chunk 返回 404", r.status_code == 404, str(r.status_code))

    r = client.post("/bench", params={"n": 24})
    check("POST /bench 两个 arm 都跑完", r.status_code == 200 and len(r.json()["arms"]) == 2)
    a1, a16 = r.json()["arms"]
    check("bench：batch=1 → 24 次 / batch=16 → 2 次",
          a1["calls"] == 24 and a16["calls"] == 2, f"{a1['calls']}/{a16['calls']}")
    check("bench：两次跑完的 token 完全相同", a1["act_tokens"] == a16["act_tokens"])

    r = client.post("/run", json={"batch_size": 0})
    check("非法 batch_size 返回 422", r.status_code == 422, str(r.status_code))

    r = client.get("/")
    check("GET / 返回页面", r.status_code == 200 and "Unit 24" in r.text, str(r.status_code))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
