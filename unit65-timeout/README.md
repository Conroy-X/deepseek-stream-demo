# Unit 65 · Timeout：超时分层

> 目标：**等不到就停**——LLM、工具、整体请求各自独立超时，超时说得清「超的是哪一层」。

---

## 一、为什么超时要分层

一次问答请求里有多个「等待点」：

```
客户端请求
  └─ LLM 调用（可能 30 秒）
  └─ 工具调用（可能 5 秒）
```

如果只设一个整体超时，会出现：

- LLM 卡住时，整个请求要等到「整体超时」才停，期间用户一直转圈
- 工具很快但 LLM 很慢时，不知道该在哪一层给用户反馈

所以三层各自设限：

| 层 | 典型值 | 超时后 |
| --- | --- | --- |
| LLM 调用 | 30–120s | 降级（换模型 / 返回部分结果 / 报错） |
| 工具调用 | 3–10s | 跳过该工具，继续流程 |
| 整体请求 | 客户端耐心上限 | 返回 504 |

## 二、核心实现

```python
async def run_with_timeout(coro, timeout, what):
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise TimeoutExceeded(what, timeout) from exc
```

`TimeoutExceeded` 带两个字段：`what`（超的是哪层）、`limit`（限制多少秒）。
**超时错误必须说得清上下文**，否则排障时只知道「超时了」，不知道超在哪。

## 三、怎么跑

```bash
cd ~/ai-journey/unit65-timeout
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8065
curl "http://127.0.0.1:8065/ask?llm_latency=3&llm_timeout=1"   # LLM 超时 → 504
curl "http://127.0.0.1:8065/ask?tool_latency=3&tool_timeout=1" # 工具超时 → 504
curl "http://127.0.0.1:8065/ask"                               # 正常
```

## 四、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] LLM 延迟 > LLM 超时 → 504，detail 说明「LLM 调用超过 Xs」
- [ ] 工具延迟 > 工具超时 → 504，detail 说明「工具调用超过 Xs」
- [ ] 整体超时即使单层没超也会触发
- [ ] 能解释「超时为什么是 504 而不是 500」
- [ ] 能说「设超时的依据是什么」

## 五、面试追问

**Q: 超时和重试什么关系？**
→ 超时是可重试错误的一种（Unit 64 的 RetryableError 要涵盖它）。
但要注意：超时后上游可能还在执行——重试前要确认操作幂等。

**Q: 504 和 500 的区别？**
→ 504 是「网关/上游超时」，明确告诉客户端「是我等上游等超了」；
500 是「服务器内部错误」。超时用 504 语义更准确。

**Q: 超时设多少合适？**
→ 依据 P95 延迟 + 余量，不是拍脑袋。观察真实请求的延迟分布，
P95 是 3 秒就设 5 秒；设太短会误杀慢但正常的请求。

**Q: 超时后怎么降级？**
→ LLM 超时：换更快的小模型 / 返回缓存 / 返回「回答生成超时请重试」；
工具超时：跳过该工具，用其余结果继续。

---

## 六、资料

- [asyncio.wait_for](https://docs.python.org/3/library/asyncio-task.html#asyncio.wait_for)
- [httpx 超时配置](https://www.python-httpx.org/advanced/timeouts/)
- [MDN：504 Gateway Timeout](https://developer.mozilla.org/zh-CN/docs/Web/HTTP/Status/504)
