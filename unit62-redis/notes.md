# Unit 62 笔记 · Redis 缓存与会话

> 边做边填。模拟面试的复习材料。

## 本次做了什么

- 缓存层抽象：MemoryCache（离线）+ RedisCache（真机），统一 get/set/delete/clear/stats
- LLM 响应缓存：假模型 + 计数演示「三次同问只调一次模型」
- 会话存储：Session 序列化进缓存带 TTL

## 核心认知

**LLM 调用又贵又慢，能缓存就缓存。** 缓存的价值由 hit_rate 衡量：
`hits / (hits + misses)`，不看存了多少。

## 缓存 key 的三条规则

1. 覆盖影响输出的全部参数（model / messages / temperature）
2. messages 顺序敏感，不能排序
3. 用哈希压缩长度；绝不能把时间戳/随机数算进去

## 降级原则

`get_cache("auto")`：优先 Redis，连不上退回 Memory。
**缓存不可用时服务还要能跑**——可用性优先于省钱。

## 我自己的话解释「缓存 key 为什么要把 temperature 算进去」

（待填：提示——temperature=0 和 1.5 的输出不同，不算进去就会把不同答案缓存成同一个）

## 我自己的话解释「缓存挂了为什么服务不该跟着挂」

（待填：提示——缓存是优化，不是功能；优化失效应该退化回无缓存，而不是中断）

## 完成标准对照

- [ ] `uv run python main.py` 自检通过
- [ ] `/ask` 连打两次，第二次 hit=true 且 model_calls 不变
- [ ] 能解释 key 设计和降级原则
- [ ] 能说 hit_rate 低意味着什么

## 一句话总结

（待填）

## 面试追问

1. **「缓存 key 怎么设计？」** → 覆盖影响输出的参数 + 顺序敏感 + 哈希压缩 + 不掺时间戳
2. **「Redis 挂了怎么办？」** → 降级直连模型，可用性优先
3. **「为什么用 Redis 不用 dict？」** → 多实例共享 / 重启不丢 / 自带 TTL
