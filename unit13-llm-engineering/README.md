# Unit 13 · LLM 工程化：超时 / 重试 / 限流 / 成本

把裸的 `client.chat.completions.create()` 换成有纪律的封装。核心不是"能调通"，
而是"**调不通的时候系统还活着**"。

## 快速开始

```bash
uv sync
cp .env.example .env          # 填真实 key 才能跑 /chat
uv run uvicorn main:app --reload --port 8000
```

## 自检（全离线，不花钱）

```bash
uv run python main.py
```

18 项。用 `httpx.MockTransport` 伪造上游，模拟 429 / 400 / 500 / 超时四种故障，
验证重试次数、退避、成本计算、令牌桶、以及"没配 key 时返回 503 而不是 500"。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 探活 + 当前超时/重试/限流配置 |
| POST | `/chat` | 非流式，带重试。返回内容 + `attempts/retries/tokens/cost` |
| POST | `/chat/stream` | SSE 流式，重试时推 `retry` 事件 |
| GET | `/stats` | 累计账本：调用数、重试数、token、花费（元） |

## 模块结构

```
llm_client.py            ← 核心，可直接拷到别的项目
├── 异常体系              LLMError → 6 个子类，每个带 retryable 标记
├── normalize_error()     SDK 异常 → 自己的类型（唯一需要改的地方）
├── backoff_delay()       指数退避 + full jitter
├── TokenBucket           令牌桶
├── PerUserRateLimiter    每用户一个桶
├── CallStats / CostLedger 单次账本 / 进程账本
└── LLMClient
    ├── chat()            非流式，循环重试
    └── chat_stream()     流式，首 token 后不再重试

main.py                  ← FastAPI 薄壳 + 自检
```

## 五个关键决策

1. **重试标准是"再发一次有没有可能成功"**，不是错误码本身。
   400/401 快速失败，429/5xx/超时/连接错才重试。

2. **`max_retries=0` 关掉 SDK 自带重试。**
   否则实际请求数 = 外层次数 × SDK 次数，一次调用能打出 9 个请求。

3. **退避必须加抖动。** `random.uniform(0, base * 2**n)`。
   不加抖动，被限流的一批客户端会永远同步重试，限流解不开。

4. **流式首个 token 之后不再重试。** 重发会让用户看到重复的半句话。

5. **错误码分层次。** 上游 401 给前端 502、超时给 504、重试耗尽给 503，
   只有真正的代码 bug 才该出现 500。

## 验收清单

- [ ] `uv run python main.py` → 18 通过 / 0 失败
- [ ] `curl localhost:8000/health` 看到 `max_retries: 3`
- [ ] 把 `.env` 里的 key 改错，`/chat` 返回 **503**（不是 500）
- [ ] 把 `RATE_LIMIT_PER_MINUTE=2`，连点 3 次得到 **429 + `Retry-After`**
- [ ] 调一次真实对话后 `/stats` 里 `total_cost_cny` 有值
- [ ] 看 `/docs` 里 `/chat` 的响应，`attempts` 和 `retries` 字段能对上

## 下一单元

Unit 14 把 11/12/13 拼成一个能演示、能写进简历的完整项目：`project1-ai-chat`。
