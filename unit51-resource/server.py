"""Unit 51 · Resource —— 把只读数据挂上去（stdio）。

两个固定 resource + 一个模板 resource，**一个 tool 都不挂**。
这不是省事，是本单元要证明的东西：不是所有「给模型的数据」都该做成 tool。

    /home/rik/.cache/aj/runner/bin/python server.py
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

server = MCPServer(
    "unit51-resources",
    version="0.1.0",
    instructions="只提供只读文档：员工手册、变更日志、知识库。没有可执行的动作。",
)

# ---------------------------------------------------------------------------
# 固定 resource 的内容
# ---------------------------------------------------------------------------
HANDBOOK_LEAVE = """# 请假制度

- 年假：入职满 1 年 5 天，满 3 年 10 天，满 5 年 15 天。
- 病假：需提供二级以上医院证明，全年累计不超过 30 天。
- 事假：按小时扣，最小单位 0.5 小时。
- 调休：加班产生的调休当年有效，跨年清零。

提交路径：OA → 人事 → 请假申请，直属主管审批后生效。
"""

CHANGELOG = """Unit 51 变更日志
=================

0.3.0  2026-11-01
  - 新增 doc://kb/{topic} 模板 resource，支持 onboarding / deploy / security
  - 固定 resource 改为两个：doc://handbook/leave、doc://changelog

0.2.0  2026-10-28
  - 请假制度补充调休条款

0.1.0  2026-10-20
  - 首个版本，只挂了员工手册
"""

# 模板 resource 的数据源。key 就是 URI 里的 {topic}。
KB_TOPICS: dict[str, str] = {
    "onboarding": """# 新人上手

1. 领设备，装 VPN，`ssh` 到跳板机验证权限。
2. 读《请假制度》（`doc://handbook/leave`）和《发布流程》。
3. 第一周只做一件事：把本地环境跑起来，并提交一个文档修正 PR。
""",
    "deploy": """# 发布流程

1. 合入 `main` 后 CI 自动构建镜像，tag 为 commit sha。
2. 灰度 5% 流量观察 30 分钟，看错误率与 P99。
3. 全量发布，保留上一个镜像 7 天可回滚。
4. 回滚命令：`deploy rollback --to <sha>`，不需要审批。
""",
    "security": """# 安全基线

- 生产凭证一律走密钥管理服务，禁止写进代码或 `.env` 提交。
- 数据库只读账号用于分析类查询，写账号仅限服务本身。
- 外部文档进入提示词前必须标记为不可信，指令不得执行。
""",
}


def fetch_kb(topic: str) -> tuple[str, str]:
    """按 topic 取文档。返回 (标题, 正文)，未知 topic 抛 KeyError。

    纯函数，方便在自检里直接断言，不用起子进程。
    """
    key = topic.strip().lower()
    if key not in KB_TOPICS:
        raise KeyError(f"未知主题：{topic}")
    title = KB_TOPICS[key].splitlines()[0].lstrip("# ").strip()
    return title, KB_TOPICS[key]


def list_topics() -> list[str]:
    return sorted(KB_TOPICS)


# ---------------------------------------------------------------------------
# resource 定义
# ---------------------------------------------------------------------------
# 固定 URI：函数**不能**有参数。有参数就说明它该做成模板或者 tool。
@server.resource(
    "doc://handbook/leave",
    name="员工手册 · 请假制度",
    description="公司请假政策全文：年假/病假/事假/调休，含提交路径。",
    mime_type="text/markdown",
)
def handbook_leave() -> str:
    return HANDBOOK_LEAVE


@server.resource(
    "doc://changelog",
    name="变更日志",
    description="本单元的版本记录，纯文本。",
    mime_type="text/plain",
)
def changelog() -> str:
    return CHANGELOG


# 模板 URI：{topic} 是 RFC 6570 的占位符，函数参数名必须和它一致。
# 注意 SDK 会把这里注册成「模板」而不是「固定 resource」——两者在 list 里分两个接口返回。
@server.resource(
    "doc://kb/{topic}",
    name="知识库",
    description="按主题取文档，topic ∈ onboarding / deploy / security。",
    mime_type="text/markdown",
)
def kb_doc(topic: str) -> str:
    try:
        return fetch_kb(topic)[1]
    except KeyError as exc:
        # 这里故意 raise：resource 侧没有「返回错误文本」这条通道，
        # 客户端拿到的是脱敏的 MCPError。这正是 resource 不适合承载业务的证据。
        raise ValueError(str(exc)) from exc


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
