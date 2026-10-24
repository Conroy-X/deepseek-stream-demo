# Unit 6 · Streaming：SSE 流式输出

> 目标：让模型回答**逐字出现**，而不是转圈等 5 秒突然弹出一整段。
> 这个单元打通两条链路：后端 `StreamingResponse` → 浏览器 `fetch` + `ReadableStream`。

---

## 一、为什么需要流式

| 指标 | 非流式 | 流式 |
| --- | --- | --- |
| 首字延迟 TTFT | = 总生成时间（3~10s） | **0.3~1s**，几乎立刻有反馈 |
| 用户感知 | 一直转圈，不知道在不在干活 | 文字持续冒出，能读一半就开始理解 |
| 实际总耗时 | 一样的 | 一样的 |

**关键认知**：流式**不会让总时间变短**，它改变的是**首字延迟**和**过程可见性**。这是体验问题，不是性能问题。

---

## 二、SSE 到底是什么

就是一段 `text/event-stream` 的 HTTP 响应，格式固定：

```
data: {"type":"delta","content":"流"}

data: {"type":"delta","content":"式"}

data: [DONE]

```

规则只有三条：

1. 每条消息以 `data: ` 开头，**以两个换行 `\n\n` 结束**
2. 两个换行是「这个事件完了」的标记，客户端靠它切分
3. 最后用 `data: [DONE]` 表示结束（OpenAI 的约定，不是协议强制）

一个小细节容易踩：**一次 `read()` 拿到的可能不是完整事件**——可能是半个事件，也可能是三个半事件。所以前端必须有缓冲区，按 `\n\n` 切分，剩下的留到下次。

---

## 三、为什么不用 EventSource

`EventSource` 是浏览器原生 SSE 客户端，很省事，但两个致命限制：

| 限制 | 后果 |
| --- | --- |
| 只支持 GET | 没法 POST 一段对话历史过去 |
| 不能自定义 headers | 没法带 `Authorization` |
| 无法中断/精细控制 | 只能 `close()`，不能 abort |

所以真实项目里都用 **`fetch` + `ReadableStream` 手动解析**。本单元的 `parseSSE()` 就是这个解析器，20 行代码，值得背下来。

---

## 四、怎么跑

### 首次准备（只需一次）

```bash
cd ~/ai-journey/unit06-streaming
uv sync
```

> 若报 `uv: command not found`，先把 `~/.local/bin` 加进 PATH：
> `export PATH="$HOME/.local/bin:$PATH"`（新开终端会自动生效）。

### 五种启动方式（全部实测通过）

| 方式 | 命令 | 说明 |
| --- | --- | --- |
| A 自检 | `uv run python main.py` | **先跑这个**。16 项断言，不调模型、不花钱 |
| B 开发模式 | `uv run uvicorn main:app --reload --port 8000` | 改代码自动重载 |
| C fastapi CLI | `uv run fastapi dev main.py --port 8000` | 带自动重载 + 交互式文档 |

任选 B/C 之一启动后：

```bash
# 看原始报文（-N 是 --no-buffer，不加看不到流式效果）
curl -N "http://127.0.0.1:8000/stream/demo?text=hello&delay=0.2"
```

浏览器打开 **http://127.0.0.1:8000/** ，有两个实验：

- **实验一**：纯文本流式，不需要 Key，用来观察报文和节奏
- **实验二**：真实模型流式，带 TTFT / 总耗时 / 分块数统计，还有「中途断开」按钮

### 排错对照

| 现象 | 原因 | 解决 |
| --- | --- | --- |
| `uv: command not found` | `~/.local/bin` 不在非交互 shell 的 PATH | 先 `export PATH="$HOME/.local/bin:$PATH"` |
| `ModuleNotFoundError: No module named 'dotenv'` | 用了系统 python3，不是 uv 管理的环境 | 用 `uv run python main.py`，不要裸跑 `python3` |
| `please install "fastapi[standard]"` | 环境里是裸 `fastapi`，没有 CLI | `uv sync`（pyproject 里就是 `fastapi[standard]`） |
| curl 一次性输出、看不到流式 | 没加 `-N` | `curl -N ...` |
| 页面空白、404 | 不是访问 `/` 而是别的路径 | 访问 `http://127.0.0.1:8000/` |

---

## 五、三个必须理解的实现细节

### 1. `if await request.is_disconnected(): break`

用户关掉页面后，如果不主动停，后端会**继续把剩下的 token 生成完并计费**。加上这个判断，断开即停止。

长连接场景下还有更隐蔽的坑：如果 LLM 上游很慢，客户端早就走了，生成器还在等——所以超时也要配。

### 2. `X-Accel-Buffering: no`

这个响应头是给 **nginx** 看的。不加的话 nginx 会把整个响应缓冲起来，前端看到的效果就和非流式一样——**本地测试正常、上线后流式失效**，这个问题非常难查。

### 3. 错误要通过 SSE 事件下发，而不是 HTTP 状态码

流式已经开始（200 已发出），这时再改状态码是不可能的。所以上游报错时，只能发一个 `{"type":"error"}` 事件。前端要能处理这种情况。

---

## 六、SSE vs WebSocket（面试高频）

| 维度 | SSE | WebSocket |
| --- | --- | --- |
| 方向 | **单向**（服务端 → 客户端） | **双向** |
| 协议 | 普通 HTTP/1.1、HTTP/2 | 独立协议，需 Upgrade 握手 |
| 自动重连 | 浏览器 `EventSource` 内置 | 要自己实现 |
| 二进制 | 只支持文本 | 支持二进制 |
| 代理/防火墙友好度 | **好**（就是 HTTP） | 差（部分代理不支持 Upgrade） |
| 适合场景 | **LLM 流式输出**、通知推送、进度条 | 聊天室、协同编辑、游戏 |

**结论**：LLM 应用几乎都用 SSE —— 我们只需要「服务器往客户端推」，双向能力是浪费，而且 SSE 更简单、更容易过代理。OpenAI、Anthropic、DeepSeek 的 API 全都是 SSE。

---

## 七、完成标准

- [ ] `python main.py` 自检全部 PASS
- [ ] `curl -N .../stream/demo` 能看到逐字输出（不加 `-N` 对比一次，体会差别）
- [ ] 浏览器实验一跑通，能看到报文实时刷出
- [ ] 浏览器实验二跑通，TTFT 明显小于总耗时
- [ ] 点「中途断开」，确认后端停止生成（看服务端日志）
- [ ] 能口述 SSE 报文格式：`data: xxx\n\n`
- [ ] 能讲清 SSE 和 WebSocket 的区别，以及为什么 LLM 选 SSE

---

## 八、资料

- [FastAPI StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse)
- [MDN：使用 Server-Sent Events](https://developer.mozilla.org/zh-CN/docs/Web/API/Server-sent_events/Using_server-sent_events)
- [MDN：ReadableStream](https://developer.mozilla.org/zh-CN/docs/Web/API/ReadableStream)
- [MDN：AbortController](https://developer.mozilla.org/zh-CN/docs/Web/API/AbortController)
- [OpenAI SDK 流式用法](https://github.com/openai/openai-python#streaming-responses)
- [Vercel AI SDK 流式协议](https://sdk.vercel.ai/docs/ai-sdk-ui/stream-protocol)（Unit 7 会用）
- [DeepSeek 对话 API 文档](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion)（`stream_options` 参数）
