# Unit 62 · Redis：缓存与会话

> 目标：**相同的问题，模型只被调一次**。缓存层双后端（Memory 离线 / Redis 真机），
> 上层代码不感知差异。

---

## 一、为什么需要缓存

LLM 调用又贵又慢。用户问了两遍一模一样的话，理想情况是第二遍直接返回第一次的答案：

```
第 1 次问「什么是缓存」 → 调模型（花 1 次钱） → 存缓存
第 2 次问「什么是缓存」 → 缓存命中 → 直接返回（不花钱）
```

本单元用「假模型 + 计数」演示这个闭环：三次同问，模型只被调 1 次。

## 二、怎么跑

### 离线自检（Redis 未起也能跑）

```bash
cd ~/ai-journey/unit62-redis
uv sync
uv run python main.py
```

Memory 后端全部断言离线通过；Redis 真机部分可选（本机 6379 已装 Redis 8，
连得上就多验 3 项，连不上算 SKIP 不算失败）。

### 真机体验（需要本机 Redis 已起）

```bash
uv run uvicorn main:app --port 8062

curl "http://127.0.0.1:8062/ask?q=什么是缓存"   # hit=false，model_calls=1
curl "http://127.0.0.1:8062/ask?q=什么是缓存"   # hit=true，model_calls 还是 1
curl "http://127.0.0.1:8062/cache/stats"         # 看 hit_rate
```

## 三、核心设计

### 1. 缓存 key 必须覆盖影响输出的全部参数

```python
key = sha256(model + messages + temperature)
```

- messages **顺序敏感**，不能排序（顺序变了语义就变）
- key 用哈希而不是原样 prompt：Redis key 太长拖慢内存

### 2. 双后端抽象

| 后端 | 用途 | 何时用 |
| --- | --- | --- |
| `MemoryCache` | 进程内 dict，带 TTL | 自检 / 单进程小工具 |
| `RedisCache` | 真机 Redis，SETEX 带 TTL | 多进程 / 服务重启不丢 / 分布式 |

`get_cache("auto")`：优先 Redis，连不上自动退回 Memory——**缓存不可用时服务还能跑**，
只是不省钱了。这是降级，不是失败。

### 3. 命中率是唯一指标

`hits / (hits + misses)`。缓存有价值与否，不看「存了多少」，看「命中率多少」。
命中率低 = key 设计有问题（比如把时间戳、随机数算进了 key）。

## 四、文件说明

| 文件 | 作用 |
| --- | --- |
| `cache.py` | 缓存抽象 + 双后端 + 会话存取 + 独立自检（26 项） |
| `main.py` | FastAPI：/ask（假模型+缓存）/stats/clear |
| `requirements.txt` / `pyproject.toml` | 依赖（redis 包） |

## 五、完成标准

- [ ] `uv run python main.py` 自检通过（Redis 项 SKIP 也算过）
- [ ] `/ask` 连打两次同问，第二次 `hit=true` 且 `model_calls` 不变
- [ ] 能解释「缓存 key 为什么要把 temperature 也算进去」
- [ ] 能解释「缓存挂了为什么服务不该跟着挂」
- [ ] 能说出 hit_rate 怎么算、低命中率意味着什么

## 六、面试追问

**Q: 缓存 key 怎么设计？**
→ 覆盖影响输出的全部参数（model/messages/temperature/tools），顺序敏感的字段不能排序，
用哈希压缩长度；绝不能把时间戳/随机数算进去（否则永不命中）。

**Q: 缓存过期策略？**
→ TTL 简单可靠；语义缓存更高级（近似匹配也算命中，Unit 76+ 才会碰）。
LLM 场景还要考虑：模型版本升级后旧缓存要失效（key 里带模型版本）。

**Q: Redis 挂了怎么办？**
→ 降级：缓存不可用就直连模型，服务可用性优先于省钱。绝不能因为缓存崩了把问答接口带崩。

**Q: 为什么用 Redis 不用进程内 dict？**
→ 多进程/多实例共享、服务重启不丢、自带 TTL 和淘汰；单进程小工具用 dict 足够。

---

## 七、资料

- [Redis 文档](https://redis.io/docs/latest/)
- [redis-py](https://redis-py.readthedocs.io/en/stable/)
- [Redis 缓存模式](https://redis.io/docs/latest/develop/use/patterns/)
- [Cache-Aside 模式](https://learn.microsoft.com/azure/architecture/patterns/cache-aside)
