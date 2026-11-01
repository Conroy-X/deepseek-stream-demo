# Unit 60 · 项目 4 交付：MCP Database Agent

> 五条链拧成一条：schema 注入 → NL→SQL → 四道安全闸 → 错误重试 → 结构化日志。
> 这是阶段四（MCP）的收官单元，交付物能直接贴进简历。

**完成标准**：端到端一条正常链路跑通；4 条攻击全被拦；错误自愈成功；日志有完整 trace。

## 快速开始
```bash
cd ~/ai-journey/unit60-project4
uv sync
uv run uvicorn main:app --port 8060
```

## 自检
```bash
uv run python main.py
# 结果：31 通过 / 0 失败
```

## 这个目录里有什么
| 文件 | 作用 |
| --- | --- |
| `main.py` | 端到端整合 + 31 自检 |
| `static/index.html` | 项目主页 |
| `ARCHITECTURE.md` | 架构图 + 链路 + 为什么 Agent 不放服务端 |
| `SECURITY.md` | 威胁模型表（8 条威胁 + 防线 + 实测） |
| `DEMO.md` | 5 个演示脚本 |

## 这个项目能写进简历的三句话
1. 基于 MCP 协议构建了多租户 NL→SQL Agent，支持自然语言查询 + 只读 SQL 执行。
2. 实现四道安全闸（只读连接 + 词法白名单 + 强制 LIMIT + 行级权限），拦截 8 类 OWASP SQL 注入攻击。
3. 错误自愈机制（SQL 报错归一化 + 自动改写，最多 3 次）+ 结构化日志（trace_id 全链路追踪）。

## 已知局限
- SQLite 没有 PostgreSQL RLS，行级权限靠 SQL 改写
- fix_sql 是简化版（前缀匹配），生产用编辑距离
- NL→SQL 是规则模型，生产用 LLM

## 下一单元
阶段五（Unit 61-75）：AI 工程化 + LLM 原理 —— 容器化 / 观测 / 评测 / 面试原理题。
