"""Unit 48 · 三大能力：Tools / Resources / Prompts。

新手最容易犯的错是把这三个当成「三个并列的清单」，于是随便挑一个放进去。
它们不是并列关系 —— 真正的区别在**谁决定用它**：

  Tools      模型决定      「我要算个数」→ 模型自己调，可以改状态
  Resources  应用决定      「这个文档要带上」→ 宿主 UI 里勾选，只读，不该有副作用
  Prompts    用户决定      「/总结」→ 用户显式触发，把一段预设模板塞进对话

这条控制轴决定了三件事：谁有权限、能不能有副作用、UI 上摆在哪。
分错了不会报错，只会「用起来很别扭」—— 这才是最难发现的问题，
所以本单元最后写了一个 `classify()` 来主动挑刺。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

JSONRPC = "2.0"
PROTOCOL_VERSION = "2025-06-18"

#: 「谁来决定用它」—— 这一列是整章的主轴。
CONTROL_AXIS: dict[str, dict[str, str]] = {
    "tools": {
        "owner": "模型",
        "trigger": "模型在对话中自己判断该不该调",
        "side_effect": "允许（这正是它的价值）",
        "readonly": "不保证",
        "ui": "通常不直接展示，只在调用记录里出现",
        "wrong_use": "把「读一份文档」做成 tool —— 模型不知道有这份文档，永远不会调",
    },
    "resources": {
        "owner": "应用（宿主）",
        "trigger": "宿主 UI 里勾选 / 自动挂载上下文",
        "side_effect": "**不该有**（只读上下文）",
        "readonly": "是",
        "ui": "资源面板、@ 提及列表",
        "wrong_use": "把「下单」做成 resource —— 用户点一下就改了状态，没有任何确认",
    },
    "prompts": {
        "owner": "用户",
        "trigger": "用户显式选一个模板（类似斜杠命令）",
        "side_effect": "无（只产出消息）",
        "readonly": "是",
        "ui": "斜杠命令、模板下拉",
        "wrong_use": "把「查天气」做成 prompt —— 用户得先知道有这个东西才能用",
    },
}


class RpcError(Exception):
    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def to_error(self, msg_id: Any) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            err["data"] = self.data
        return {"jsonrpc": JSONRPC, "id": msg_id, "error": err}


METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


# ── URI 模板：resource 唯一需要一点解析的地方 ────────────────────────────────


class UriTemplate:
    """`doc://kb/{topic}` 这种。只支持单层 `{name}`，够用了。

    为什么 resource 需要模板而 tool 不需要：tool 用 JSON Schema 描述参数，
    resource 的「参数」被塞进了 URI 本身 —— 因为 resource 的身份就是一个地址。
    """

    _VAR = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self.vars = self._VAR.findall(pattern)
        # _VAR 有一个捕获组，split 之后奇数位是占位符名、偶数位是字面量。
        # 字面量必须 re.escape —— `doc://` 里的 `.` 和 `/` 都是正则元字符。
        parts = self._VAR.split(pattern)
        regex = "".join(
            "([^/]+)" if i % 2 == 1 else re.escape(part) for i, part in enumerate(parts)
        )
        self._regex = re.compile("^" + regex + "$")

    def match(self, uri: str) -> dict[str, str] | None:
        m = self._regex.match(uri)
        if not m:
            return None
        return dict(zip(self.vars, m.groups()))

    def expand(self, **kwargs: str) -> str:
        out = self.pattern
        for name in self.vars:
            if name not in kwargs:
                raise RpcError(INVALID_PARAMS, f"模板缺少参数 {name}", {"required": self.vars})
            out = out.replace("{" + name + "}", kwargs[name])
        return out


# ── 三类能力的描述对象 ───────────────────────────────────────────────────────


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], str]
    annotations: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }
        if self.annotations:
            out["annotations"] = self.annotations
        return out


@dataclass
class Resource:
    uri: str
    name: str
    description: str
    mime_type: str
    loader: Callable[[], str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


@dataclass
class ResourceTemplate:
    template: UriTemplate
    name: str
    description: str
    mime_type: str
    loader: Callable[[dict[str, str]], str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "uriTemplate": self.template.pattern,
            "name": self.name,
            "description": self.description,
            "mimeType": self.mime_type,
        }


@dataclass
class Prompt:
    name: str
    description: str
    arguments: list[dict[str, Any]]
    render: Callable[[dict[str, str]], str]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "arguments": self.arguments}


# ── 演示数据：一个「公司知识库 server」────────────────────────────────────────


LEAVE_DOC = """# 请假制度

