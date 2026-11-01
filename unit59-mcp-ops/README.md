# Unit 59 · MCP 工程化

> 日志（结构化 + 白名单 + 敏感打码）、超时（asyncio.wait_for）、并发（trace_id 不串）、错误码（E_ 前缀 + 对外不含 traceback）。

**完成标准**：能说清日志字段白名单为什么要做；能解释超时为什么转 is_error 而不是崩进程；
能说清错误码为什么必须稳定（同一种失败总是同一个码）。

## 快速开始
```bash
cd ~/ai-journey/unit59-mcp-ops
uv sync
uv run uvicorn main:app --port 8059
```

## 自检
```bash
uv run python main.py
# 结果：42 通过 / 0 失败
```

## 这个目录里有什么
| 文件 | 作用 |
| --- | --- |
| `ops.py` | Logger / call_with_timeout / concurrent_calls / error_code_for / public_response |
| `main.py` | 4 接口 + 42 自检 |
| `static/index.html` | 日志面板 + 并发面板 + 错误码表 |

## 实测
| 项目 | 结果 |
| --- | --- |
| 日志白名单 | 非白名单字段（token/password/sql）被剔除 |
| 敏感打码 | token=Bearer xxx → ***REDACTED*** |
| 慢工具超时 | 0.2s 超时被捕获，不崩，错误码 E_TOOL_TIMEOUT |
| 5 并发 | 5 个 trace_id 各不相同，不串号 |
| 错误码 | 5 种稳定：E_SQL_SYNTAX / E_SQL_FORBIDDEN / E_TOOL_TIMEOUT / E_TOOL_ERROR / E_UPSTREAM |
| 对外响应 | 只有 error_code + message，不含 traceback |

## 下一单元
Unit 60 · 项目 4 交付 —— 把 55-59 的能力串起来 + ARCHITECTURE.md + SECURITY.md + DEMO.md。
