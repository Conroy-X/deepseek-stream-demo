# Unit 50 笔记 · 自定义 Tool

> 工具的边界不是代码边界，是**模型看得懂的那部分**边界。代码里写得再漂亮，
> `description` 没写清「参数是什么单位」，模型就会传 `"3km"` 进来。

## 本次做了什么

- `server.py`：`MCPServer` 挂 4 个工具，背后是可单测的纯函数（`fx_convert` / `convert_value` / `compute_stats`）。
- `client.py`：`MCPClient` 封装 `stdio_client + ClientSession`，另给 FastAPI 用的同步一次性包装。
- `main.py` 5 个接口 + **65 条**自检（0 失败，连跑两次）；`static/index.html` 按 `input_schema` 现场生成表单。
- `data/sales.csv`：20 行销售数据，首次导入时幂等生成，不覆盖用户改过的文件。

## 一、三类工具，schema 写法完全不同

| 类别 | 本单元的例子 | schema 要特别写清的东西 |
| --- | --- | --- |
| 纯函数 | `fx_rate`、`unit_convert` | 参数单位与取值域（三位币种码 / 不带数字的单位符） |
| 有状态 | `csv_stats` | 状态在哪（`data/sales.csv`）、什么时候会变 |
| 有副作用 | `append_note` | **声明副作用 + 声明不幂等**，否则 Agent 会放心重试 |

第三种最容易被忽略。`append_note` 的描述第一句是「有副作用，重复调用会写多行」——
这不是客套，是给模型的重试策略提示。不写，Agent 遇到超时会重试，日志里立刻三条一样的笔记。
`tools/list` 返回 4 个工具共 **2566 bytes**，`description` 平均 **172 字**，每次会话都要进上下文，
所以写法必须是三句话结构：「参数含义 / 取值域 / 返回形状」——

```
参数：amount 是正数金额；from_currency 与 to_currency 是三位货币代码，
支持 USD / CNY / EUR / JPY，也接受 RMB、¥ 这类别名。
返回一行文本：换算结果（保留 2 位小数）和本次使用的汇率。
```

## 二、非法单位：宁可返回字符串，也不抛异常

```python
if not gs or not gd:
    return f"错误：不认识单位 {bad!r}，可用单位：{', '.join(all_units())}。没做任何换算。"
```

实测 `is_error = false`，文本 `错误：不认识单位 'furlong'，可用单位：...`。
同一个工具传错**类型**（`value="abc"`）时则是 `is_error = true`，
文本 `Error executing tool unit_convert: 1 validation error for ... value Input should be a valid number`。
前者是**业务拒绝**（改个参数就能成功），后者是**调用本身不合法**；pydantic 的校验信息相当可读，
这是「schema 写严一点」的回报。

## 三、五种失败，五种文本，实测记录

| 触发方式 | `is_error` | 客户端拿到的文本 |
| --- | --- | --- |
| `return "错误：..."` | `false` | 完整中文原因（自己写的） |
| 参数类型 / 必填缺失 | `true` | `Error executing tool X: 1 validation error for ...` |
| `raise RuntimeError("内部炸了")` | `true` | `Error executing tool X` —— **原因被吃掉** |
| 调不存在的工具 | `true` | `Unknown tool: nope` |
| `raise ToolError("...")` | `true` | `Error executing tool X: 我不认识 SQL ...` |

最后一行值得单独记：`ToolError` 是**第三种选择** —— 协议层标记失败，同时保留原因；比 `return`
更诚实，比 `raise` 更有用。（临时脚本实测，本单元没挂 `ToolError` 工具。）

## 四、与 SDK 事实卡的一处不一致（重要）

`docs/mcp-sdk-2x-notes.md` 第 5 节坑二写的是「工具不存在 / 参数不对，客户端**抛异常**」，
实测 `mcp 2.3.0` **不抛**：

```
call_tool("nope", {})              -> is_error=True, text="Unknown tool: nope"
call_tool("fx_rate", {"amount":1}) -> is_error=True, text="... 1 validation error ..."
```

抛异常的只有**资源**侧（`read_resource` 未知 uri → `MCPError: Unknown resource: doc://x`）。
`ToolError` 类仍然存在（`mcp.server.mcpserver.exceptions.ToolError`），但它是给工具内部用的，
不是客户端调用的失败出口。**写断言按上面这张表，别按事实卡。**

## 五、每次请求起一个子进程，是我故意的

HTTP 接口是 `def`（同步），内部 `anyio.run()` 起一次完整连接，跑完 `aclose()`。
代价是单次调用 **0.75 s**（进程启动 ~0.5 s + 握手 + 调用 + 关闭）；收益是没有跨事件循环的共享状态。
FastAPI 的同步路由跑在线程池里，把 `ClientSession` 存成全局变量给不同线程用，会在 anyio 的
cancel scope 上报错，而且报错信息极难读。生产做法是长连接 + 连接池（Unit 59）；
自检里反过来，一次连接跑十几个调用，65 条断言总共 4.9 s。

## 完成标准对照

| 要求 | 做到没有 | 证据 |
| --- | --- | --- |
| 4 个有真实价值的工具 | 是 | `/api/tools` 返回 `fx_rate`/`unit_convert`/`csv_stats`/`append_note` |
| 离线，不联网 | 是 | 汇率是内置常量表，CSV 在本地，全程无 `httpx` 调用 |
| 工具描述进入 `list_tools` | 是 | 断言「每个 description > 20 字」「fx_rate 描述含 USD」 |
| `input_schema.required` 正确 | 是 | `fx_rate` 三个全必填；`csv_stats` 只有 `column`（`op` 有默认值） |
| 非法单位返回中文错误 | 是 | `is_error=false` + `错误：不认识单位 'furlong'` |
| 框架层失败是 `is_error=True` | 是 | 类型错 / 未知工具 / 缺参数 三条断言 |
| 有副作用真的落盘 | 是 | `notes.log` 行数 +1，返回文本报行号 |

## 一句话总结

**工具写得好不好，就看模型只读过 `description` 和 `input_schema` 之后，
能不能一次把参数传对 —— 传错了，它能不能从返回文本里知道该怎么改。**

## 面试追问

**Q：工具报错应该抛异常还是返回字符串？**
A：取决于「谁该负责修」。参数非法、单位不支持这类错误，用户/模型换个参数就能过，就该
`return` 可读文本（`is_error=false`）让它自己重试；真正不该发生的事（磁盘坏了、逻辑 bug）才抛，
让 traceback 留在服务端 stderr 给工程师。想两者兼顾 —— 标记失败且保留原因 —— 用 `ToolError`。

**Q：`input_schema` 写宽松点是不是兼容性更好？**
A：代价很高。schema 是模型唯一的参数说明书，也是 pydantic 的校验依据。写成 `dict` 或全 `str`，
等于把解析和校验推到函数体里，模型猜错时你只能回「参数不对」，它连改哪个字段都不知道。

**Q：有副作用的工具怎么防重试？**
A：三层。①`description` 写明「有副作用 / 不幂等」；②参数带幂等键，服务端去重；
③把「写」和「查」拆成两个工具，让读的路径可以随便重试。本单元只做了①，②是 Unit 58 的内容。
