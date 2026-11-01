"""Unit 50 · 自定义 Tool —— 写一个真正有用的 tool。

启动：
    /home/rik/.cache/aj/runner/bin/python -m uvicorn main:app --port 8050
    打开 http://127.0.0.1:8050/

自检（会真的起 stdio 子进程、握手、调工具）：
    /home/rik/.cache/aj/runner/bin/python main.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import anyio
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import client as mcp_client
import server as tools_server

BASE_DIR = Path(__file__).parent
STATIC_INDEX = BASE_DIR / "static" / "index.html"

app = FastAPI(title="Unit 50 · 自定义 Tool", version="0.1.0")


class CallIn(BaseModel):
    name: str = Field(description="工具名，必须是 list_tools 里的名字")
    arguments: dict = Field(default_factory=dict, description="按 input_schema 填的参数")


SAMPLES: list[dict] = [
    {
        "tool": "fx_rate",
        "arguments": {"amount": 100, "from_currency": "USD", "to_currency": "CNY"},
        "note": "纯函数：同样的参数永远同样的结果，可以放心重试",
    },
    {
        "tool": "fx_rate",
        "arguments": {"amount": 100, "from_currency": "USD", "to_currency": "GBP"},
        "note": "不支持的货币：工具自己兜住，is_error 仍是 false，模型能读到原因",
    },
    {
        "tool": "unit_convert",
        "arguments": {"value": 100, "from_unit": "c", "to_unit": "f"},
        "note": "温度是特殊分支，不是简单乘系数",
    },
    {
        "tool": "unit_convert",
        "arguments": {"value": 3, "from_unit": "km", "to_unit": "kg"},
        "note": "跨组换算必须拒绝 —— 这就是「宁可报错也不要瞎算」",
    },
    {
        "tool": "csv_stats",
        "arguments": {"column": "qty"},
        "note": "有状态：结果取决于 data/sales.csv 的内容",
    },
    {
        "tool": "append_note",
        "arguments": {"note": "今天学了 tool schema 的三类设计"},
        "note": "有副作用：每点一次 data/notes.log 就多一行",
    },
]


@app.get("/health", summary="服务端身份 + 数据文件状态")
def health() -> dict:
    catalog = mcp_client.fetch_catalog()
    return {
        "ok": True,
        "server": catalog["server"],
        "tool_count": len(catalog["tools"]),
        "tools": [t["name"] for t in catalog["tools"]],
        "sales_csv": str(tools_server.SALES_CSV),
        "sales_rows": len(tools_server.read_column("qty")),
        "notes_log_lines": tools_server.count_notes(),
    }


@app.get("/api/tools", summary="工具清单（带 description 与 input_schema，前端据此生成表单）")
def api_tools() -> dict:
    catalog = mcp_client.fetch_catalog()
    return {"count": len(catalog["tools"]), "items": catalog["tools"]}


@app.get("/api/samples", summary="六个可点的样例参数")
def api_samples() -> dict:
    return {"items": SAMPLES}


@app.post("/api/call", summary="真的调一次工具")
def api_call(req: CallIn) -> dict:
    result = mcp_client.invoke(req.name, req.arguments)
    return {"name": req.name, "arguments": req.arguments, **result}


@app.get("/", include_in_schema=False)
def index():
    if STATIC_INDEX.exists():
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

    print("Unit 50 · 自定义 Tool 自检（真起 stdio 子进程）\n")

    print("  —— 纯函数：不经 MCP 也能验 ——")
    check("币种别名 rmb → CNY", tools_server.normalize_currency("rmb") == "CNY")
    check("未知币种归一化成空串", tools_server.normalize_currency("GBP") == "")
    check("100 USD = 712 CNY", tools_server.fx_convert(100, "USD", "CNY") == 712.0,
          str(tools_server.fx_convert(100, "USD", "CNY")))
    check("换算可逆（误差 < 1e-9）",
          abs(tools_server.fx_convert(tools_server.fx_convert(100, "USD", "JPY"), "JPY", "USD") - 100) < 1e-9)
    check("单位分组：km→length", tools_server.unit_group("km") == "length")
    check("单位分组不区分大小写：LB→weight", tools_server.unit_group("LB") == "weight")
    check("单位分组：C→temperature", tools_server.unit_group("C") == "temperature")
    check("100c → 212f", tools_server.convert_value(100, "c", "f") == 212.0)
    check("0c → 273.15k", abs(tools_server.convert_value(0, "c", "k") - 273.15) < 1e-9)
    check("1km → 1000m", tools_server.convert_value(1, "km", "m") == 1000.0)
    check("1lb → 453.5924g", abs(tools_server.convert_value(1, "lb", "g") - 453.5924) < 1e-6)

    print("\n  —— 有状态：CSV 是真的从磁盘读的 ——")
    qty = tools_server.read_column("qty")
    stats = tools_server.compute_stats(qty)
    check("sales.csv 有 20 行", len(qty) == 20, str(len(qty)))
    check("qty 总和 = 97", stats["sum"] == 97.0, str(stats["sum"]))
    check("qty 均值 = 4.85", stats["mean"] == 4.85, str(stats["mean"]))
    check("unit_price 均值 = 2308", tools_server.compute_stats(tools_server.read_column("unit_price"))["mean"] == 2308.0)
    check("fmt_num 去掉多余小数", tools_server.fmt_num(97.0) == "97" and tools_server.fmt_num(4.85) == "4.85")

    print("\n  —— 握手与工具清单（stdio 子进程）——")
    async def probe() -> dict:
        out: dict = {}
        c = mcp_client.MCPClient()
        await c.connect()
        out["info"] = c.server_info
        out["tools"] = await c.list_tools()
        out["by_name"] = {t["name"]: t for t in out["tools"]}
        out["fx_ok"] = await c.call_tool("fx_rate", {"amount": 100, "from_currency": "USD", "to_currency": "CNY"})
        out["fx_bad"] = await c.call_tool("fx_rate", {"amount": 100, "from_currency": "USD", "to_currency": "GBP"})
        out["temp"] = await c.call_tool("unit_convert", {"value": 100, "from_unit": "c", "to_unit": "f"})
        out["bad_unit"] = await c.call_tool("unit_convert", {"value": 1, "from_unit": "furlong", "to_unit": "m"})
        out["cross"] = await c.call_tool("unit_convert", {"value": 3, "from_unit": "km", "to_unit": "kg"})
        out["csv"] = await c.call_tool("csv_stats", {"column": "qty"})
        out["csv_sum"] = await c.call_tool("csv_stats", {"column": "unit_price", "op": "mean"})
        out["csv_text_col"] = await c.call_tool("csv_stats", {"column": "region"})
        out["csv_bad_op"] = await c.call_tool("csv_stats", {"column": "qty", "op": "median"})
        before = tools_server.count_notes()
        out["note"] = await c.call_tool("append_note", {"note": "自检写入的一行"})
        out["note_before"] = before
        out["note_after"] = tools_server.count_notes()
        out["note_empty"] = await c.call_tool("append_note", {"note": "   "})
        out["type_err"] = await c.call_tool("unit_convert", {"value": "abc", "from_unit": "m", "to_unit": "km"})
        out["no_tool"] = await c.call_tool("nope", {})
        out["missing_arg"] = await c.call_tool("fx_rate", {"amount": 1})
        await c.close()
        return out

    facts = anyio.run(probe)
    check("服务端名 unit50-tools", facts["info"]["name"] == "unit50-tools", str(facts["info"]))
    check("协议版本 2025-11-25", facts["info"]["protocol_version"] == "2025-11-25")
    check("能力表含 tools", "tools" in facts["info"]["capabilities"], str(facts["info"]["capabilities"]))
    check("列出 4 个工具", len(facts["tools"]) == 4, str([t["name"] for t in facts["tools"]]))
    check("每个工具都有非空 description", all(len(t["description"]) > 20 for t in facts["tools"]))
    check("fx_rate 的 description 说清了币种范围", "USD" in facts["by_name"]["fx_rate"]["description"])
    check("unit_convert 的 description 列了三组单位",
          all(k in facts["by_name"]["unit_convert"]["description"] for k in ("长度", "重量", "温度")))
    check("append_note 的 description 标注了副作用", "副作用" in facts["by_name"]["append_note"]["description"])

    fx_schema = facts["by_name"]["fx_rate"]["input_schema"]
    check("fx_rate 三个参数都是必填",
          sorted(fx_schema["required"]) == ["amount", "from_currency", "to_currency"], str(fx_schema.get("required")))
    check("fx_rate 的 amount 是 number", fx_schema["properties"]["amount"]["type"] == "number")
    check("fx_rate 的币种是 string", fx_schema["properties"]["to_currency"]["type"] == "string")
    csv_schema = facts["by_name"]["csv_stats"]["input_schema"]
    check("csv_stats 只有 column 必填（op 有默认值）", csv_schema["required"] == ["column"], str(csv_schema.get("required")))
    check("csv_stats 的 op 是 string", csv_schema["properties"]["op"]["type"] == "string")

    print("\n  —— 调用：成功的路径 ——")
    check("100 USD→CNY 文本含 712.00", "712.00" in facts["fx_ok"]["text"], facts["fx_ok"]["text"])
    check("成功时 is_error 为 False", facts["fx_ok"]["is_error"] is False)
    check("返回体只有 text 一块", facts["fx_ok"]["blocks"] == ["text"])
    check("100c→212f（温度不是乘法）", "212" in facts["temp"]["text"] and "temperature" in facts["temp"]["text"])
    check("csv_stats qty 一次给全五项",
          all(s in facts["csv"]["text"] for s in ("rows=20", "sum=97", "mean=4.85", "min=1", "max=15")),
          facts["csv"]["text"])
    check("csv_stats 支持单指标（mean）", "mean=2308" in facts["csv_sum"]["text"], facts["csv_sum"]["text"])

    print("\n  —— 调用：工具自己兜住的错误（is_error=False）——")
    check("不支持的货币返回中文错误", facts["fx_bad"]["text"].startswith("错误：不认识货币"), facts["fx_bad"]["text"])
    check("自己兜的错 is_error 仍是 False", facts["fx_bad"]["is_error"] is False)
    check("错误里列出可用币种", "CNY" in facts["fx_bad"]["text"] and "JPY" in facts["fx_bad"]["text"])
    check("非法单位返回中文错误", facts["bad_unit"]["text"].startswith("错误：不认识单位"), facts["bad_unit"]["text"])
    check("非法单位时 is_error 仍为 False", facts["bad_unit"]["is_error"] is False)
    check("跨组换算被拒", "不能互换" in facts["cross"]["text"], facts["cross"]["text"])
    check("文本列统计被拒", "不是数值列" in facts["csv_text_col"]["text"], facts["csv_text_col"]["text"])
    check("不支持的 op 被拒", "不支持" in facts["csv_bad_op"]["text"], facts["csv_bad_op"]["text"])

    print("\n  —— 有副作用：写就是写了 ——")
    check("append_note 真的让 notes.log 多一行", facts["note_after"] == facts["note_before"] + 1,
          f"{facts['note_before']} → {facts['note_after']}")
    check("返回文本里报了行号", f"第 {facts['note_after']} 条" in facts["note"]["text"], facts["note"]["text"])
    check("空 note 不写入", facts["note_after"] == tools_server.count_notes() and "空" in facts["note_empty"]["text"])
    check("副作用工具调用成功时 is_error 为 False", facts["note"]["is_error"] is False)

    print("\n  —— 框架层的失败（is_error=True，且文本被脱敏）——")
    check("参数类型错 → is_error=True", facts["type_err"]["is_error"] is True, str(facts["type_err"]))
    check("参数类型错的文本以 Error executing tool 开头",
          facts["type_err"]["text"].startswith("Error executing tool unit_convert"), facts["type_err"]["text"][:80])
    check("未知工具 → is_error=True（2.3.0 不抛异常）",
          facts["no_tool"]["is_error"] is True, str(facts["no_tool"]))
    check("未知工具的文本是 Unknown tool",
          "Unknown tool" in facts["no_tool"]["text"], facts["no_tool"]["text"])
    check("缺必填参数 → is_error=True", facts["missing_arg"]["is_error"] is True, str(facts["missing_arg"]))
    check("缺参文本里点名了缺哪个字段",
          "from_currency" in facts["missing_arg"]["text"], facts["missing_arg"]["text"][:120])

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    http = TestClient(app)
    r = http.get("/health")
    h = r.json()
    check("GET /health 200", r.status_code == 200)
    check("/health 报出 4 个工具", h["tool_count"] == 4, str(h))
    check("/health 报出 CSV 行数 20", h["sales_rows"] == 20)
    check("/health 暴露服务端名", h["server"]["name"] == "unit50-tools")

    r = http.get("/api/tools")
    d = r.json()
    check("GET /api/tools 返回 4 条", d["count"] == 4)
    check("每条都带 input_schema（前端靠它生成表单）",
          all("input_schema" in t and "properties" in t["input_schema"] for t in d["items"]))

    r = http.post("/api/call", json={"name": "fx_rate", "arguments": {
        "amount": 100, "from_currency": "USD", "to_currency": "CNY"}})
    check("POST /api/call 走通 stdio", "712.00" in r.json()["text"], str(r.json())[:160])
    check("POST /api/call 回传 is_error", r.json()["is_error"] is False)

    r = http.post("/api/call", json={"name": "unit_convert", "arguments": {
        "value": 1, "from_unit": "km", "to_unit": "kg"}})
    check("HTTP 调跨组换算拿到中文拒绝", "不能互换" in r.json()["text"])

    r = http.post("/api/call", json={"name": "nope", "arguments": {}})
    check("HTTP 调未知工具 is_error=True", r.json()["is_error"] is True)

    r = http.get("/api/samples")
    check("样例 6 个且都带 note", len(r.json()["items"]) == 6 and all(s["note"] for s in r.json()["items"]))

    r = http.get("/")
    check("GET / 返回页面", r.status_code == 200 and "Unit 50" in r.text)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
