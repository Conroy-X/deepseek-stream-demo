# Unit 6 笔记 · Streaming（SSE 流式输出）

> 边做边填。模拟面试的复习材料。
>
> ⚠️ 状态：**材料已就绪，待学习**。下面的实测数据来自搭建时的验证，
> 你可以直接照着跑一遍，把理解补上、把空白填掉。

## 本次做了什么

- 搭好了 `unit06-streaming/`：FastAPI SSE 后端 + 浏览器实验台（`static/index.html`）
- 后端三个接口：
  - `POST /chat/stream` —— 真实模型 SSE 流式
  - `GET /stream/demo` —— 纯文本流式，**不需要 API Key**，用来看懂报文
  - `POST /chat` —— 非流式对照，用来比较首字延迟
- 前端手写了 `parseSSE()`，用 `fetch + ReadableStream` 消费流

## 流式 vs 非流式：到底改善了什么

**关键认知：流式不会让总耗时变短，它改善的是「首字延迟」和「过程可见性」。**

| 指标 | 非流式 | 流式 |
| --- | --- | --- |
| 首字延迟 TTFT | = 总生成时间 | 明显更小 |
| 总耗时 | 一样 | 一样 |
| 用户感知 | 一直转圈 | 文字持续冒出 |

实测数据（本机）：

| 场景 | 首块/首字 | 总耗时 | 分块数 |
| --- | --- | --- | --- |
| 纯文本 demo（`delay=0.2`） | 12 ms | 2219 ms | 13 |
| 真实模型（`deepseek-flash`） | 1819 ms | 2004 ms | 75 |

### ⚠️ 真实模型的 TTFT 占比高达 90.8%，这不是 bug

1.82s / 2.00s = 90.8% 的时间都花在「等第一个字」上。原因：

`deepseek-flash` **默认开启思考模式**，模型先推理再输出，那 1.8 秒是在思考，
期间**没有任何 token 可以推**。流式只能解决「输出阶段」的体验，解决不了「思考阶段」的等待。

想降低 TTFT，得从模型侧下手：
- 关思考模式（如果任务不需要）
- 换更小/更快的模型
- 缩短输入（少塞上下文）

**面试常问「流式能让响应变快吗？」**
→ 标准答案：**总时间不变，改善的是 TTFT 和过程可见性**。

## SSE 报文格式

就是一段 `text/event-stream` 响应，格式固定：

```
data: {"type":"delta","content":"流"}

data: {"type":"delta","content":"式"}

data: [DONE]

```

三条规则：
1. 每条以 `data: ` 开头，**以两个换行 `\n\n` 结束**
2. 两个换行是「这个事件完了」的标志，客户端靠它切分
3. 最后 `data: [DONE]` 表示结束（OpenAI 的约定，非协议强制）

### 实测：报文是按真实时间到达的

```
[17:15:19.999] data: {"type":"delta","content":"h","elapsed":0.0}
[17:15:20.248] data: {"type":"delta","content":"e","elapsed":0.25}
[17:15:20.498] data: {"type":"delta","content":"l","elapsed":0.5}
```

每 250ms 一块 —— 证明是真流式，不是攒完一次性发。

## 为什么不用 EventSource

| 限制 | 后果 |
| --- | --- |
| 只支持 GET | 没法 POST 一段对话历史 |
| 不能自定义 headers | 没法带 `Authorization` |
| 无法精细中断 | 只能 `close()`，不能 abort |

所以真实项目都用 **`fetch` + `ReadableStream` 手动解析**。
`static/index.html` 里的 `parseSSE()` 就是这个解析器，20 行，值得背下来。

### 解析的关键：一次 read() 可能拿到半个事件

这是最容易踩的坑：

```javascript
// ❌ 错误：假设每个 chunk 正好是一个完整事件
const data = JSON.parse(chunk);   // 会崩

// ✅ 正确：缓冲区 + 按 \n\n 切分 + 剩余部分留到下次
buffer += decoder.decode(value, { stream: true });
while ((idx = buffer.indexOf("\n\n")) !== -1) {
  const raw = buffer.slice(0, idx);
  buffer = buffer.slice(idx + 2);
  ...
}
```

