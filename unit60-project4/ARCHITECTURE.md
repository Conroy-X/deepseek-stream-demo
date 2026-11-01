# 架构

## 链路

```
用户问题
    ↓
[1] schema 注入     — 从 DB 拉表结构，拼进 prompt
    ↓
[2] NL→SQL          — 离线规则模型把问题转成 SQL
    ↓
[3] 四道安全闸      — 只读连接 + 词法白名单 + 强制 LIMIT + 行级权限
    ↓
[4] 错误重试        — SQL 报错归一化 + 改写，最多 3 次
    ↓
[5] 结构化日志      — 每一步记 trace_id + duration_ms + error_code
    ↓
最终答案
```

## 为什么 Agent 不放服务端

Agent 持有对话历史和模型上下文 —— 这些是**会话级**状态。
MCP server 是**无状态**工具进程，每次调用独立。
把 Agent 放服务端意味着 server 要维护会话状态，违反了 MCP 的设计原则：
- server 可以被多个 Host 同时连
- server 不该知道「当前用户是谁」（那是 Host 的事）
- server 挂了不影响 Host 的其他 server

所以 Agent 在 Host 端，MCP server 只管「执行这一个工具」。

## 各模块来源

| 链路 | 单元 |
| --- | --- |
| schema 注入 | Unit 56 |
| NL→SQL | Unit 56 |
| 四道闸 | Unit 52 + Unit 57 |
| 错误重试 | Unit 58 |
| 结构化日志 | Unit 59 |
| MCP 协议 | Unit 46-49 |
| Agent Loop | Unit 53-54 |