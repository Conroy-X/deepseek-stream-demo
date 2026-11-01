# Unit 51 笔记 · Resource

> 这一章一个 tool 都没挂 —— 不是省事，是 statement。
> 不是所有「给模型的数据」都该做成 tool。

## 本次做了什么

- 写 `server.py`：2 个固定 resource + 1 个模板 resource + **0 个 tool**。
- 写 `client.py`：基于 `mcp.client.stdio.stdio_client` 的同步包装（`MCPClient + fetch_catalog()`）。
- 写 `main.py`：4 接口 + **45 条自检**（0 失败，连跑两次一致）。
- 写 `static/index.html`：阅读器 + 路径遍历测试框。
- 留下两个不踩坑的人会奇怪的设计：loader 函数故意 raise（让脱敏错误流可见）、模板大小写归一化在 loader 里做。

## 一、为什么不挂 tool

Unit 48 已经说过「谁决定用它」这条轴：
- tool：模型决定 → 模型临场决定要不要调
- resource：宿主决定 → 应用 UI 里勾选或自动挂载

公司手册、变更日志、新人知识库这些**只读、确定、宿主预先能枚举**的东西就该做成 resource。
宿主在用户问问题时直接把它挂进上下文，模型看到的就是现成内容，**不需要「想到去拿」**。

而把它做成 tool，模型不知道这份文档存在，永远不会调 —— 这是上一章讲过的错位。

## 二、固定 resource / 模板 resource 是两个接口

```python
@server.resource("doc://handbook/leave", name="...", mime_type="text/markdown")  # 固定
@server.resource("doc://kb/{topic}", name="...", mime_type="text/markdown")         # 模板
```

看上去只差一个 `{topic}`，但 SDK 会把第二个**注册成模板**，并强制 `resources/templates/list` 单独返回。客户端必须**两个接口都拉**才能拿到完整目录。

为什么这样分：固定 resource 的 URI 列表可以直接渲染成菜单；模板 resource 必须让用户先**填占位符**才能读，UI 上是不同形态。混在一起，客户端分不清「直接点」和「要填参数」。

实测：`resources/list` 返回 2 条；`resources/templates/list` 返回 1 条。共 3 个可达资源，但分布两个接口。

## 三、resource 没有「失败文本」通道

Unit 49 里讲过 tool 的两类失败：异常脱敏成 `Error executing tool xxx`；自己 try/except 把中文原因当正常返回值。

resource 只有第一种 —— **没有第二种**。loader 抛异常 → 客户端拿到脱敏的 `MCPError: Error creating resource from template`，
底层 KeyError（`未知主题：nope`）一行都看不到。

这意味着两件事：

1. resource 不适合承载业务 —— 业务失败必须让模型看到原因，而 resource 没有这条通道。
2. 想做「按 topic 取文档」，loader 必须**自己兜掉异常**，但即使兜住也只能丢成正常文本（含「未知主题」字样），无法表达「这是个错误」。

## 四、路径遍历防护不在 SDK —— loader 自己写

```python
def kb_doc(topic: str) -> str:
    try:
        return fetch_kb(topic)[1]
    except KeyError as exc:
        raise ValueError(str(exc)) from exc
```

`{topic}` 的匹配规则是「[^/]+」—— 不会跨斜杠，所以 `doc://kb/../etc/passwd` 不会命中（实测： relentlessly抛 `Unknown resource`）。
但**SDK 不替你过滤路径**，如果模板写 `{path}` 用 `-.+` 匹配，就会真撞上文件系统。
SDK 替你做的是「模板不命中」→ 拒绝；客户端能不能用 `..` 读到东西，是 loader 的事。

## 五、模板和固定 resource 的注册位置

SDK 靠 URI 里**是否含 `{}`** 判定是固定还是模板。本单元有意挂上一个 `doc://kb/{topic}` 来观察这条边界：

`resources/list` 不含 `{topic}` 那一条；`resources/templates/list` 才有它。这把分类**直接变成客户端可见的字段**，不是隐式的。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 区分固定与模板 | 两个接口分别拿到 2 条与 1 条，模板处不在固定列表 |
| 解释 resource 没有错误文本通道 | 实测抛 `MCPError`，底层原因不传客户端 |
| 大小写归一化 | `doc://kb/DEPLOY` 能读到「发布流程」 |
| 路径遍历不读本地文件 | 实测 `doc://kb/../etc/passwd` 不命中 |
| 离线可自检 | 45 条断言，0 失败，连跑两次一致，无网络 |

## 一句话总结

**resource 是「宿主挂上去的上下文」—— 它该做的不是给模型决策权，而是给宿主一个能枚举的清单。

## 面试追问

**Q：什么时候 resource 比 tool 更合适？**
A：当「能不能让模型读到这份内容」的决策权在**宿主**而不在模型手里时。
判据：宿主能预先枚举 URI → resource（如 `doc://handbook/leave`）；
模型临场才能决定具体查什么 → tool。

**Q：resource 的 loader 抛异常客户端会看到什么？**
A：看到的是脱敏后的 `MCPError: Error creating resource from template`。
真实原因只写到 server 端 stderr。底层异常（KeyError / file not found / 解析失败）
都会被框架吞掉重写。所以 resource 侧没法把「业务错误原因」交给客户端 ——
这也是把「读不到就返回中文」设计成 tool 而不是 resource 的原因。