# Unit 64 · Retry：只重试「可重试」的错误

> 目标：**失败会自动重试，但不该重试的绝不浪费次数；重试节奏指数退避 + 抖动。**

---

## 一、三条铁律

### 1. 只重试可重试错误

| 错误 | 语义 | 该重试吗 |
| --- | --- | --- |
| 429 / 5xx / 网络超时 | 瞬时故障，过一会儿可能好 | ✅ 重试 |
| 4xx（参数错 / 认证失败 / 余额不足） | 重试一万次结果一样 | ❌ 直接抛 |

本单元用两类异常表达：`RetryableError`（重试）vs `PermanentError`（不重试）。

### 2. 指数退避 + 抖动

```
第 1 次重试前等 base
第 2 次重试前等 base × factor
第 3 次重试前等 base × factor²
```

再叠加随机抖动。**抖动的作用**：几十个客户端同时失败同时重试，会把服务打死；
抖动把重试错开，避免惊群。

### 3. 双上限

- `max_attempts`：最多试几次
- `max_time`：总时长封顶

哪个先到都停——防止「指数退避」真的退避到天荒地老。

## 二、怎么跑

```bash
cd ~/ai-journey/unit64-retry
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8064
curl "http://127.0.0.1:8064/ask?q=hello"   # 前两次 503 自动重试，第三次成功
curl "http://127.0.0.1:8064/ask?q=bad"     # 4xx 语义，不重试直接 400
```

## 三、文件说明

| 文件 | 作用 |
| --- | --- |
| `retry.py` | 重试器：错误分类 + 退避公式 + 双上限 + on_retry 回调 + 自检 |
| `main.py` | /ask 接假不稳定模型，演示重试与不重试两条路径 |

## 四、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] `/ask?q=hello` 自动重试两次后成功，返回 retries=2
- [ ] `/ask?q=bad` 不重试直接 400
- [ ] 能解释「4xx 为什么不重试」
- [ ] 能解释「抖动是干什么的」
- [ ] 能说出双上限分别管什么

## 五、面试追问

**Q: 幂等和重试什么关系？**
→ 重试的前提是操作幂等（或至少安全）。扣款/下单这类非幂等操作，
重试前必须做幂等键，否则重试两次 = 扣两次款。

**Q: 429 的 Retry-After 响应头？**
→ 服务端明确说「多久后再来」，重试间隔应取 `Retry-After` 和退避公式的较大值，
别跟服务端对着干。

**Q: 重试会不会放大故障？**
→ 会。上游已经过载时，大量客户端立即重试 = 雪崩。所以要有抖动、有上限、
有熔断（Unit 65/66 会讲超时和限流，配合使用）。

---

## 六、资料

- [tenacity（生产级重试库）](https://tenacity.readthedocs.io/en/latest/)
- [指数退避与抖动（AWS 官方）](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)
- [MDN：Retry-After](https://developer.mozilla.org/zh-CN/docs/Web/HTTP/Headers/Retry-After)
