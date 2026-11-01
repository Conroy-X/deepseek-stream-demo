# Unit 54 笔记 · 多 MCP Server

> 单 server 写多了就会发现：把所有工具塞一个进程里既不分工也不可扩展。
> 多 server 第一天就撞上「工具名冲突」 —— 这是本单元存在的理由。

## 本次做了什么

- 写 3 个独立 server：`servers/math_server.py` / `text_server.py` / `db_server.py`。
- 写 `multi_client.py`：`MultiMCPClient` 同时连 3 个 stdio 子进程，工具名加前缀。
- 写 `main.py`：5 接口 + 42 自检，0 失败，连跑两次一致。

## 一、命名空间前缀

最简单也最有效的隔离：

```
math.add       ← math server 的 add
text.upper     ← text server 的 upper
db.add         ← db server 的 add（名字和 math 冲突）
db.query       ← db server 的 query
```

模型看到 `db.add` 就知道这是 db 的，不会调到 math 的 add。
前缀不仅是名字隔离，**也是状态隔离**：db 的 `add` 真的改了 db 进程里的库存，
math 的 `add` 是纯函数。两者互不干扰。

## 二、状态串台的实测

```
db.add(A-100, 5)   → 17   # 第一次加
db.add(A-100, 5)   → 22   # 第二次再加
math.add(2, 3)     → 5    # 不受 db 状态影响
```

每个 server 子进程有自己的内存。`MultiMCPClient` 维护 `{namespace: session}` 映射，
调用时按前缀路由到对应 session。前缀错了就调错 server，但永远不会串状态。

## 三、部分失败降级

故意让 broken server 路径不存在：

```python
async with MultiMCPClient([..., ("broken", "no_such_server.py")]) as c:
    ...
```

`__aenter__` 里每个 server 独立 try/except：

```python
try:
    r, w = await stdio_client(params)
    ...
except Exception as e:
    status.error = f"{type(e).__name__}: {e}"
```

broken 标记为未连接、错误被记下，但 math/text/db 三个仍连得上、仍能调。

这就是「部分失败降级」—— 一个 server 挂了，Agent 还能用其他 server 的工具继续工作。
如果一挂全挂，那就退化成单点故障。

## 四、子进程清理

每个 `stdio_client` 起一个子进程。`AsyncExitStack` 在 `__aexit__` 时按相反顺序关闭：
先关 ClientSession、再关 stdio_client（杀子进程）。

如果不收尾，每次自检多 3 个僵尸进程。本单元跑了多次自检后查 `ps` 没有残留 —— 证明清理有效。

## 五、关键词路由

最简单的路由：按问题里的关键词匹配 namespace。

```python
if "加" in question: return "math"
if "大写" in question: return "text"
if "库存" in question: return "db"
```

生产里会用 embedding 相似度，但本单元演示用关键词就够。
关键不是「路由算法多聪明」，是「路由层独立于工具调用层」—— 路由决定 ns，调用走 ns 对应 session。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 解释多 server 前缀 | 6 个工具全带前缀，冲突挑出 |
| 部分失败降级 | broken 失败，其他 3 个仍连，math.add 仍可调 |
| 子进程无残留 | AsyncExitStack 关闭，多次自检后 ps 无残留 |
| 关键词路由 | 4 类问题正确路由 |
| 离线可自检 | 42 条断言，0 失败，连跑两次一致 |

## 一句话总结

**多 server 的复杂度不在连接，在隔离 —— 名字、状态、失败，三者都要分开。**

## 面试追问

**Q：为什么不用一个 server 把所有工具都塞进去？**
A：三个理由。一是**职责分离**：math 工具和 db 工具不应该耦合，前者纯函数、后者有状态。
二是**故障隔离**：一个 server 挂了不影响其他。三是**部署灵活**：db server 可以单独部署到内网，
math server 可以无状态水平扩展。塞一起就失去这些。

**Q：前缀会不会让模型困惑？**
A：不会。模型读 `math.add` 时会把 `math` 当 namespace 解读，不会以为是工具名的一部分。
真实 OpenAI function calling 也支持带点的工具名。前缀反而帮模型做决策 ——
看到 `db.add` 它就知道这是数据库操作，看到 `math.add` 它知道是纯计算。