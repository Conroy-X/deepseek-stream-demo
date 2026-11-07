"""Unit 81 · Research Agent 优化：引用准确性、去重、报告模板。

计划主题：把 Unit 45 的 Research Agent 打磨到「可交付报告」的程度，三个要点：

  1. 引用准确性：报告里每个结论都要带 [n]，且 [n] 必须指向真实来源
  2. 去重：同一来源被多个工具/多轮检索重复命中时，只保留一份
  3. 报告模板：固定结构（结论 / 证据 / 来源 / 局限），不自由发挥

启动：uv run uvicorn main:app --port 8081
自检：uv run python main.py
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import FastAPI
from fastapi.responses import JSONResponse

# 模拟检索到的原始结果（可能重复、可能来自多个工具）
RAW_SEARCH = [
    {"source": "handbook.md", "page": 1, "text": "年假满一年 5 天，满三年 10 天。"},
    {"source": "handbook.md", "page": 1, "text": "年假满一年 5 天，满三年 10 天。"},  # 重复
    {"source": "policy.md", "page": 1, "text": "退款 3-5 个工作日原路退回。"},
    {"source": "ops.md", "page": 2, "text": "住宿标准一线城市 600 元/晚。"},
    {"source": "policy.md", "page": 1, "text": "退款 3-5 个工作日原路退回。"},  # 重复
]


def dedupe(results: list[dict]) -> list[dict]:
    """按 (source, page, text) 去重，保留首次出现顺序。"""
    seen = set()
    out = []
    for r in results:
        key = (r["source"], r["page"], r["text"])
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


@dataclass
class Report:
    question: str
    conclusions: list[dict] = field(default_factory=list)  # {"text":..., "cite": n}
    sources: list[dict] = field(default_factory=list)

    def to_markdown(self) -> str:
        if not self.conclusions:
            return (
                f"# 研究报告：{self.question}\n\n"
                "## 结论\n\n资料不足，无法形成结论。\n\n"
                "## 来源\n\n（无）\n\n## 局限\n\n知识库中没有与该问题相关的内容。\n"
            )
        lines = [f"# 研究报告：{self.question}", "", "## 结论", ""]
        for c in self.conclusions:
            lines.append(f"- {c['text']} [{c['cite']}]")
        lines += ["", "## 来源", ""]
        for i, s in enumerate(self.sources, 1):
            lines.append(f"[{i}] {s['source']} 第 {s['page']} 页：{s['text']}")
        lines += ["", "## 局限", "", "本报告仅基于知识库检索结果，未覆盖外部信息。"]
        return "\n".join(lines)


def research(question: str) -> Report:
    """模拟一次 research：检索 → 去重 → 提炼结论（每条带引用）→ 套模板。"""
    results = dedupe(RAW_SEARCH)
    # 与问题相关的结论（教学版规则）
    keywords = {
        "年假": "年假天数按工龄递增",
        "退款": "退款 3-5 个工作日原路退回",
        "住宿": "一线城市住宿标准 600 元/晚",
    }
    conclusions = []
    for idx, r in enumerate(results, 1):
        for kw, claim in keywords.items():
            if kw in question and kw in r["text"]:
                conclusions.append({"text": claim, "cite": idx})
    return Report(question=question, conclusions=conclusions, sources=results)


def check_citations(report: Report) -> tuple[bool, list[str]]:
    """校验引用准确性：每条结论的 [n] 必须能对上来源。"""
    problems = []
    for c in report.conclusions:
        n = c["cite"]
        if not 1 <= n <= len(report.sources):
            problems.append(f"引用 [{n}] 超出来源范围")
    return (len(problems) == 0), problems


app = FastAPI(title="Unit 81 · Research Agent 优化", version="0.1.0")


@app.get("/research", summary="生成研究报告（去重 + 引用 + 模板）")
async def research_endpoint(q: str) -> dict:
    rep = research(q)
    ok, problems = check_citations(rep)
    return {
        "ok": ok,
        "markdown": rep.to_markdown(),
        "conclusions": len(rep.conclusions),
        "sources": len(rep.sources),
        "citation_ok": ok,
        "problems": problems,
    }


@app.get("/", include_in_schema=False)
async def index() -> dict:
    return {"hint": "/research?q=年假 或 /research?q=退款"}


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

    print("Unit 81 · Research Agent 优化 自检\n")

    # ---- 去重 ----
    deduped = dedupe(RAW_SEARCH)
    check("去重后 3 条（原 5 条）", len(deduped) == 3, str(len(deduped)))
    check("去重保留顺序", deduped[0]["source"] == "handbook.md")
    check("去重后无重复", len(deduped) == len(set((r["source"], r["page"], r["text"]) for r in deduped)))

    # ---- 引用准确性 ----
    rep = research("年假怎么算")
    check("相关结论有引用", len(rep.conclusions) > 0)
    ok, problems = check_citations(rep)
    check("引用全部有效", ok, str(problems))
    md = rep.to_markdown()
    check("报告有结论段", "## 结论" in md)
    check("报告有来源段", "## 来源" in md)
    check("报告有局限段", "## 局限" in md)
    check("结论行带 [n]", any("[" in ln and "]" in ln for ln in md.splitlines() if ln.startswith("- ")))

    # ---- 无关问题：不硬编 ----
    rep2 = research("今天天气")
    check("无关问题结论为空", len(rep2.conclusions) == 0)
    check("无关问题报告说明资料不足", "资料不足" in rep2.to_markdown())

    # ---- HTTP ----
    c = TestClient(app)
    r = c.get("/research", params={"q": "退款"})
    check("HTTP /research 200", r.status_code == 200)
    body = r.json()
    check("HTTP 返回 markdown", "markdown" in body)
    check("HTTP citation_ok", body["citation_ok"] is True)
    check("HTTP 来源已去重", body["sources"] == 3, str(body["sources"]))
    check("HTTP 结论数正确", body["conclusions"] >= 1)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
