"""Unit 35 · 多工具路由 —— 工具一多，主要矛盾从「怎么执行」变成「选哪个」。

离线路由器用最朴素的办法：把问题和工具的全部文字都切成 1~3 字的片段，
按 IDF 加权求交集。不用向量、不用模型，纯确定性 —— 这样才能量出「描述写得好不好」的差别。

三个可比的配置：
    TOOLS_5        五个工具，描述完整        → 准确率最高
    TOOLS_5_VAGUE  描述被写成「换算相关」这种废话
    TOOLS_5_BARE   完全去掉 description，只剩工具名

关键结论（自检里跑出来的）：
    描述不是注释，是路由器唯一能看见的信息。
"""

from __future__ import annotations

import json
import math
import re

CJK = re.compile(r"[\u4e00-\u9fa5]+")
ASCII = re.compile(r"[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+)*")
MAX_N = 3


# ---------------------------------------------------------------------------
# 一、五个工具
# ---------------------------------------------------------------------------
def get_weather(city: str) -> dict:
    table = {"上海": (18, "多云", 62), "北京": (9, "晴", 35), "广州": (24, "小雨", 81), "深圳": (25, "阴", 74)}
    if city not in table:
        raise ValueError(f"没有 {city} 的数据")
    t, sky, h = table[city]
    return {"city": city, "temp_c": t, "sky": sky, "humidity": h}


def calculate(expression: str) -> dict:
    if not re.fullmatch(r"[0-9+\-*/().%\s]+", expression or ""):
        raise ValueError("表达式只能包含数字和 + - * / ( ) %")
    return {"expression": expression, "value": round(eval(expression, {"__builtins__": {}}, {}), 4)}  # noqa: S307 - 已用正则白名单，见 Unit 31


RATES = {"人民币": 1.0, "美元": 7.1, "欧元": 7.8, "日元": 0.047}


def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    if from_currency not in RATES or to_currency not in RATES:
        raise ValueError(f"支持的货币：{list(RATES)}")
    value = amount * RATES[from_currency] / RATES[to_currency]
    return {"amount": amount, "from": from_currency, "to": to_currency, "value": round(value, 2)}


UNITS = {
    "公里": ("长度", 1000.0), "米": ("长度", 1.0), "厘米": ("长度", 0.01),
    "公斤": ("重量", 1.0), "磅": ("重量", 0.4536), "克": ("重量", 0.001),
}


def convert_unit(value: float, from_unit: str, to_unit: str) -> dict:
    if from_unit not in UNITS or to_unit not in UNITS:
        raise ValueError(f"支持的单位：{list(UNITS)}")
    a, b = UNITS[from_unit], UNITS[to_unit]
    if a[0] != b[0]:
        raise ValueError(f"{from_unit} 和 {to_unit} 不是同一类量纲")
    return {"value": value, "from": from_unit, "to": to_unit, "result": round(value * a[1] / b[1], 4)}


ORDERS = {
    "SO-000123": {"order_id": "SO-000123", "item": "降噪耳机", "status": "已发货", "amount": 899.0},
    "SO-000456": {"order_id": "SO-000456", "item": "机械键盘", "status": "已签收", "amount": 459.0},
}


def lookup_order(order_id: str) -> dict:
    if order_id not in ORDERS:
        raise ValueError(f"没有订单 {order_id}")
    return ORDERS[order_id]


IMPL = {
    "get_weather": get_weather,
    "calculate": calculate,
    "convert_currency": convert_currency,
    "convert_unit": convert_unit,
    "lookup_order": lookup_order,
}

_OBJ = lambda props, required: {  # noqa: E731
    "type": "object",
    "properties": props,
    "required": required,
    "additionalProperties": False,
}

TOOLS_5 = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市今天的天气，返回温度、天气现象和湿度。只能查上海、北京、广州、深圳。",
            "parameters": _OBJ({"city": {"type": "string", "enum": ["上海", "北京", "广州", "深圳"], "description": "城市名"}}, ["city"]),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "计算一个算术表达式，支持加减乘除、括号和乘方。例如 (120-30)*0.8。",
            "parameters": _OBJ({"expression": {"type": "string", "description": "算术表达式，例如 (120-30)*0.8"}}, ["expression"]),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_currency",
            "description": "把一种货币换算成另一种货币，按今天的汇率。例如 100 美元换人民币、5 欧元换日元。",
            "parameters": _OBJ(
                {
                    "amount": {"type": "number", "description": "金额"},
                    "from_currency": {"type": "string", "enum": list(RATES), "description": "原货币"},
                    "to_currency": {"type": "string", "enum": list(RATES), "description": "目标货币"},
                },
                ["amount", "from_currency", "to_currency"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_unit",
            "description": "换算长度、重量、温度的单位。例如 5 公里换米、180 磅换公斤。",
            "parameters": _OBJ(
                {
                    "value": {"type": "number", "description": "数值"},
                    "from_unit": {"type": "string", "enum": list(UNITS), "description": "原单位"},
                    "to_unit": {"type": "string", "enum": list(UNITS), "description": "目标单位"},
                },
                ["value", "from_unit", "to_unit"],
            ),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "按订单号查询订单的物流状态和金额。订单号形如 SO-000123。",
            "parameters": _OBJ({"order_id": {"type": "string", "description": "订单号，形如 SO-000123"}}, ["order_id"]),
        },
    },
]

TOOLS_2 = [TOOLS_5[0], TOOLS_5[1]]

