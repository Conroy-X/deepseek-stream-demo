"""Unit 31 · 工具定义与执行 —— Function Calling 的另一半。

模型只会"说要调什么"，真正执行的是你。
所以这一层要守住三件事：

    1. 参数校验：模型给的 JSON 不可信，缺字段/类型错/枚举外都要在**执行前**拦下
    2. 安全执行：计算器不能用 eval，天气只查白名单城市
    3. 结果回填：执行失败也要变成一条 role=tool 的消息，而不是让整个请求 500

工具描述（JSON Schema）不是文档，是**协议**：
模型靠它决定传什么参数，写含糊了它就会乱传。
"""

from __future__ import annotations

import ast
import json
import operator
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# 工具声明：OpenAI / DeepSeek 通用格式
# ---------------------------------------------------------------------------
TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "查询指定城市今天的天气。只能查内置的几个城市。",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "enum": ["上海", "北京", "广州", "深圳"],
                        "description": "城市名，必须是枚举里的一个",
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["c", "f"],
                        "description": "温度单位，默认摄氏度",
                    },
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "计算一个算术表达式，支持 + - * / % ** 和括号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "例如 (120-30)*0.8",
                    }
                },
                "required": ["expression"],
            },
        },
    },
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}


class ToolError(Exception):
    """工具层的可预期错误。会作为 role=tool 的内容回给模型。"""


@dataclass
class ToolResult:
    name: str
    ok: bool
    content: str       # 回给模型的字符串（JSON 或错误说明）
    data: Any = None   # 给前端看的结构化结果

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "content": self.content, "data": self.data}


# ---------------------------------------------------------------------------
# 参数校验：只实现本单元用到的 JSON Schema 子集（type / required / enum）
# ---------------------------------------------------------------------------
_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
}


def validate_args(tool_name: str, args: dict) -> dict:
    """校验并归一化参数。返回干净的新 dict，不改原对象。"""
    if tool_name not in TOOL_NAMES:
        raise ToolError(f"未知工具 {tool_name}")

    schema = next(t["function"]["parameters"] for t in TOOLS if t["function"]["name"] == tool_name)
    props: dict = schema.get("properties", {})
    required: list[str] = schema.get("required", [])

    if not isinstance(args, dict):
        raise ToolError("arguments 必须是 JSON 对象")

    missing = [k for k in required if k not in args]
    if missing:
        raise ToolError(f"缺少必填参数：{', '.join(missing)}")

    unknown = [k for k in args if k not in props]
    if unknown:
        # 严格模式：多余字段直接拒绝，而不是默默忽略。
        # 模型多传字段往往意味着它理解错了工具用途。
        raise ToolError(f"出现未定义参数：{', '.join(unknown)}")

    clean: dict = {}
    for k, v in args.items():
        spec = props[k]
        expect = spec.get("type")
        py = _TYPE_MAP.get(expect or "string")
        if py and not isinstance(v, py):
            raise ToolError(f"参数 {k} 应为 {expect}，收到 {type(v).__name__}")
        if "enum" in spec and v not in spec["enum"]:
            raise ToolError(f"参数 {k} 只能取 {spec['enum']}，收到 {v!r}")
        clean[k] = v
    return clean


# ---------------------------------------------------------------------------
# 两个工具的实现
# ---------------------------------------------------------------------------
_WEATHER = {
    "上海": {"temp_c": 18, "sky": "多云", "humidity": 62},
    "北京": {"temp_c": 9, "sky": "晴", "humidity": 35},
    "广州": {"temp_c": 24, "sky": "小雨", "humidity": 81},
    "深圳": {"temp_c": 25, "sky": "阴", "humidity": 74},
}

_ALLOWED_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval(node: ast.AST) -> float:
    """只允许算术节点。绝对不要用 eval() —— 那是远程代码执行入口。"""
    if isinstance(node, ast.Expression):
        return _safe_eval(node.body)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise ToolError("表达式里只允许数字")
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_OPS:
        left, right = _safe_eval(node.left), _safe_eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 64:
            raise ToolError("指数太大，拒绝计算")
        return _ALLOWED_OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_OPS:
        return _ALLOWED_OPS[type(node.op)](_safe_eval(node.operand))
    raise ToolError("表达式包含不允许的语法")


def get_weather(city: str, unit: str = "c") -> dict:
    if city not in _WEATHER:
        raise ToolError(f"没有 {city} 的天气数据（只支持 {list(_WEATHER)}）")
    w = _WEATHER[city]
    temp = w["temp_c"] if unit == "c" else round(w["temp_c"] * 9 / 5 + 32, 1)
    return {"city": city, "temp": temp, "unit": unit, "sky": w["sky"], "humidity": w["humidity"]}


def calculate(expression: str) -> dict:
    if len(expression) > 120:
        raise ToolError("表达式太长")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ToolError(f"表达式语法错误：{e.msg}") from e
    value = _safe_eval(tree)
    return {"expression": expression, "value": value}


def execute(tool_name: str, raw_args: dict) -> ToolResult:
    """校验 + 执行。任何失败都返回 ok=False 的 ToolResult，绝不抛到外层。"""
    try:
        args = validate_args(tool_name, raw_args)
        data = get_weather(**args) if tool_name == "get_weather" else calculate(**args)
        return ToolResult(name=tool_name, ok=True, content=json.dumps(data, ensure_ascii=False), data=data)
    except ToolError as e:
        return ToolResult(name=tool_name, ok=False, content=json.dumps({"error": str(e)}, ensure_ascii=False))
    except Exception as e:  # noqa: BLE001 - 工具内部异常也要变成可读结果
        return ToolResult(
            name=tool_name,
            ok=False,
            content=json.dumps({"error": f"工具内部错误：{type(e).__name__}"}, ensure_ascii=False),
        )
