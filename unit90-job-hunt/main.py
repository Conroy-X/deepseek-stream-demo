"""Unit 90 · 求职投递：投递跟踪器 + 漏斗统计。

求职是漏斗：投递 → 沟通 → 面试 → offer，每一层都要记录和复盘。

三个能力：
  1. 记录：公司/岗位/渠道/日期/状态
  2. 漏斗：转化率（沟通率、面试率、offer 率）
  3. 复盘：按渠道看哪条路转化最好

启动：uv run uvicorn main:app --port 8090
自检：uv run python main.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

# 状态流转（只能前进不能后退，防止状态回退造成统计错乱）
STATUS_FLOW = ["已投递", "已沟通", "面试中", "已 offer", "已拒绝"]

# 漏斗分层：某个状态说明至少到达了哪一层
STATUS_LEVEL = {"已投递": 1, "已沟通": 2, "面试中": 3, "已 offer": 4, "已拒绝": 0}


@dataclass
class JobTracker:
    jobs: list = field(default_factory=list)

    def add(self, company: str, role: str, channel: str, applied_on: str | None = None) -> int:
        if not company.strip() or not role.strip():
            raise ValueError("公司和岗位不能为空")
        job = {
            "id": len(self.jobs),
            "company": company.strip(),
            "role": role.strip(),
            "channel": channel.strip(),
            "date": applied_on or date.today().isoformat(),
            "status": "已投递",
        }
        self.jobs.append(job)
        return job["id"]

    def update(self, idx: int, status: str) -> dict:
        if status not in STATUS_FLOW:
            raise ValueError(f"未知状态 {status}，合法值：{STATUS_FLOW}")
        if not 0 <= idx < len(self.jobs):
            raise IndexError(f"无此投递记录（id={idx}）")
        job = self.jobs[idx]
        # 状态只能前进（拒绝可以从任何状态进入）
        old_lv = STATUS_LEVEL[job["status"]]
        new_lv = STATUS_LEVEL[status]
        if status != "已拒绝" and new_lv < old_lv:
            raise ValueError(f"状态不能回退：{job['status']} → {status}")
        job["status"] = status
        return job

    def funnel(self) -> dict:
        total = len(self.jobs)
        if not total:
            return {"total": 0}
        # 到达某层：当前状态层级 >= 该层，或已拒绝但曾到过（这里用当前层级近似）
        def reached(level: int) -> int:
            return sum(1 for j in self.jobs if STATUS_LEVEL[j["status"]] >= level)

        comm = reached(2)
        inter = reached(3)
        offer = reached(4)
        return {
            "total": total,
            "communicated": comm,
            "interviewed": inter,
            "offered": offer,
            "communicate_rate": round(comm / total, 4),
            "interview_rate": round(inter / total, 4),
            "offer_rate": round(offer / total, 4),
            "rejected": sum(1 for j in self.jobs if j["status"] == "已拒绝"),
        }

    def by_channel(self) -> dict:
        out: dict[str, dict] = {}
        for j in self.jobs:
            c = j["channel"] or "(未填)"
            d = out.setdefault(c, {"total": 0, "interviewed": 0, "offered": 0})
            d["total"] += 1
            if STATUS_LEVEL[j["status"]] >= 3:
                d["interviewed"] += 1
            if STATUS_LEVEL[j["status"]] >= 4:
                d["offered"] += 1
        for c, d in out.items():
            d["interview_rate"] = round(d["interviewed"] / d["total"], 4)
        return out


tracker = JobTracker()


app = FastAPI(title="Unit 90 · 求职投递跟踪", version="0.1.0")


@app.post("/jobs", summary="记录一次投递")
async def add_job(company: str, role: str, channel: str = "未填", applied_on: str | None = None) -> dict:
    try:
        jid = tracker.add(company, role, channel, applied_on)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "id": jid, "job": tracker.jobs[jid]}


@app.patch("/jobs/{jid}", summary="更新状态（只能前进）")
async def update_job(jid: int, status: str) -> dict:
    try:
        job = tracker.update(jid, status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except IndexError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return {"ok": True, "job": job}


@app.get("/jobs", summary="列出所有投递")
async def list_jobs() -> dict:
    return {"count": len(tracker.jobs), "jobs": tracker.jobs}


@app.get("/funnel", summary="漏斗统计")
async def funnel() -> dict:
    return tracker.funnel()


@app.get("/channels", summary="按渠道复盘")
async def channels() -> dict:
    return tracker.by_channel()


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({
        "hint": "POST /jobs?company=X&role=Y&channel=BOSS → PATCH /jobs/0?status=面试中 → GET /funnel"
    })


# ---------------------------------------------------------------------------
# 独立自检
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

    print("Unit 90 · 求职投递 自检\n")

    t = JobTracker()
    check("空漏斗", t.funnel() == {"total": 0})

    i0 = t.add("A公司", "AI应用", "BOSS直聘")
    i1 = t.add("B公司", "RAG工程师", "内推")
    i2 = t.add("C公司", "Agent开发", "BOSS直聘")
    i3 = t.add("D公司", "LLM后端", "官网")
    check("记录 4 条", len(t.jobs) == 4)
    check("初始状态已投递", all(j["status"] == "已投递" for j in t.jobs))
    check("有投递日期", all(j["date"] for j in t.jobs))

    t.update(i0, "面试中")
    t.update(i1, "已 offer")
    t.update(i2, "已沟通")
    f = t.funnel()
    check("总数 4", f["total"] == 4)
    check("沟通 3（面试中/offer/已沟通）", f["communicated"] == 3, str(f))
    check("面试 2", f["interviewed"] == 2, str(f))
    check("offer 1", f["offered"] == 1, str(f))
    check("面试率 0.5", f["interview_rate"] == 0.5, str(f["interview_rate"]))
    check("offer 率 0.25", f["offer_rate"] == 0.25, str(f["offer_rate"]))

    # ---- 状态只能前进 ----
    try:
        t.update(i0, "已投递")  # 面试中 → 已投递，回退
        check("状态不能回退", False)
    except ValueError:
        check("状态不能回退", True)
    # 拒绝可以从任何状态进入
    t.update(i1, "已拒绝")
    check("拒绝可随时进入", t.jobs[i1]["status"] == "已拒绝")

    # ---- 非法输入 ----
    try:
        t.update(0, "随便")
        check("非法状态抛错", False)
    except ValueError:
        check("非法状态抛错", True)
    try:
        t.update(99, "已 offer")
        check("越界抛错", False)
    except IndexError:
        check("越界抛错", True)
    try:
        t.add("", "x", "y")
        check("空公司名抛错", False)
    except ValueError:
        check("空公司名抛错", True)

    # ---- 渠道复盘 ----
    ch = t.by_channel()
    check("BOSS直聘 2 条", ch["BOSS直聘"]["total"] == 2, str(ch))
    check("内推 1 条", ch["内推"]["total"] == 1)
    check("渠道有面试率", all("interview_rate" in d for d in ch.values()))

    # ---- HTTP ----
    c = TestClient(app)
    r = c.post("/jobs", params={"company": "E公司", "role": "AI工程", "channel": "内推"})
    check("HTTP 记录投递 200", r.status_code == 200)
    jid = r.json()["id"]
    check("HTTP 返回 id", isinstance(jid, int))
    check("HTTP 更新状态 200", c.patch(f"/jobs/{jid}", params={"status": "面试中"}).status_code == 200)
    check("HTTP 非法状态 400", c.patch(f"/jobs/{jid}", params={"status": "乱写"}).status_code == 400)
    check("HTTP 不存在 404", c.patch("/jobs/9999", params={"status": "面试中"}).status_code == 404)
    check("HTTP 空公司 400", c.post("/jobs", params={"company": "", "role": "x"}).status_code == 400)
    check("HTTP /jobs 列表", c.get("/jobs").json()["count"] >= 1)
    check("HTTP /funnel", "interview_rate" in c.get("/funnel").json())
    check("HTTP /channels", isinstance(c.get("/channels").json(), dict))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())