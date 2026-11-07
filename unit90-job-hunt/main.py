"""Unit 90 · 求职投递：投递跟踪器 + 漏斗统计。

核心认知：求职是漏斗——投递 → 面试 → offer，每个环节都要记录和复盘。
  1. 记录：公司/岗位/渠道/日期/状态
  2. 统计：转化率（面试率、offer 率）
  3. 复盘：哪个渠道/哪类岗位转化好
"""

from __future__ import annotations

from dataclasses import dataclass, field

STATUS_FLOW = ["已投递", "已沟通", "面试中", "已 offer", "已拒绝"]


@dataclass
class JobTracker:
    jobs: list = field(default_factory=list)

    def add(self, company: str, role: str, channel: str) -> int:
        job = {"company": company, "role": role, "channel": channel, "status": "已投递"}
        self.jobs.append(job)
        return len(self.jobs) - 1

    def update(self, idx: int, status: str) -> None:
        if status not in STATUS_FLOW:
            raise ValueError(f"未知状态 {status}，合法：{STATUS_FLOW}")
        if idx >= len(self.jobs):
            raise IndexError("无此投递")
        self.jobs[idx]["status"] = status

    def funnel(self) -> dict:
        total = len(self.jobs)
        if not total:
            return {"total": 0}
        interviews = sum(1 for j in self.jobs if j["status"] in ("面试中", "已 offer", "已拒绝"))
        offers = sum(1 for j in self.jobs if j["status"] == "已 offer")
        return {
            "total": total,
            "interview_rate": round(interviews / total, 4),
            "offer_rate": round(offers / total, 4),
        }

    def by_channel(self) -> dict:
        out: dict[str, int] = {}
        for j in self.jobs:
            out[j["channel"]] = out.get(j["channel"], 0) + 1
        return out


def run_selfcheck() -> int:
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
    t.add("A公司", "AI应用", "BOSS直聘")
    t.add("B公司", "RAG工程师", "内推")
    t.add("C公司", "Agent开发", "BOSS直聘")
    t.add("D公司", "LLM后端", "官网")
    t.update(0, "面试中")
    t.update(1, "已 offer")
    f = t.funnel()
    check("总投递 4", f["total"] == 4)
    check("面试率 0.5", f["interview_rate"] == 0.5, str(f))
    check("offer 率 0.25", f["offer_rate"] == 0.25, str(f))
    ch = t.by_channel()
    check("渠道统计", ch["BOSS直聘"] == 2 and ch["内推"] == 1)
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

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(run_selfcheck())