## 三个必须理解的实现细节

### 1. `await request.is_disconnected()` → 立刻停止

用户关页面后，不主动停的话后端会**继续生成并计费**。

### 2. `X-Accel-Buffering: no`

这个响应头是给 **nginx** 看的。不加的话 nginx 会缓冲整个响应，
**本地测试正常、上线后流式失效**——这个问题非常难查。

### 3. 错误要通过 SSE 事件下发

流已经开始（200 已发出），无法改状态码。所以报错只能发 `{"type":"error"}` 事件，
前端要能处理。实测：缺 API Key 时依然返回 200，错误信息在 SSE 事件里。

## SSE vs WebSocket（面试高频）

| 维度 | SSE | WebSocket |
| --- | --- | --- |
| 方向 | **单向**（服务端→客户端） | **双向** |
| 协议 | 普通 HTTP | 独立协议，需 Upgrade 握手 |
| 自动重连 | `EventSource` 内置 | 要自己实现 |
| 二进制 | 只支持文本 | 支持 |
| 代理/防火墙友好度 | **好** | 差（部分代理不支持 Upgrade） |
| 适合场景 | **LLM 流式输出**、通知、进度条 | 聊天室、协同编辑、游戏 |

**结论**：LLM 应用几乎都用 SSE —— 只需要服务器往客户端推，
双向是浪费。OpenAI / Anthropic / DeepSeek 的 API 全是 SSE。

## 遇到的报错 & 解决过程

| 报错 | 原因 | 怎么解决 |
| ---- | ---- | -------- |
| `AttributeError: 'coroutine' object has no attribute 'body_iterator'` | 自检脚本里 `stream_demo(...)` 是 async 函数，忘了 `await` | 加上 `await`，并对 `body_iterator` 判断是否有 `aclose` |
| `curl` 看不到流式效果（一次性输出） | 没加 `-N`（`--no-buffer`），curl 会攒着一起输出 | 加 `-N` |

## 我自己的话解释「流式改善了什么」

（待填：用一句话说清 TTFT 和总耗时的区别）

## 我自己的话解释「SSE 和 WebSocket 怎么选」

（待填：从方向性、代理友好度、场景三个角度说）

## Token / 成本

- 搭建验证时跑了几次真实请求，约 ¥0.01

## 下次的第一件事

- 跑通浏览器实验一（不花钱），观察报文实时刷出
- 跑实验二，对比 TTFT 与总耗时
- 点「中途断开」，确认后端停止生成

## 完成标准对照

- [ ] `python main.py` 自检全部 PASS（16 项）
- [ ] `curl -N .../stream/demo` 能看到逐字输出（不加 `-N` 对比一次，体会差别）
- [ ] 浏览器实验一跑通，报文实时刷出
- [ ] 浏览器实验二跑通，TTFT 明显小于总耗时
- [ ] 点「中途断开」，确认后端停止生成
- [ ] 能口述 SSE 报文格式：`data: xxx\n\n`
- [ ] 能讲清 SSE 和 WebSocket 的区别，以及为什么 LLM 选 SSE

---

## 一句话总结

（待填：等你跑完自己写。提示——从「流式改善的是什么」和「SSE 为什么赢」两个角度收束）

## 面试可能的追问（自己先想）

1. **「流式能让响应变快吗？」**
   → 总时间不变，改善的是 TTFT 和过程可见性。真实模型 TTFT 占比 90% 是因为思考模式在烧时间。

2. **「SSE 断线了怎么办？」**
   → `EventSource` 内置自动重连（`retry:` 字段控制间隔）。
   但 `fetch` + `ReadableStream` 方案要自己实现重连，并考虑「从哪里续」的问题。

3. **「后端怎么知道客户端走了？」**
   → `request.is_disconnected()`。不检测的话会继续生成、继续花钱。
   更稳妥还要加超时（Unit 13 会做）。

4. **「流式的错误怎么返回？」**
   → 流已开始就无法改状态码，只能发一个 error 类型的事件让前端处理。

5. **「为什么本地流式好好的，上线就不流了？」**
   → 多半是反向代理缓冲。加 `X-Accel-Buffering: no`，并确保 nginx 的 `proxy_buffering off`。