VAGUE_DESC = {
    "get_weather": "处理天气相关的请求。",
    "calculate": "处理数字相关的请求。",
    "convert_currency": "处理换算相关的请求。",
    "convert_unit": "处理换算相关的请求。",
    "lookup_order": "处理订单相关的请求。",
}


def _clone(tools: list[dict]) -> list[dict]:
    return json.loads(json.dumps(tools, ensure_ascii=False))


def vague_tools() -> list[dict]:
    out = _clone(TOOLS_5)
    for t in out:
        t["function"]["description"] = VAGUE_DESC[t["function"]["name"]]
    return out


def bare_tools() -> list[dict]:
    out = _clone(TOOLS_5)
    for t in out:
        del t["function"]["description"]
        for p in t["function"]["parameters"]["properties"].values():
            p.pop("description", None)
    return out


# ---------------------------------------------------------------------------
# 二、路由器：n-gram 交集 + IDF
# ---------------------------------------------------------------------------
def ngrams(text: str) -> set[str]:
    text = (text or "").lower()
    out: set[str] = set()
    for run in CJK.findall(text):
        L = len(run)
        for n in range(1, min(MAX_N, L) + 1):
            for i in range(L - n + 1):
                out.add(run[i : i + n])
    for tok in ASCII.findall(text):
        out.add(tok)
    return out


def tool_text(tool: dict) -> str:
    fn = tool["function"]
    parts = [fn["name"].replace("_", " "), fn.get("description", "")]
    for name, spec in (fn["parameters"].get("properties") or {}).items():
        parts.append(name.replace("_", " "))
        parts.append(spec.get("description", ""))
        parts.append(" ".join(str(x) for x in spec.get("enum") or []))
    return " ".join(p for p in parts if p)


def rank(question: str, tools: list[dict]) -> list[tuple[str, float]]:
    """返回 [(工具名, 分数)]，按分数降序。分数为 0 表示没有任何词面交集。"""
    feats = {t["function"]["name"]: ngrams(tool_text(t)) for t in tools}
    df: dict[str, int] = {}
    for fs in feats.values():
        for f in fs:
            df[f] = df.get(f, 0) + 1
    n = len(tools)
    q = ngrams(question)

    scored = []
    for name, fs in feats.items():
        score = 0.0
        for f in q & fs:
            idf = math.log((n + 1) / (df[f] + 0.5))
            score += idf * (len(f) ** 1.3)
        scored.append((name, round(score, 3)))
    return sorted(scored, key=lambda x: (-x[1], x[0]))


def route(question: str, tools: list[dict]) -> tuple[str | None, float]:
    ranked = rank(question, tools)
    if not ranked or ranked[0][1] <= 0:
        return None, 0.0
    return ranked[0]


def explain(question: str, tools: list[dict], top: int = 3) -> dict:
    """给页面用：选出谁、凭什么、其他候选差在哪。"""
    ranked = rank(question, tools)
    feats = {t["function"]["name"]: ngrams(tool_text(t)) for t in tools}
    df: dict[str, int] = {}
    for fs in feats.values():
        for f in fs:
            df[f] = df.get(f, 0) + 1
    q = ngrams(question)
    detail = []
    for name, score in ranked[:top]:
        hits = sorted(q & feats[name], key=lambda f: (-len(f), f))[:8]
        detail.append({"name": name, "score": score, "hits": hits})
    return {"picked": ranked[0][0] if ranked and ranked[0][1] > 0 else None, "ranked": ranked, "detail": detail}


# ---------------------------------------------------------------------------
# 三、10 道固定问题 + 准确率
# ---------------------------------------------------------------------------
QUESTIONS: list[dict] = [
    {"q": "上海今天多少度", "expect": "get_weather"},
    {"q": "北京会下雨吗", "expect": "get_weather"},
    {"q": "(120-30)*0.8 等于多少", "expect": "calculate"},
    {"q": "帮我算 99 乘 37", "expect": "calculate"},
    {"q": "100 美元能换多少人民币", "expect": "convert_currency"},
    {"q": "5 欧元是多少日元", "expect": "convert_currency"},
    {"q": "5 公里是多少米", "expect": "convert_unit"},
    {"q": "180 磅是多少公斤", "expect": "convert_unit"},
    {"q": "订单 SO-000123 到哪了", "expect": "lookup_order"},
    {"q": "SO-000456 现在什么状态", "expect": "lookup_order"},
]

# 没有对应工具时，路由器不会说「我不知道」，而是把问题塞给最像的那个 —— 这是最危险的误选：
# 它看起来「有答案」，只是答案答的是另一个问题。记录在案，不删。
NO_TOOL: list[dict] = [
    {"q": "深圳到北京的机票多少钱", "expect": None, "why": "只有城市名能对上，于是被塞给 get_weather"},
    {"q": "上海有什么好吃的", "expect": None, "why": "同样是城市名在起作用"},
]


def accuracy(tools: list[dict], questions: list[dict] | None = None) -> dict:
    questions = questions or QUESTIONS
    rows = []
    for item in questions:
        picked, score = route(item["q"], tools)
        rows.append({"q": item["q"], "expect": item["expect"], "picked": picked, "score": score, "ok": picked == item["expect"]})
    correct = sum(1 for r in rows if r["ok"])
    return {"correct": correct, "total": len(rows), "rate": round(correct / len(rows), 2), "rows": rows}
