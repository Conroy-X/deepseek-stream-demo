"""Unit 44 · 多来源交叉比对 —— FastAPI 实验台 + 自检。

启动：
    /home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --reload --port 8044

自检（离线）：
    /home/rik/.cache/aj/runner/bin/python main.py
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from analyzer import DEFAULT_SOURCES, TOPIC_KEYWORDS, cross_compare

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 44 · 多来源交叉比对", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class SourceIn(BaseModel):
    name: str = Field(min_length=1)
    text: str = Field(min_length=1)


class CompareIn(BaseModel):
    sources: list[SourceIn] = Field(min_length=2, max_length=10)


@app.get("/health", summary="后端存活 + 默认 fixture 概览")
def health() -> dict:
    return {
        "ok": True,
        "unit": "unit44-analysis",
        "default_sources_count": len(DEFAULT_SOURCES),
        "topics_known": sorted(TOPIC_KEYWORDS.keys()),
    }


@app.get("/sources", summary="默认 fixture 的 3 个来源")
def default_sources() -> dict:
    return {"count": len(DEFAULT_SOURCES), "items": DEFAULT_SOURCES}


@app.get("/topics", summary="已识别的主题关键词")
def topics() -> dict:
    return {"topics": TOPIC_KEYWORDS}


@app.post("/compare", summary="对自定义来源做交叉比对")
def do_compare(req: CompareIn) -> dict:
    srcs = [{"name": s.name, "text": s.text} for s in req.sources]
    return cross_compare(srcs)


@app.post("/compare/default", summary="对内置 3 来源做交叉比对（页面默认演示）")
def do_compare_default() -> dict:
    return cross_compare(DEFAULT_SOURCES)


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
            print(f"  [FAIL] {name}  {detail}")

    print("Unit 44 · 多来源交叉比对 自检（离线）\n")

    print("  —— 默认 fixture：3 个来源 ——")
    check("默认 fixture 3 个来源", len(DEFAULT_SOURCES) == 3, str(len(DEFAULT_SOURCES)))
    names = [s["name"] for s in DEFAULT_SOURCES]
    check("3 个来源名分别是 A/B/C", all(x.startswith(("A", "B", "C")) for x in names), str(names))
    check("每个来源有 name 和 text", all(s.get("name") and s.get("text") for s in DEFAULT_SOURCES))
    check("A 提到 reranker / 0.62 / 0.81",
          "reranker" in DEFAULT_SOURCES[0]["text"] and "0.62" in DEFAULT_SOURCES[0]["text"] and "0.81" in DEFAULT_SOURCES[0]["text"])
    check("B 也提到 reranker 但数值不同（0.55 / 0.79）",
          "reranker" in DEFAULT_SOURCES[1]["text"] and "0.55" in DEFAULT_SOURCES[1]["text"] and "0.79" in DEFAULT_SOURCES[1]["text"])
    check("C 提到延迟 260ms（补充点）",
          "reranker" in DEFAULT_SOURCES[2]["text"] and "260ms" in DEFAULT_SOURCES[2]["text"])

    print("\n  —— cross_compare 输出结构 ——")
    r = cross_compare(DEFAULT_SOURCES)
    check("返回 topic 字段", "topic" in r and r["topic"] == "reranker", r.get("topic"))
    check("返回 sources_count=3", r["sources_count"] == 3, str(r["sources_count"]))
    check("返回 consensus 数组", isinstance(r["consensus"], list))
    check("返回 conflicts 数组", isinstance(r["conflicts"], list))
    check("返回 unique_points 数组", isinstance(r["unique_points"], list))
    check("返回 sources_summary 数组", isinstance(r["sources_summary"], list) and len(r["sources_summary"]) == 3)

    print("\n  —— 共识：所有来源都在讨论 reranker ——")
    check("consensus 至少 1 条", len(r["consensus"]) >= 1, str(len(r["consensus"])))
    first_consensus = r["consensus"][0] if r["consensus"] else {}
    check("共识有 claim 字段", "claim" in first_consensus, str(first_consensus))
    check("共识有 sources 字段（列表）", isinstance(first_consensus.get("sources"), list))
    check("共识列出 >=2 个来源", len(first_consensus.get("sources") or []) >= 2,
          str(first_consensus.get("sources")))
    check("共识 claim 里出现 reranker", "reranker" in first_consensus.get("claim", "").lower(),
          first_consensus.get("claim"))
    check("共识 sources 覆盖全部 3 个来源",
          len(first_consensus["sources"]) == 3, str(first_consensus["sources"]))

    print("\n  —— 冲突：A 和 B 的命中率数值不同 ——")
    check("conflicts 至少 2 条（起始值 + 最终值）", len(r["conflicts"]) >= 2, str(len(r["conflicts"])))
    # 找命中率起始值冲突
    start_conflict = next((c for c in r["conflicts"] if "起始" in c["claim"]), None)
    check("找到命中率起始值冲突", start_conflict is not None, str(r["conflicts"]))
    if start_conflict:
        check("起始值冲突有 dimension=命中率", start_conflict.get("dimension") == "命中率",
              str(start_conflict.get("dimension")))
        check("起始值冲突有 2 个不同数值（0.55 / 0.62）",
              len(start_conflict["values"]) == 2 and {v["value"] for v in start_conflict["values"]} == {0.55, 0.62},
              str(start_conflict["values"]))
        check("起始值 0.62 来自 A",
              next(v for v in start_conflict["values"] if v["value"] == 0.62)["sources"] == ["A · RAG 实测"])
        check("起始值 0.55 来自 B",
              next(v for v in start_conflict["values"] if v["value"] == 0.55)["sources"] == ["B · 另一项实测"])
    # 找命中率最终值冲突
    end_conflict = next((c for c in r["conflicts"] if "最终" in c["claim"]), None)
    check("找到命中率最终值冲突", end_conflict is not None, str(r["conflicts"]))
    if end_conflict:
        check("最终值冲突有 2 个不同数值（0.79 / 0.81）",
              len(end_conflict["values"]) == 2 and {v["value"] for v in end_conflict["values"]} == {0.79, 0.81},
              str(end_conflict["values"]))
        check("最终值 0.81 来自 A",
              next(v for v in end_conflict["values"] if v["value"] == 0.81)["sources"] == ["A · RAG 实测"])
        check("最终值 0.79 来自 B",
              next(v for v in end_conflict["values"] if v["value"] == 0.79)["sources"] == ["B · 另一项实测"])

    # 冲突里不应该出现 ms 单位（延迟是补充点，不是冲突）
    has_ms_conflict = any("ms" in c.get("dimension", "") or "延迟" in c.get("dimension", "") for c in r["conflicts"])
    check("冲突里没有 ms 维度（延迟是补充不是冲突）", not has_ms_conflict, str(r["conflicts"]))

    print("\n  —— 补充点：C 独有，不重复 ——")
    check("unique_points 至少 1 条", len(r["unique_points"]) >= 1, str(len(r["unique_points"])))
    # 每个 unique_point 只属于 1 个来源
    check("每个补充点只属于 1 个来源",
          all(len(u.get("source", "")) > 0 for u in r["unique_points"]),
          str(r["unique_points"]))
    # 补充点里应该出现 C 的 260ms
    c260 = next((u for u in r["unique_points"] if u.get("value") == 260.0 and u.get("unit") == "ms"), None)
    check("C 的 260ms 在补充点里", c260 is not None and c260["source"].startswith("C"), str(c260))
    # 补充点不重复（每个 value+unit 只出现一次）
    seen_keys = set()
    no_dup = True
    for u in r["unique_points"]:
        k = (u["value"], u["unit"], u["source"])
        if k in seen_keys:
            no_dup = False
            break
        seen_keys.add(k)
    check("补充点不重复", no_dup)
    # 补充点里不应该出现冲突的数值（0.55 / 0.62 / 0.79 / 0.81）
    conflict_vals = {0.55, 0.62, 0.79, 0.81}
    has_overlap = any(u["value"] in conflict_vals for u in r["unique_points"])
    check("补充点不与冲突数值重复", not has_overlap, str([u for u in r["unique_points"] if u["value"] in conflict_vals]))

    print("\n  —— sources_summary 字段齐全 ——")
    for s in r["sources_summary"]:
        check(f"{s['name']} summary 有 numbers", isinstance(s.get("numbers"), list))
        check(f"{s['name']} summary 有 delta_pairs", isinstance(s.get("delta_pairs"), list))
        check(f"{s['name']} summary 有 topic", "topic" in s)
    # A 应该有 2 个 delta_pairs（0.62→0.81 和 90ms→180ms）
    a_sum = next(s for s in r["sources_summary"] if s["name"].startswith("A"))
    check("A 有 2 个 delta_pairs", len(a_sum["delta_pairs"]) == 2, str(a_sum["delta_pairs"]))
    # B 有 1 个 delta_pair（0.55→0.79）
    b_sum = next(s for s in r["sources_summary"] if s["name"].startswith("B"))
    check("B 有 1 个 delta_pair", len(b_sum["delta_pairs"]) == 1, str(b_sum["delta_pairs"]))
    # C 没有 delta_pair
    c_sum = next(s for s in r["sources_summary"] if s["name"].startswith("C"))
    check("C 没有 delta_pair", len(c_sum["delta_pairs"]) == 0, str(c_sum["delta_pairs"]))

    print("\n  —— 边界场景 ——")
    empty = cross_compare([])
    check("空 sources 返回空结构",
          empty["sources_count"] == 0 and empty["conflicts"] == [] and empty["consensus"] == [],
          str(empty))

    one = cross_compare([{"name": "A", "text": "reranker 把命中率从 0.5 提升到 0.8。"}])
    check("只有 1 个来源：无冲突", one["conflicts"] == [], str(one["conflicts"]))
    check("只有 1 个来源：无共识", one["consensus"] == [], str(one["consensus"]))

    # 两个主题不同的来源 → topic=mixed
    mixed = cross_compare([
        {"name": "X", "text": "reranker 提升了命中率。"},
        {"name": "Y", "text": "LangGraph 的 StateGraph 用 TypedDict。"},
    ])
    check("两个主题不同的来源 topic=mixed", mixed["topic"] == "mixed", mixed["topic"])
    check("主题不同时无共识", mixed["consensus"] == [], str(mixed["consensus"]))

    # 完全相同的两个来源 → 无冲突
    same = cross_compare([
        {"name": "X", "text": "reranker 把命中率从 0.5 提升到 0.8。"},
        {"name": "Y", "text": "reranker 把命中率从 0.5 提升到 0.8。"},
    ])
    check("两个完全相同的来源：无冲突", same["conflicts"] == [], str(same["conflicts"]))

    print("\n  —— 数值提取 ——")
    from analyzer import _extract_numbers, _extract_delta_pairs
    nums = _extract_numbers("命中率为 0.81，延迟 180ms，共 200 条文档。")
    vals = [n["value"] for n in nums]
    check("数值提取：能拿到 0.81 / 180 / 200", 0.81 in vals and 180.0 in vals and 200.0 in vals, str(vals))
    pairs = _extract_delta_pairs("从 0.62 拉到 0.81。")
    check("delta_pair 抽出 from=0.62 to=0.81",
          pairs and pairs[0]["from"] == 0.62 and pairs[0]["to"] == 0.81, str(pairs))
    pairs2 = _extract_delta_pairs("从 90ms 涨到 180ms。")
    check("delta_pair 抽出 ms 单位",
          pairs2 and pairs2[0]["from"] == 90.0 and pairs2[0]["to"] == 180.0
          and pairs2[0]["from_unit"] == "ms" and pairs2[0]["to_unit"] == "ms", str(pairs2))
    pairs3 = _extract_delta_pairs("今天天气不错。")
    check("无 delta 句子返回 []", pairs3 == [])

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    h = client.get("/health").json()
    check("GET /health", h["ok"] is True and h["default_sources_count"] == 3)

    sd = client.get("/sources").json()
    check("GET /sources 返回 3 条", sd["count"] == 3)

    tp = client.get("/topics").json()
    check("GET /topics 返回主题字典", "reranker" in tp["topics"])

    body = client.post("/compare/default").json()
    check("POST /compare/default 命中冲突", len(body["conflicts"]) >= 2, str(len(body["conflicts"])))
    check("POST /compare/default 有共识", len(body["consensus"]) >= 1)

    body = client.post("/compare", json={"sources": [
        {"name": "A", "text": "reranker 把命中率从 0.5 提升到 0.8。"},
        {"name": "B", "text": "reranker 把命中率从 0.6 提升到 0.85。"},
    ]}).json()
    check("POST /compare 自定义来源检测到冲突", len(body["conflicts"]) >= 2, str(body["conflicts"]))

    # 边界：sources 少于 2 个被 pydantic 拒绝
    err = client.post("/compare", json={"sources": [{"name": "A", "text": "x"}]})
    check("POST /compare 只 1 个来源被 pydantic 拒绝（422）", err.status_code == 422, str(err.status_code))

    # name 或 text 空
    err = client.post("/compare", json={"sources": [{"name": "", "text": "x"}, {"name": "B", "text": "y"}]})
    check("POST /compare 空 name 被 pydantic 拒绝（422）", err.status_code == 422)

    ridx = client.get("/")
    check("GET / 返回 200", ridx.status_code == 200)
    check("GET / 返回 HTML", "<html" in ridx.text.lower())

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
