"""Unit 69 · AI Security：数据泄露与工具权限。

核心认知：
  1. 防注入（Unit 68）之外，还要防「输出泄露」：模型可能把训练数据、
     上下文里的 PII、别人的数据带出来。所以输出要过一道敏感信息检测/脱敏。
  2. 工具权限 = 最小权限原则：每个工具声明自己需要什么权限，
     调用前先验证当前角色有没有 —— 和 Unit 67 的 RBAC 是一套逻辑，
     只是把「能不能调这个工具」也纳入权限管理。
  3. 危险参数校验：就算有权限，SQL 里 DROP/DELETE 无 WHERE 这类也要拦。

本单元全部规则实现，离线可测；生产要叠加 LLM 裁判做输出审查。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 输出泄露：敏感信息检测与脱敏
# ---------------------------------------------------------------------------
SENSITIVE_PATTERNS = {
    "邮箱": re.compile(r"[A-Za-z0-9._%+-]+@(?!example\.|localhost)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "手机号": re.compile(r"(?<![\d*])1[3-9]\d{9}(?![\d*])"),
    "身份证": re.compile(r"(?<!\d)\d{6}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"),
}


def find_sensitive(text: str) -> list[tuple[str, str]]:
    """返回 [(类型, 命中文本), ...]。example.com 这类示例域名不算。"""
    out = []
    for label, pat in SENSITIVE_PATTERNS.items():
        for m in pat.finditer(text):
            out.append((label, m.group(0)))
    return out


def contains_sensitive(text: str) -> bool:
    return bool(find_sensitive(text))


def redact(text: str) -> str:
    """把敏感信息替换成带类型的掩码。"""
    for label, pat in SENSITIVE_PATTERNS.items():
        text = pat.sub(f"[{label}已脱敏]", text)
    return text


# ---------------------------------------------------------------------------
# 数据访问：按用户隔离
# ---------------------------------------------------------------------------
class DataStore:
    """演示用：user → {key: value}。真实系统是数据库行级权限。"""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = {
            "alice": {"order_1": "手机壳 x1"},
            "bob": {"order_2": "键盘 x1"},
        }

    def get(self, user_id: str, key: str) -> str:
        if key not in self._data.get(user_id, {}):
            # 不告诉调用方「存不存在」，避免存在性探测
            raise PermissionError(f"无权访问或数据不存在（key={key}）")
        return self._data[user_id][key]


# ---------------------------------------------------------------------------
# 工具权限：最小权限表
# ---------------------------------------------------------------------------
TOOL_PERMISSIONS = {
    "lookup_order": "orders:read",
    "refund": "orders:write",
    "query_db": "db:read",
}

ROLE_PERMISSIONS = {
    "admin": {"orders:read", "orders:write", "db:read", "db:write"},
    "support": {"orders:read", "orders:write"},
    "user": {"orders:read"},
}


class ToolPermissionError(Exception):
    """角色没有调用该工具的权限。"""


def check_tool_permission(role: str, tool: str) -> None:
    if tool not in TOOL_PERMISSIONS:
        raise ToolPermissionError(f"未知工具 {tool}")
    required = TOOL_PERMISSIONS[tool]
    if required not in ROLE_PERMISSIONS.get(role, set()):
        raise ToolPermissionError(f"角色 {role} 无权调用 {tool}（需要 {required}）")


# ---------------------------------------------------------------------------
# 危险参数：SQL 安全检查（教学版）
# ---------------------------------------------------------------------------
DANGEROUS_SQL = re.compile(
    r"^\s*(drop|truncate|alter)\b|\bdelete\b(?!.*\bwhere\b)|--|/\*", re.IGNORECASE
)


def validate_sql(sql: str) -> None:
    """只做教学级检查：DROP/TRUNCATE/ALTER、无 WHERE 的 DELETE、注释注入。"""
    if DANGEROUS_SQL.search(sql):
        raise ValueError(f"SQL 被安全策略拒绝：{sql[:80]}")


# ---------------------------------------------------------------------------
# 完整工具执行链
# ---------------------------------------------------------------------------
def execute_tool(role: str, tool: str, args: dict) -> str:
    """工具调用必经：权限检查 → 参数校验 → 执行。"""
    check_tool_permission(role, tool)

    if tool == "lookup_order":
        store = args.get("store")
        return store.get(args.get("user_id", ""), args.get("order_id", ""))
    if tool == "refund":
        return f"已退款：{args.get('order_id')}"
    if tool == "query_db":
        validate_sql(args.get("sql", ""))
        return "查询结果：1 行"
    raise ToolPermissionError(f"未实现的工具 {tool}")


# ---------------------------------------------------------------------------
# 独立自检
# ---------------------------------------------------------------------------
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

    print("Unit 69 · 数据泄露与工具权限 自检\n")

    # ---- 敏感信息检测 ----
    check("检测真实邮箱", any(t == "邮箱" for t, _ in find_sensitive("联系 a@corp.test")))  # pii-check: allow
    check("example.com 不算", not any(t == "邮箱" for t, _ in find_sensitive("联系 a@example.com")))
    check("检测手机号", any(t == "手机号" for t, _ in find_sensitive("手机 13812345678")))  # pii-check: allow
    check("检测身份证", any(t == "身份证" for t, _ in find_sensitive("320101199001011234")))  # pii-check: allow
    check("正常文本无敏感", not contains_sensitive("退款 3-5 个工作日到账"))

    # ---- 脱敏 ----
    masked = redact("邮箱 a@corp.test，手机 13812345678")  # pii-check: allow
    check("邮箱被脱敏", "[邮箱已脱敏]" in masked)
    check("手机号被脱敏", "[手机号已脱敏]" in masked)
    check("脱敏后无原值", "a@corp.test" not in masked and "13812345678" not in masked)  # pii-check: allow

    # ---- 输出过滤：模拟 LLM 答案泄露 ----
    llm_answer = "用户 alice 的邮箱是 alice@corp.test，手机 13912345678"  # pii-check: allow
    check("LLM 答案被判定含敏感", contains_sensitive(llm_answer))
    safe_answer = redact(llm_answer)
    check("输出过滤后可安全返回", not contains_sensitive(safe_answer))

    # ---- 数据访问隔离 ----
    store = DataStore()
    check("alice 能读自己的订单", store.get("alice", "order_1") == "手机壳 x1")
    try:
        store.get("alice", "order_2")
        check("alice 读不到 bob 的订单", False)
    except PermissionError:
        check("alice 读不到 bob 的订单", True)
    try:
        store.get("eve", "order_1")
        check("未知用户被拒", False)
    except PermissionError:
        check("未知用户被拒", True)

    # ---- 工具权限 ----
    check("user 可查订单", check_tool_permission("user", "lookup_order") is None)
    try:
        check_tool_permission("user", "refund")
        check("user 不可退款", False)
    except ToolPermissionError:
        check("user 不可退款", True)
    check("support 可退款", check_tool_permission("support", "refund") is None)
    try:
        check_tool_permission("support", "query_db")
        check("support 不可查库", False)
    except ToolPermissionError:
        check("support 不可查库", True)
    check("admin 可查库", check_tool_permission("admin", "query_db") is None)

    # ---- 工具执行链 ----
    r = execute_tool("user", "lookup_order", {"store": store, "user_id": "alice", "order_id": "order_1"})
    check("执行链正常", r == "手机壳 x1")
    try:
        execute_tool("user", "refund", {"order_id": "order_1"})
        check("执行链拦越权", False)
    except ToolPermissionError:
        check("执行链拦越权", True)

    # ---- 危险 SQL ----
    for bad in ("DROP TABLE users", "DELETE FROM orders", "SELECT * FROM x --", "/* hack */ SELECT 1"):
        try:
            validate_sql(bad)
            check(f"拦截危险 SQL: {bad}", False)
        except ValueError:
            check(f"拦截危险 SQL: {bad}", True)
    check("正常 SELECT 放行", validate_sql("SELECT * FROM orders WHERE id = 1") is None)
    check("带 WHERE 的 DELETE 放行", validate_sql("DELETE FROM orders WHERE id = 1") is None)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
