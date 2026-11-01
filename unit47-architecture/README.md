# Unit 47 · 架构与传输

> 帧的形状在 Unit 46 已经定死了。这一章只问一件事：**这些帧怎么从 A 进程走到 B 进程。**
> stdio 上你得自己解决「一帧到哪里结束」；HTTP 上边界白送，但每帧要背一堆头，还得自己把「连接身份」补回来。

**完成标准**：能说清 Host / Client / Server 各自负责什么、不负责什么；
能解释为什么 stdio 上不能用缩进 JSON；能说出一条连接一次握手在 HTTP 上要靠什么维持；
能算出同一批帧在两根管子上的字节差。

## 快速开始

```bash
cd ~/ai-journey/unit47-architecture
uv sync
uv run uvicorn main:app --reload --port 8047
```

打开 http://127.0.0.1:8047/ 。四块面板：三层职责表、字节成本对照、分帧器实验台、会话绑定演示。
分帧器那块最值得动手 —— 把切片大小改成 1 字节，看它怎么一字节一字节地攒出一帧；
再点「换成缩进 JSON」，看它当场炸掉。

## 自检

```bash
uv run python main.py
# 结果：71 通过 / 0 失败
```

九组：三层职责、分帧、缩进 JSON 的致命性、stdio 主循环与 EOF、HTTP 会话绑定、
线缆字节成本、完整 stdio 会话、HTTP 层、分帧接口。
纯标准库 + fastapi，**没有 `mcp` 依赖** —— 这一章讲的还是协议，不是 SDK。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `transport.py` | `FrameCodec`（分帧）、`MemoryPipe` / `PipePair`、`StdioTransport`、`serve_stdio`、`SessionTable`、`compare_wire_cost`、`TOPOLOGY` |
| `main.py` | 9 个接口 + 71 条自检 |
| `static/index.html` | 传输对照台：职责表、字节表、分帧器、会话演示、会话脚本 |

## 实测

| 项目 | 结果 |
| --- | --- |
| stdio 三帧总字节 | **191**（89 + 55 + 47，一行一帧，零头部） |
| 同三帧走 HTTP 总字节 | **806**（294 + 260 + 252，含请求头 + 响应头） |
| 比值 | **4.22 倍**，多付 **615 字节** |
| 单帧最大开销 | `initialize`：89 B body → 294 B 线缆 |
| 缩进 JSON（`tools/list`） | 47 B → **60 B**，**5 个换行** |
| 缩进 JSON 喂给分帧器 | 报错 `原始行=b'{'` —— 第一个换行处就被截断 |
| 按 7 字节切片喂 47 B 帧 | **7 步**，中间最多残留 6 B，最终切出 1 帧 |
| 未握手就发通知 | 服务端接收 1 帧、回 0 帧（`frames_out = 0`） |
| stdin EOF | `exit_reason = "eof"`，进程退出 |
| 半帧 + EOF | `exit_reason = "eof_with_partial_frame"`，残留被报出 |
| 不带 session 的后续请求 | **400** |
| 通知在 HTTP 上 | **202**，无 body |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 协议版本、两种传输、帧分隔符、会话头名、活跃会话数 |
| GET | `/api/topology` | 三层职责（负责什么 / 不负责什么 / 一句话） |
| GET | `/api/wire` | 同一批帧在 stdio 与 HTTP 上的字节成本对照 |
| POST | `/api/splitter` | 把文本按指定切片大小喂给分帧器，返回每一步的残留 |
| GET | `/api/pretty-bug` | 缩进 JSON vs 紧凑 JSON 的分帧结果 |
| GET | `/api/session-demo` | 带 / 不带 session id 的两种结局 |
| POST | `/rpc` | HTTP 传输下的一帧；`initialize` 免 session，其余必须有 `Mcp-Session-Id` |
| GET | `/api/stdio-session` | 跑一遍四帧 stdio 会话，含 EOF 退出 |
| GET | `/` | 对照台页面 |

## 常见坑

1. **用 `json.dumps(indent=2)` 生成帧**。人类看着舒服，分帧器当场报废：
   它按 `\n` 切，第一个换行切在 `{` 后面，剩下的全是半个 JSON。
   实测报错 `原始行=b'{'` —— 你会在日志里看到一堆「JSON 非法」，却不一定想到是格式化干的。
2. **假设一次 `read()` 正好读到一帧**。网络和管道都不保证这个。可能是半帧，也可能是三帧连在一起。
   所以必须有 `buffer`：切不出来的留着等下一段，一次切出多个就全部返回。
3. **服务器读到 stdin EOF 不退出**。客户端走了，你的进程还挂在那儿等一个再也写不进来的管道。
   宿主每重启一次，就多一个僵尸。必须显式处理 EOF。
4. **半帧 + EOF 当成正常退出**。对面写了一半就断了，这是**错误**，不是结束。
   `eof_with_partial_frame` 要和 `eof` 分开报，否则丢帧永远查不出来。
5. **HTTP 上只做 `initialize`，后面不带 session id**。每个请求都被当成陌生人，
   服务器**每次**都重新握手、重新建状态。症状是「工具偶尔找不到」，且完全看不出规律。
6. **在 FastAPI 里用 `response: Response` 注入再加头，同时 `return JSONResponse(...)`**。
   注入的那个对象会被丢弃，你设的 `Mcp-Session-Id` 静默消失。要么在返回对象上设头，要么 `return dict`。
7. **以为 HTTP 比 stdio 轻**。实测反了：同样的三帧，stdio 191 字节，HTTP 806 字节，**4.22 倍**。
   stdio 适合本地工具不是因为它「高级」，是因为它几乎不花钱。

## 验收清单

- [ ] `python main.py` 输出 `结果：71 通过 / 0 失败`
- [ ] 页面上三层职责表三列都在，每层都有「不负责」清单
- [ ] 字节表里 HTTP 合计明显大于 stdio 合计，页脚给出倍数
- [ ] 分帧器选 1 字节切片，能看到残留字节数一路涨上去再归零
- [ ] 点「换成缩进 JSON」再喂，看到红色报错而不是帧
- [ ] 会话演示里 `without_id.ok === false`，`rejected` 从 0 涨到 1
- [ ] 能不看代码说出：stdio 的帧边界是什么、HTTP 的会话身份靠什么

## 下一单元

Unit 48 · 三大能力 —— Tools / Resources / Prompts 不是三个并列的列表，
而是三种**不同性质**的东西：一个做动作、一个给上下文、一个固化流程。
分不清它们的边界，你的 server 会长出一堆「用起来别扭」的工具。
