# Unit 89 · 面试：AI 工程化（30 题）

> 目标：**30 道工程化高频题讲得清。** 计划要求重点考成本、延迟、评测、安全。

---

## 一、题库结构

| 板块 | 题号 | 覆盖 |
| --- | --- | --- |
| 可靠性 | 1–7 | 重试、超时、熔断、限流、幂等、降级、主备切换 |
| 可观测 | 8–12 | 结构化日志、指标、链路追踪、成本面板、告警 |
| 性能与成本 | 13–18 | 缓存、批处理、流式、量化、KV Cache、自建 vs API |
| 评测 | 19–23 | 防退化、线上评测、A/B、指标选择、badcase 归因 |
| 安全 | 24–28 | 注入、PII、密钥、多租户、审计 |
| 交付 | 29–30 | Docker、部署扩缩容 |

## 二、这张表要背下来

| 问题 | 一句话答案 |
| --- | --- |
| 重试 | 只重试可重试（429/5xx/超时），指数退避 + 抖动，双上限 |
| 超时 | 分层（LLM/工具/整体），504 表示上游超时 |
| 熔断 | 上游持续失败就快速失败，防雪崩，半开探测恢复 |
| 限流 | 令牌桶允许突发，429 + Retry-After |
| 缓存 | key 覆盖所有影响输出的参数，看命中率 |
| 评测 | 金标准 + 离线可复现 + 指标可解释 |

## 三、怎么跑

```bash
cd ~/ai-journey/unit89-interview-eng
uv sync
uv run python main.py

uv run uvicorn main:app --port 8089
curl "http://127.0.0.1:8089/bank"                # 30 题
curl "http://127.0.0.1:8089/bank?topic=eval"     # 只看评测
```

## 四、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 上面那张「一句话答案」表能默写
- [ ] 能说清一次 LLM 请求的完整可观测链路（request_id → span → 指标）
- [ ] 弱项回去补（Unit 61–70）

## 五、面试追问

**Q: 「线上模型突然变慢，你怎么定位？」**
→ 看指标分层：请求总量 → 各接口 P95 → LLM 调用耗时 → 上游状态码。
用 request_id 捞具体慢请求的完整链路。

**Q: 「怎么控成本？」**
→ 四板斧：缓存（不重复调）、小模型兜底、限长（max_tokens）、
预算告警（超阈值告警而非等月底）。

---

## 六、资料

- [Unit 61–70 工程化单元](../unit61-docker/)
- [OWASP LLM Top 10](https://owasp.org/www-project-top-10-for-large-language-model-applications/)
- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/)