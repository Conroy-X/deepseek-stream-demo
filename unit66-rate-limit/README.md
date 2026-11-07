# Unit 66 · Rate Limit：限流

> 目标：**保护上游、公平分配**——超限返回 429 + Retry-After，让客户端正确退避。

---

## 一、三种算法

| 算法 | 特点 | 边界漏洞 | 适用 |
| --- | --- | --- | --- |
| **令牌桶** | 允许突发（桶容量），持续速率由补充速率决定 | 无 | **最常用** |
| **滑动窗口** | 精确统计过去 N 秒请求数 | 无 | 需要精确控制 |
| **固定窗口** | 实现最简单 | **有**：窗口交界瞬间可放行 2×max | 粗粒度可接受时 |

### 固定窗口的边界漏洞（本单元的重点演示）

```python
fw = FixedWindow(window_sec=10, max_requests=2)
# 窗口 N 末尾放 2 次
fw.allow(); fw.allow()
# 时间刚过边界，进入窗口 N+1 —— 计数器归零
# 瞬间又放 2 次
fw.allow(); fw.allow()   # 10 秒内实际放了 4 次，超了 2 倍
```

这就是为什么生产限流一般用令牌桶或滑动窗口，而不是固定窗口。

## 二、429 + Retry-After

限流响应必须带两样东西：

1. **429 Too Many Requests** —— 明确语义
2. **`Retry-After` 响应头** —— 告诉客户端多久后再来

客户端（Unit 64 的 Retry）看到 429 应该退避重试，而不是立刻猛打。

## 三、怎么跑

```bash
cd ~/ai-journey/unit66-rate-limit
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8066
# 快速连打 6 次，第 6 次起 429
for i in $(seq 1 6); do curl -sS -o /dev/null -w "%{http_code} " "http://127.0.0.1:8066/ask"; done; echo
```

## 四、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 连打超过容量后返回 429，响应头有 Retry-After
- [ ] 能解释三种算法的区别与适用场景
- [ ] 能演示固定窗口的边界漏洞
- [ ] 能说限流和重试怎么配合

## 五、面试追问

**Q: 令牌桶和漏桶的区别？**
→ 漏桶是固定速率输出、队列缓冲；令牌桶允许突发。
限流场景令牌桶更常用，因为「允许一定突发」对用户体验更好。

**Q: 限流粒度怎么定？**
→ 全局（保护上游）、每用户（公平）、每 API key（商业计费）。
一般多层叠加：全局兜底 + 每用户限额。

**Q: 分布式限流怎么做？**
→ 内存版只在单实例有效；多实例用 Redis（INCR + EXPIRE 做固定窗口，
或 Lua 脚本做令牌桶/滑动窗口）。

**Q: 限流 429 客户端该怎么办？**
→ 读 Retry-After，退避重试（Unit 64）；连续 429 说明配额真不够，
应该报错而不是无限重试。

---

## 六、资料

- [令牌桶维基](https://en.wikipedia.org/wiki/Token_bucket)
- [MDN：429](https://developer.mozilla.org/zh-CN/docs/Web/HTTP/Status/429)
- [Redis 限流模式](https://redis.io/docs/latest/develop/use/patterns/)
- [slowapi（FastAPI 限流）](https://slowapi.readthedocs.io/en/latest/)