- 年假：入职满一年 5 天，满三年 10 天。
- 病假：需提供医院证明，全年不超过 15 天。
- 事假：按日扣薪。
"""

CHANGELOG = """# 变更日志

## 2026-11-01
- 检索接口新增 tenant 过滤。

## 2026-10-20
- 引用定位支持页码跳转。
"""

KB_TOPICS = {
    "onboarding": "# 入职指引\n\n第一天：领设备、开通账号、读安全手册。",
    "expense": "# 报销流程\n\n发票 → 系统提单 → 直属主管审批 → 财务打款（5 个工作日）。",
    "security": "# 安全规范\n\n密码 90 天轮换；禁止把生产数据下载到本地。",
}

PRODUCTS = [
    {"sku": "A-100", "name": "机械键盘", "price": 499, "stock": 12},
    {"sku": "B-200", "name": "人体工学椅", "price": 1899, "stock": 3},
    {"sku": "C-300", "name": "显示器支架", "price": 259, "stock": 27},
]


def _tool_search_products(args: dict[str, Any]) -> str:
    kw = str(args.get("keyword", "")).strip()
    hits = [p for p in PRODUCTS if kw in p["name"] or kw in p["sku"]]
    if not hits:
        return f"没有匹配「{kw}」的商品，共 {len(PRODUCTS)} 个在售"
    return "\n".join(f"{p['sku']} {p['name']} ¥{p['price']} 库存{p['stock']}" for p in hits)


def _tool_reserve_stock(args: dict[str, Any]) -> str:
    sku = str(args.get("sku", ""))
    qty = int(args.get("qty", 0))
    item = next((p for p in PRODUCTS if p["sku"] == sku), None)
    if item is None:
        raise RpcError(INVALID_PARAMS, f"没有这个 SKU：{sku}", {"known": [p["sku"] for p in PRODUCTS]})
    if qty > item["stock"]:
        # 业务失败 → 变成内容，不是协议错误
        return f"库存不足：{sku} 只剩 {item['stock']} 件，你要 {qty} 件"
    item["stock"] -= qty
    return f"已预留 {qty} 件 {sku}，剩余 {item['stock']} 件"


def _tool_count_items(args: dict[str, Any]) -> str:
    return f"在售 {len(PRODUCTS)} 个 SKU，总库存 {sum(p['stock'] for p in PRODUCTS)} 件"


class DemoServer:
    """一个 mini server，把三类能力都挂上。"""

    def __init__(self, capabilities: set[str] | None = None) -> None:
        self.declared = capabilities if capabilities is not None else {"tools", "resources", "prompts"}
        self.reset()

    def reset(self) -> None:
        global PRODUCTS
        PRODUCTS = [
            {"sku": "A-100", "name": "机械键盘", "price": 499, "stock": 12},
            {"sku": "B-200", "name": "人体工学椅", "price": 1899, "stock": 3},
            {"sku": "C-300", "name": "显示器支架", "price": 259, "stock": 27},
        ]
        self.tools: dict[str, Tool] = {
            "search_products": Tool(
                name="search_products",
                description="按关键词或 SKU 前缀搜索在售商品，返回 SKU/名称/价格/库存。",
                input_schema={
                    "type": "object",
                    "properties": {"keyword": {"type": "string", "description": "关键词或 SKU 前缀"}},
                    "required": ["keyword"],
                },
                handler=_tool_search_products,
                annotations={"readOnlyHint": True, "idempotentHint": True},
            ),
            "reserve_stock": Tool(
                name="reserve_stock",
                description="为订单预留库存。会真实扣减库存，不可撤销。",
                input_schema={
                    "type": "object",
                    "properties": {
                        "sku": {"type": "string"},
                        "qty": {"type": "integer", "minimum": 1},
                    },
                    "required": ["sku", "qty"],
                },
                handler=_tool_reserve_stock,
                annotations={"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False},
            ),
            "count_items": Tool(
                name="count_items",
                description="统计在售 SKU 数量与总库存。",
                input_schema={"type": "object", "properties": {}, "required": []},
                handler=_tool_count_items,
                annotations={"readOnlyHint": True, "idempotentHint": True},
            ),
        }

        self.resources: dict[str, Resource] = {
            "doc://handbook/leave": Resource(
                uri="doc://handbook/leave",
                name="请假制度",
                description="公司请假政策全文",
                mime_type="text/markdown",
                loader=lambda: LEAVE_DOC,
            ),
            "doc://changelog": Resource(
                uri="doc://changelog",
                name="变更日志",
                description="系统变更记录",
                mime_type="text/markdown",
                loader=lambda: CHANGELOG,
            ),
        }
        self.templates: list[ResourceTemplate] = [
            ResourceTemplate(
                template=UriTemplate("doc://kb/{topic}"),
                name="知识库条目",
                description="按主题取一条知识库文档",
                mime_type="text/markdown",
                loader=lambda kw: KB_TOPICS.get(kw["topic"])
                or (_raise(RpcError(INVALID_PARAMS, f"未知主题：{kw['topic']}", {"known": sorted(KB_TOPICS)}))),
            )
        ]
        self.prompts: dict[str, Prompt] = {
            "summarize": Prompt(
                name="summarize",
                description="把一段文本总结成三条要点",
                arguments=[{"name": "text", "description": "要总结的文本", "required": True}],
                render=lambda a: f"请把下面这段文本总结成三条要点，每条不超过 20 字：\n\n{a['text']}",
            ),
            "review_sql": Prompt(
                name="review_sql",
                description="审查一条 SQL 的安全性",
                arguments=[
                    {"name": "sql", "description": "待审查的 SQL", "required": True},
                    {"name": "dialect", "description": "方言，默认 sqlite", "required": False},
                ],
                render=lambda a: f"请审查这条 {a.get('dialect', 'sqlite')} SQL 是否有注入风险、是否缺 LIMIT：\n\n{a['sql']}",
            ),
        }

    # ── 能力表 ──────────────────────────────────────────────────────────────

    def capabilities(self) -> dict[str, Any]:
        caps: dict[str, Any] = {}
        if "tools" in self.declared:
            caps["tools"] = {"listChanged": False}
        if "resources" in self.declared:
            caps["resources"] = {"subscribe": False, "listChanged": False}
        if "prompts" in self.declared:
            caps["prompts"] = {"listChanged": False}
        return caps

    def server_info(self) -> dict[str, Any]:
        return {"name": "company-kb", "version": "0.1.0"}

    # ── dispatch ────────────────────────────────────────────────────────────

    def handle(self, frame: dict[str, Any]) -> dict[str, Any] | None:
        if "id" not in frame:
            return None
        msg_id = frame.get("id")
        method = frame.get("method")
        params = frame.get("params") or {}
        try:
            result = self._dispatch(method, params)
            return {"jsonrpc": JSONRPC, "id": msg_id, "result": result}
        except RpcError as e:
            return e.to_error(msg_id)

    def _dispatch(self, method: str | None, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": self.capabilities(),
                "serverInfo": self.server_info(),
            }

        kind = (method or "").split("/")[0]
        if kind in {"tools", "resources", "prompts"} and kind not in self.declared:
            # 没声明这个能力 → 方法不存在，不是「拒绝访问」
            raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}", {"declared": sorted(self.declared)})

        if method == "tools/list":
            return {"tools": [t.to_dict() for t in self.tools.values()]}
        if method == "tools/call":
            return self._call_tool(params)
        if method == "resources/list":
            return {"resources": [r.to_dict() for r in self.resources.values()]}
        if method == "resources/templates/list":
            return {"resourceTemplates": [t.to_dict() for t in self.templates]}
        if method == "resources/read":
            return self._read_resource(params)
        if method == "prompts/list":
            return {"prompts": [p.to_dict() for p in self.prompts.values()]}
        if method == "prompts/get":
            return self._get_prompt(params)
        raise RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")

    def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        tool = self.tools.get(str(name))
        if tool is None:
            raise RpcError(INVALID_PARAMS, f"未知工具：{name}", {"available": sorted(self.tools)})
        args = params.get("arguments") or {}
        missing = [k for k in tool.input_schema.get("required", []) if k not in args]
        if missing:
            raise RpcError(INVALID_PARAMS, f"缺少必填参数：{missing}", {"required": tool.input_schema["required"]})
        try:
            text = tool.handler(args)
        except RpcError:
            raise
        except Exception as e:  # 工具内部炸了 → 变成内容，不是协议错误
            return {"content": [{"type": "text", "text": f"工具执行失败：{e}"}], "isError": True}
        return {"content": [{"type": "text", "text": text}], "isError": False}

    def _read_resource(self, params: dict[str, Any]) -> dict[str, Any]:
        uri = str(params.get("uri") or "")
        direct = self.resources.get(uri)
        if direct is not None:
            return {
                "contents": [
                    {"uri": direct.uri, "mimeType": direct.mime_type, "text": direct.loader()}
                ]
            }
        for tpl in self.templates:
            kw = tpl.template.match(uri)
            if kw is not None:
                return {"contents": [{"uri": uri, "mimeType": tpl.mime_type, "text": tpl.loader(kw)}]}
        raise RpcError(
            INVALID_PARAMS,
            f"未知资源：{uri}",
            {"known": sorted(self.resources), "templates": [t.template.pattern for t in self.templates]},
        )

    def _get_prompt(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name") or "")
        prompt = self.prompts.get(name)
        if prompt is None:
            raise RpcError(INVALID_PARAMS, f"未知提示词：{name}", {"available": sorted(self.prompts)})
        supplied = params.get("arguments") or {}
        missing = [a["name"] for a in prompt.arguments if a.get("required") and a["name"] not in supplied]
        if missing:
            raise RpcError(INVALID_PARAMS, f"缺少必填参数：{missing}", {"args": prompt.arguments})
        text = prompt.render({k: str(v) for k, v in supplied.items()})
        return {
            "description": prompt.description,
            "messages": [{"role": "user", "content": {"type": "text", "text": text}}],
        }


def _raise(exc: Exception) -> Any:
    raise exc


# ── 挑刺器：放错了没有报错，只会难用 ─────────────────────────────────────────

#: 名字看起来就是「读」的动词前缀。命中了还做成 tool，大概率该是 resource。
READ_VERBS = (
    "get_",
    "list_",
    "read_",
    "fetch_",
    "search_",
    "query_",
    "find_",
    "show_",
    "view_",
    "describe_",
    "count_",
)

#: 名字看起来就是「改」的动词前缀。命中了做成 resource，那就危险了。
WRITE_VERBS = (
    "create_",
    "update_",
    "delete_",
    "remove_",
    "reserve_",
    "cancel_",
    "send_",
    "publish_",
    "pay_",
    "charge_",
    "set_",
)


def classify(entry: dict[str, Any]) -> dict[str, Any]:
    """给一条能力挑刺，返回问题清单和建议。

    输入形如 `{"kind": "tool", "name": "get_leave_doc", "annotations": {...}}`。
    这不是学术分类器，是把「凭直觉容易放错」的几种情况写成规则。
    """
    kind = str(entry.get("kind") or "")
    name = str(entry.get("name") or "")
    ann = entry.get("annotations") or {}
    issues: list[dict[str, str]] = []
    suggestion: str | None = None

    if kind == "tool":
        if ann.get("readOnlyHint") is True and name.startswith(READ_VERBS):
            suggestion = "resource"
            issues.append(
                {
                    "code": "READONLY_TOOL_LOOKS_LIKE_RESOURCE",
                    "message": f"「{name}」自己声明了 readOnlyHint 且名字是读操作 —— 模型不会主动去调一个它不知道存在的只读工具，"
                    "做成 resource 由应用挂载更合适。",
                }
            )
        if ann.get("destructiveHint") is True and not name.startswith(WRITE_VERBS):
            issues.append(
                {
                    "code": "DESTRUCTIVE_NAME_MISLEADING",
                    "message": f"「{name}」声明了 destructiveHint，但名字看不出会改状态 —— 模型判断该不该调时全靠名字和描述。",
                }
            )
        if ann.get("readOnlyHint") is True and ann.get("destructiveHint") is True:
            issues.append(
                {
                    "code": "CONTRADICTORY_ANNOTATIONS",
                    "message": "readOnlyHint 和 destructiveHint 同时为 true，自相矛盾。",
                }
            )
        if not entry.get("description"):
            issues.append({"code": "MISSING_DESCRIPTION", "message": "工具没有 description，模型无从判断何时该用。"})
    elif kind == "resource":
        if name.startswith(WRITE_VERBS):
            suggestion = "tool"
            issues.append(
                {
                    "code": "SIDE_EFFECT_IN_RESOURCE",
                    "message": f"「{name}」名字是写操作。resource 由宿主自动挂载/预取，用户没有「我要执行」的确认动作 —— "
                    "放进去等于绕过确认直接改状态。",
                }
            )
    elif kind == "prompt":
        if name.startswith(WRITE_VERBS) or name.startswith(READ_VERBS):
            issues.append(
                {
                    "code": "PROMPT_LOOKS_LIKE_CAPABILITY",
                    "message": f"「{name}」像个能力名而不是模板名。prompt 是给用户点的模板，名字应该描述产出（如 summarize/review），不是动作。",
                }
            )
    else:
        issues.append({"code": "UNKNOWN_KIND", "message": f"未知能力类型：{kind}"})

    return {
        "kind": kind,
        "name": name,
        "ok": not issues,
        "issues": issues,
        "suggestion": suggestion,
    }


# ── 给页面用的样例帧 ─────────────────────────────────────────────────────────


def examples() -> list[dict[str, Any]]:
    return [
        {
            "id": "tools/list",
            "label": "① tools/list",
            "hint": "拿工具清单。只返回声明了 tools 的 server。",
            "frame": {"jsonrpc": JSONRPC, "id": 1, "method": "tools/list"},
        },
        {
            "id": "tools/call",
            "label": "② tools/call（只读工具）",
            "hint": "annotations.readOnlyHint=true —— 模型可以放心调。",
            "frame": {
                "jsonrpc": JSONRPC,
                "id": 2,
                "method": "tools/call",
                "params": {"name": "search_products", "arguments": {"keyword": "键盘"}},
            },
        },
        {
            "id": "tools/call-write",
            "label": "③ tools/call（有副作用）",
            "hint": "destructiveHint=true —— 真扣库存。这类工具需要人在环里确认。",
            "frame": {
                "jsonrpc": JSONRPC,
                "id": 3,
                "method": "tools/call",
                "params": {"name": "reserve_stock", "arguments": {"sku": "B-200", "qty": 2}},
            },
        },
        {
            "id": "resources/list",
            "label": "④ resources/list",
            "hint": "固定资源清单，不含模板。",
            "frame": {"jsonrpc": JSONRPC, "id": 4, "method": "resources/list"},
        },
        {
            "id": "resources/templates/list",
            "label": "⑤ resources/templates/list",
            "hint": "模板要单独列 —— 客户端得知道有这东西才能让用户填参数。",
            "frame": {"jsonrpc": JSONRPC, "id": 5, "method": "resources/templates/list"},
        },
        {
            "id": "resources/read-direct",
            "label": "⑥ resources/read（固定）",
            "hint": "直接按 URI 读。",
            "frame": {"jsonrpc": JSONRPC, "id": 6, "method": "resources/read", "params": {"uri": "doc://handbook/leave"}},
        },
        {
            "id": "resources/read-template",
            "label": "⑦ resources/read（模板展开）",
            "hint": "同一个 read 方法，URI 命中模板就走模板。",
            "frame": {"jsonrpc": JSONRPC, "id": 7, "method": "resources/read", "params": {"uri": "doc://kb/expense"}},
        },
        {
            "id": "prompts/list",
            "label": "⑧ prompts/list",
            "hint": "模板清单，带 arguments 声明。",
            "frame": {"jsonrpc": JSONRPC, "id": 8, "method": "prompts/list"},
        },
        {
            "id": "prompts/get",
            "label": "⑨ prompts/get",
            "hint": "展开成一个 user 消息。注意返回的是 messages，不是字符串。",
            "frame": {
                "jsonrpc": JSONRPC,
                "id": 9,
                "method": "prompts/get",
                "params": {"name": "review_sql", "arguments": {"sql": "SELECT * FROM orders"}},
            },
        },
        {
            "id": "bad-uri",
            "label": "⑩ 读不存在的资源",
            "hint": "-32602，data 里列出已知 URI 和模板 —— 报错要给出路。",
            "frame": {"jsonrpc": JSONRPC, "id": 10, "method": "resources/read", "params": {"uri": "doc://nope"}},
        },
        {
            "id": "bad-args",
            "label": "⑪ 提示词缺必填参数",
            "hint": "-32602，data.args 里带完整声明，客户端据此弹表单。",
            "frame": {"jsonrpc": JSONRPC, "id": 11, "method": "prompts/get", "params": {"name": "summarize", "arguments": {}}},
        },
    ]


def lint_demo() -> list[dict[str, Any]]:
    """三条故意放错的能力，用来看挑刺器怎么说。"""
    return [
        classify({"kind": "tool", "name": "get_leave_doc", "description": "读取请假制度全文", "annotations": {"readOnlyHint": True}}),
        classify({"kind": "resource", "name": "cancel_order", "description": "取消订单"}),
        classify({"kind": "prompt", "name": "search_products", "description": "搜商品"}),
        classify({"kind": "tool", "name": "reserve_stock", "description": "预留库存", "annotations": {"readOnlyHint": False, "destructiveHint": True}}),
        classify({"kind": "tool", "name": "count_items", "description": "统计在售数量", "annotations": {"readOnlyHint": True, "destructiveHint": True}}),
    ]
