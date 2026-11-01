# Unit 46 · MCP 是什么

> MCP 的推广话术是「AI 的 USB-C」。把话术剥掉，它是**一层 JSON-RPC 2.0 + 一次握手**。
> 本单元不装任何 SDK，手写全部报文 —— 因为你在 SDK 里永远看不到这些帧，出了问题就会抓瞎。

**完成标准**：能徒手写出 `initialize → notifications/initialized → tools/list → tools/call` 四帧，
能说出没握手就调用会返回哪个错误码，能解释「工具执行失败」为什么不是 `error` 帧。

## 快速开始

```bash
cd ~/ai-journey/unit46-mcp-intro
uv sync
uv run uvicorn main:app --reload --port 8046
```

打开 http://127.0.0.1:8046/ 。左边选一帧、右边看响应；顶部是生命周期四步的实时状态。
第 ⑦ 个样例是非法帧，第 ⑥ 个是「服务器没声明这个方法」—— 两个错误长得很不一样。

## 自检

```bash
uv run python main.py
# 结果：76 通过 / 0 失败
```

七组：三种帧、响应匹配、能力协商、生命周期、工具调用、资源与提示词、能力门禁与错误码、HTTP 层。
纯标准库 + fastapi，没有 `mcp` 依赖 —— 这一章的重点是协议，不是 SDK。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `protocol.py` | 帧构造与校验、能力协商、`MiniServer`（dispatch + 状态机）、`run_handshake_script` |
| `main.py` | 6 个接口 + 76 条自检 |
| `static/index.html` | 协议解剖台：生命周期、发帧、能力表、服务器日志 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 握手脚本步数 | 4（initialize / initialized / tools/list / tools/call） |
| 工具数 / 资源数 / 提示词数 | 3 / 1 / 1 |
| 未握手就 `tools/list` | `-32002 Server not initialized` |
| 未握手就 `ping` | 正常返回 `{}`（探活不受生命周期限制） |
| 调用不存在的工具 | `-32602`，`data.available` 里列出 3 个可用名 |
| 工具内部抛异常 | `result.isError = true`，**不是** `error` 帧 |
| 服务器未声明 resources 时调 `resources/list` | `-32601 Method not found` |
| 未知方法 `sampling/createMessage` | `-32601` |
| 帧里 `jsonrpc` 写成 `"1.0"` | `-32600 Invalid Request` |
| 响应 id 类型不一致（`1` vs `"1"`） | `match_response` 判定不匹配 |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 服务器身份、能力表、工具清单、协议版本、是否已握手 |
| GET | `/api/examples` | 7 个可点的帧样例（含两个错误样例） |
| POST | `/rpc` | 把一整帧喂给服务器，返回它的响应（通知返回 `null`） |
| POST | `/reset` | 重置到「连上了但没握手」的状态 |
| GET | `/api/flow` | 跑一遍完整握手脚本，逐步返回请求与响应 |
| GET | `/api/log` | 服务器收到的帧（按时间） |
| GET | `/` | 解剖台页面 |

## 常见坑

1. **通知带了 `id`**。`notifications/initialized` 一旦带上 id，服务器就会回一个响应，
   你的解析器本来在等「下一条请求的响应」，结果拿到一个空响应，后面全错位。**通知就是没有 id。**
2. **没握手就调工具**。规范要求先 `initialize` → 再 `notifications/initialized`，之后业务方法才有效。
   漏了握手，报的是 `-32002`，但很多人去查参数、查工具名，查半天。
3. **以为 `initialize` 之后就完事了**。必须再发那条**通知**，服务器才把状态置为 ready。
   这两步分开是有意的：客户端收到 `initialize` 响应后可能还要做本地准备（挂载能力、注册回调）。
4. **把工具失败包成 `error` 帧**。`error` 表示「这次 RPC 本身失败」，客户端会当协议异常处理，
   模型根本看不到原因。工具跑挂了要用 `result` + `isError: true` + 文本说明。
5. **响应 id 被转成字符串**。见过 `str(id)` 的实现，客户端永远匹配不上，症状是「一直等、最后超时」，
   而且日志里看不出任何错。
6. **能力表声明了却不会发通知**。`listChanged: true` 意味着你承诺在列表变化时发
   `notifications/tools/list_changed`。不发，客户端就一直等。声明能力等于承诺。

## 验收清单

- [ ] `python main.py` 输出 `结果：76 通过 / 0 失败`
- [ ] 页面上按「跑一遍完整流程」，四步状态全部变绿
- [ ] 点「重置」后立刻发 `③ tools/list`，看到 `-32002`
- [ ] 点「⑦ 非法帧」，看到 `-32600`，且页面提示「协议层错，连方法都没看到」
- [ ] 调 `add` 传 `{"a":"x","b":1}`，看到 `isError: true` 而不是 `error`
- [ ] 能不看文档写出四种帧的形状（请求 / 响应 / 通知 / 错误）

## 下一单元

Unit 47 · 架构与传输 —— 同样的帧，怎么从 stdio 搬到 HTTP；为什么 stdio 更适合本地工具，
HTTP 更适合多用户；以及「一条连接一次握手」到底绑定了什么。
