# Unit 59 笔记 · MCP 工程化

> 工程化不是炫技，是「出了事能查、能限损、能并发、能分类」。

## 本次做了什么
- 写 `ops.py`：Logger（白名单 + 敏感打码）、call_with_timeout、concurrent_calls、error_code_for、public_response。
- 写 `main.py`：4 接口 + 42 自检，0 失败，连跑两次一致。

## 一、日志白名单
只保留 {ts, level, event, tool, duration_ms, ok, trace_id, rounds, error_code}。
其他字段（token、password、sql 原文）一律剔除。这样日志可以安全导出到 ELK / Loki，
不用担心敏感信息泄漏。

## 二、超时不崩进程
`asyncio.wait_for(func(), timeout=0.2)` 捕获 TimeoutError → 转 is_error + E_TOOL_TIMEOUT。
关键：超时是**可恢复的**，不该让整个 Agent loop 崩。进程崩了，并发里的其他调用也全死。

## 三、并发 trace_id 不串
每个并发调用生成独立 uuid 前 8 位。日志里按 trace_id 过滤就能还原一次调用的完整链路。
如果不隔离，5 个并发的日志混在一起，排查时根本分不清哪条属于哪个调用。

## 四、错误码稳定
同一种失败总是同一个码：syntax error → E_SQL_SYNTAX，多语句 → E_SQL_FORBIDDEN。
这样客户端可以做「按错误码决定重试策略」，不用解析错误文本。

## 一句话总结
**工程化的底线是：出了事能查、能限损、能分类。**
