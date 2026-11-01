"""Unit 43 · 结构化要点提取 —— FastAPI 实验台 + 自检。

启动：
    /home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --reload --port 8043

自检（离线）：
    /home/rik/.cache/aj/runner/bin/python main.py
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from extract import (
    DEFAULT_MAX_POINTS,
    DEFAULT_MIN_POINTS,
    OfflineModel,
    SCHEMA,
    extract_key_points,
    split_sentences,
    verify_sourcing,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 43 · 结构化要点提取", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class ExtractIn(BaseModel):
    text: str = Field(min_length=1)
    min_points: int = Field(default=DEFAULT_MIN_POINTS, ge=1, le=10)
    max_points: int = Field(default=DEFAULT_MAX_POINTS, ge=1, le=10)


@app.get("/health", summary="后端存活 + 模型与 schema 概览")
def health() -> dict:
    return {
        "ok": True,
        "unit": "unit43-extract",
        "model": OfflineModel.name,
        "min_points": DEFAULT_MIN_POINTS,
        "max_points": DEFAULT_MAX_POINTS,
    }


@app.get("/schema", summary="要点形状的 JSON schema")
def schema() -> dict:
    return SCHEMA


@app.get("/model", summary="OfflineModel 信息")
def model_info() -> dict:
    return {"name": OfflineModel.name, "kind": "rule-based", "schema": SCHEMA}


@app.post("/extract", summary="贴正文 → 提取要点")
def do_extract(req: ExtractIn) -> dict:
    points = extract_key_points(req.text, SCHEMA, req.min_points, req.max_points)
    sourcing = verify_sourcing(req.text, points)
    return {
        "count": len(points),
        "points": points,
        "sourcing": sourcing,
        "all_sourced": all(s["found"] for s in sourcing),
    }


@app.post("/extract/model", summary="走 OfflineModel 路径（模拟真模型调用）")
def do_extract_via_model(req: ExtractIn) -> dict:
    m = OfflineModel()
    msg = m.complete([{"role": "user", "content": req.text}])
    points = msg.get("points") or []
    sourcing = verify_sourcing(req.text, points)
    return {
        "count": len(points),
        "points": points,
        "sourcing": sourcing,
        "all_sourced": all(s["found"] for s in sourcing),
        "model_calls": m.calls,
        "raw_content": msg["content"],
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
            print(f"  [FAIL] {name}  {detail}")

    print("Unit 43 · 结构化要点提取 自检（离线）\n")

    # 准备一段带定义 / 数值 / 因果三种句子的正文
    SAMPLE = (
        "MCP（Model Context Protocol）是一种用 JSON-RPC 2.0 在客户端和工具服务器之间建通道的协议。"
        "它规定了一次握手：先 initialize 再 notifications/initialized。"
        "在 200 条文档的语料上测了 chunk 256/512/1024 三档，reranker 把 top-3 命中率从 0.62 拉到 0.81。"
        "因为 reranker 把噪声压住了，所以答案的准确率提高了。"
        "能力表声明 listChanged: true 等于承诺在列表变化时发通知。"
        "工具失败要包成 result.isError=true，因为 error 帧会让客户端按协议异常处理。"
        "这是一段没有数字也没有因果的句子，不应该被提取。"
    )

    print("  —— 切句 ——")
    sents = split_sentences(SAMPLE)
    check("切出 ≥ 5 句", len(sents) >= 5, str(len(sents)))
    check("每句长度 >= 12", all(len(s) >= 12 for s in sents))
    check("切句保留「MCP（Model Context Protocol）是一种...」", any("MCP" in s for s in sents))

    sents_empty = split_sentences("")
    check("空文本切句返回 []", sents_empty == [])
    sents_short = split_sentences("嗯。啊。哦。")
    check("全是短句返回 []", sents_short == [])

    print("\n  —— 提取要点：3-5 个，每个可溯源 ——")
    pts = extract_key_points(SAMPLE, SCHEMA)
    check("提取数量在 3-5", 3 <= len(pts) <= 5, str(len(pts)))
    check("每个要点有 point 字段", all("point" in p for p in pts))
    check("每个要点有 source_sentence 字段", all("source_sentence" in p for p in pts))
    check("要点字段只有 point / source_sentence（不漏字段）",
          all(set(p.keys()) == {"point", "source_sentence"} for p in pts),
          str(pts[0].keys()) if pts else "")

    print("\n  —— 溯源：source_sentence 必须在原文里逐字出现 ——")
    for p in pts:
        ok = p["source_sentence"] in SAMPLE
        check(f"「{p['point'][:30]}...」的源句在原文里", ok,
              f"源句前 40 字：{p['source_sentence'][:40]}")
    sv = verify_sourcing(SAMPLE, pts)
    check("verify_sourcing 全部 found=True", all(s["found"] for s in sv), str(sv))
    check("verify_sourcing 带 source_pos（>=0）", all(s["source_pos"] >= 0 for s in sv))
    check("verify_sourcing 字段齐全", all(set(s.keys()) == {"point","source_sentence","found","source_pos"} for s in sv))

    print("\n  —— 三类句子都被覆盖 ——")
    has_definition = any("是" in p["source_sentence"] or "称为" in p["source_sentence"] or "是指" in p["source_sentence"] for p in pts)
    check("要点里有定义句", has_definition)
    has_number = any("0.62" in p["source_sentence"] or "0.81" in p["source_sentence"] or "256" in p["source_sentence"] for p in pts)
    check("要点里有数值句", has_number)
    has_cause = any("因为" in p["source_sentence"] or "所以" in p["source_sentence"] for p in pts)
    check("要点里有因果句", has_cause)

    print("\n  —— 空正文返回空列表 ——")
    check("空字符串返回 []", extract_key_points("", SCHEMA) == [])
    check("纯空格返回 []", extract_key_points("   ", SCHEMA) == [])
    check("只有标点的文本返回 []", extract_key_points("。！？；", SCHEMA) == [])
    check("没有定义/数值/因果的正文返回 []", extract_key_points("今天天气不错，出去走走吧。", SCHEMA) == [])

    print("\n  —— max_points 限制 ——")
    long_text = (
        "苹果是一种常见的水果，颜色多为红色或绿色。"
        "香蕉是一种热带水果，富含钾元素，价格便宜。"
        "葡萄是一种浆果，含糖量高达 20%，适合酿酒。"
        "橙子是一种柑橘类水果，维生素 C 含量约 53mg/100g。"
        "梨是一种水分丰富的水果，单果重量约 200 克。"
        "桃子是一种夏季水果，果肉柔软，含铁量低。"
    )
    pts3 = extract_key_points(long_text, SCHEMA, max_points=3)
    check("max_points=3 截到 3 条", len(pts3) == 3, str(len(pts3)))
    pts1 = extract_key_points(long_text, SCHEMA, max_points=1)
    check("max_points=1 截到 1 条", len(pts1) == 1, str(len(pts1)))
    pts5 = extract_key_points(long_text, SCHEMA, max_points=5)
    check("max_points=5 截到 5 条", len(pts5) == 5, str(len(pts5)))
    # 每条都还能溯源
    check("max_points=3 截断后每条仍可溯源", all(p["source_sentence"] in long_text for p in pts3))

    print("\n  —— 同一句子命中多种 pattern 只算一次 ——")
    multi = "MCP 是一种协议，因为它有协议号，所以它叫协议，定义为一类规范。"
    pts_multi = extract_key_points(multi, SCHEMA)
    check("同句子多 pattern 不重复出现", len(pts_multi) == 1, str(len(pts_multi)))
    check("那条要点仍可溯源", pts_multi[0]["source_sentence"] in multi)

    print("\n  —— OfflineModel：标准 OpenAI message 格式 ——")
    m = OfflineModel()
    msg = m.complete([{"role": "user", "content": SAMPLE}])
    check("complete 返回 role=assistant", msg["role"] == "assistant")
    check("complete 返回 content（文本格式要点）", isinstance(msg.get("content"), str) and msg["content"])
    check("complete 返回 points（结构化）", isinstance(msg.get("points"), list))
    check("complete 后 calls=1", m.calls == 1)
    check("模型 name=offline-rules", m.name == "offline-rules")
    # 模型输出的 points 也必须可溯源
    m_pts = msg.get("points") or []
    check("模型输出的要点也全部可溯源", all(p["source_sentence"] in SAMPLE for p in m_pts),
          str([(p["source_sentence"][:30], p["source_sentence"] in SAMPLE) for p in m_pts]))

    msg_empty = OfflineModel().complete([{"role": "user", "content": ""}])
    check("空输入：模型返回空 points", msg_empty.get("points") == [])
    check("空输入：content 提示未提取到", "未提取到" in msg_empty["content"])

    print("\n  —— schema 形状 ——")
    check("schema type=object", SCHEMA["type"] == "object")
    check("schema properties.points 是 array", SCHEMA["properties"]["points"]["type"] == "array")
    check("schema points.items 有 point/source_sentence",
          set(SCHEMA["properties"]["points"]["items"]["properties"].keys()) == {"point", "source_sentence"})
    check("schema additionalProperties=false", SCHEMA.get("additionalProperties") is False)
    check("schema points.items additionalProperties=false",
          SCHEMA["properties"]["points"]["items"].get("additionalProperties") is False)

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    h = client.get("/health").json()
    check("GET /health", h["ok"] is True and h["model"] == "offline-rules")

    sch = client.get("/schema").json()
    check("GET /schema 返回 SCHEMA", sch["type"] == "object")

    mi = client.get("/model").json()
    check("GET /model 返回模型信息", mi["name"] == "offline-rules" and "schema" in mi)

    body = client.post("/extract", json={"text": SAMPLE}).json()
    check("POST /extract 提取 3-5 条", 3 <= body["count"] <= 5, str(body["count"]))
    check("POST /extract all_sourced=True", body["all_sourced"] is True)
    check("POST /extract 返回 sourcing 数组", isinstance(body["sourcing"], list) and len(body["sourcing"]) == body["count"])

    body = client.post("/extract/model", json={"text": SAMPLE}).json()
    check("POST /extract/model 走 OfflineModel", body["model_calls"] == 1)
    check("POST /extract/model all_sourced=True", body["all_sourced"] is True)
    check("POST /extract/model raw_content 非空", bool(body["raw_content"]))

    body = client.post("/extract", json={"text": ""}).status_code == 422
    check("POST /extract 空 text 被 pydantic 拒绝（422）", body)

    body = client.post("/extract", json={"text": "今天天气不错。"}).json()
    check("POST /extract 无可提取句返回 count=0", body["count"] == 0)
    check("POST /extract 无可提取句 all_sourced=True（空也 True）", body["all_sourced"] is True)

    ridx = client.get("/")
    check("GET / 返回 200", ridx.status_code == 200)
    check("GET / 返回 HTML", "<html" in ridx.text.lower())

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
