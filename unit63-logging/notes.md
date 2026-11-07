# Unit 63 笔记 · 结构化日志

> 边做边填。模拟面试的复习材料。

## 本次做了什么

- 手写 JSON 行日志器（零依赖）：每行一条 JSON，字段稳定
- request_id 用 contextvar 贯穿「请求 → LLM 调用 → 工具调用」
- FastAPI 中间件生成 request_id 并回传响应头
- 自检 22 项：字段断言 + 跨请求隔离 + 可 grep

## 核心认知

**日志不是给人肉翻的，是给 grep / 聚合 / 告警用的。**
所以必须是「每行一条 JSON + 固定字段」，而不是多行 traceback。

## 三条设计决定

1. JSON 每行一条 —— 可 grep、可 jq 聚合
2. request_id 用 contextvar —— 业务代码不感知，自动带上
3. 字段固定 —— LLM 记 model/token/耗时，工具记 tool/args/ok/耗时

## 我自己的话解释「为什么用 contextvar 而不是函数传参」

（待填：提示——函数传参要一路穿过所有调用层；contextvar 让深层日志函数直接拿到当前请求 id）

## 我自己的话解释「为什么不用 print」

（待填：提示——print 无级别、无结构、并发乱序、不能定向落盘）

## 完成标准对照

- [ ] `uv run python main.py` → 22 通过 / 0 失败
- [ ] /ask 响应头有 X-Request-Id
- [ ] 同一 request_id 贯穿四条日志
- [ ] 能说 llm_call 至少要记哪几个字段

## 一句话总结

（待填）

## 面试追问

1. **「日志里至少要有什么？」** → ts/level/event/request_id + 领域字段
2. **「怎么串起一个请求的所有日志？」** → request_id 贯穿 + 响应头回传
3. **「生产日志怎么收集？」** → structlog + 采集器，JSON 行格式天生适合解析
