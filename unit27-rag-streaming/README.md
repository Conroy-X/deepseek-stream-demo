# Unit 27 · RAG Streaming

> 把「转圈 20 秒后给一整段答案」改成可取消、可观测、带引用的逐字输出。流式的价值不在快，在于**首字到达时间**和**随时能停**。

**完成标准**：能写出 `meta → token* → done` 这条事件契约、说清为什么引用只能发一次、
并演示「输出预算用完时用 `finish_reason=length` 收尾而不是断在半句话上」。

## 快速开始

```bash
cd ~/ai-journey/unit27-rag-streaming
uv sync
uv run uvicorn main:app --reload --port 8017
```

打开 http://127.0.0.1:8017/ 。实验台会真的发 `POST /v1/rag/stream` 并用 `fetch` + `ReadableStream` 逐帧消费
（`EventSource` 只能 GET，发不了带 body 的请求）。页面上有事件时间线、首字延迟、引用侧栏和一键故障注入。

## 自检

```bash
uv run python main.py
# 结果：51 通过 / 0 失败
```

七组：SSE 帧格式、token 估算与上下文预算、引用与答案、事件流顺序、输出预算与截断、断连与故障、HTTP 层。
全部离线，`extractive` 后端不需要 key。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `streaming.py` | `encode_sse` / `decode_sse` / `estimate_tokens` / `select_chunks` / `AnswerStream` / 引用与答案组装 |
| `store.py` | SQLite 存 chunk + 检索（`data/stream.db`，删了自动重建） |
| `embedding.py` | 从 Unit 15 复制的本地哈希向量 |
| `main.py` | 4 个接口 + 12 条语料 + 51 条自检 |
| `static/index.html` | 流式实验台：逐字渲染、事件时间线、首字延迟、故障注入、中止连接 |

## 实测

问「退款多久到账」，`top_k=4`，上下文选中 4 块、丢弃 0 块、上下文占 156 token：

| `max_output_tokens` | 帧数 | token 帧 | output_tokens | finish_reason | truncated |
| ---: | ---: | ---: | ---: | --- | --- |
| 300 | 46（1 + 44 + 1） | 44 | 133 | `stop` | false |
| 40 | 16（1 + 14 + 1） | 14 | 40 | `length` | true |

两次的 `input_tokens` 都是 104 —— **预算只掐输出，不重算输入**。
自检里那个更短的案例是 27 帧：`1 meta + 25 token + 1 done`，可以在输出里看到这行统计。

故障注入（`debug_fail_after: 2`）的实际收尾：HTTP 状态码已经是 200（头发出去了改不了），
所以只能靠事件宣告失败：

```
event: error
data: {"code":"GENERATION_FAILED","message":"生成中断：RuntimeError","request_id":"req_…","retryable":true}
```

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 后端类型、事件类型、chunk 数、库路径 |
| POST | `/seed` | 灌入 12 条演示语料（`{"reset": true}` 可清空重灌） |
| POST | `/v1/rag/stream` | SSE 流：`meta → token* → done`，响应头带 `X-Request-Id` |
| GET | `/api/preview?q=&top_k=` | 不开流也能看一次完整答案与 prompt（调试用） |
| GET | `/` | 流式实验台 |

请求体字段：`question`、`top_k`（默认 4）、`max_context_tokens`（默认 1200）、`max_output_tokens`（默认 300）、
`debug_fail_after`（第 N 个 token 后抛错，用来练收尾）。

## 常见坑

1. **`data:` 里放裸换行**。SSE 用空行分帧，裸换行会让解析器提前截断。
   必须 `json.dumps(..., ensure_ascii=False)`：换行被转义成 `\n`，中文也不会变成一屏 `\uXXXX`。
2. **用 `EventSource` 发 POST**。它只支持 GET，带 body 的请求发不出去。
   前端必须 `fetch` + `response.body.getReader()`，自己按 `\n\n` 切帧 —— 页面上那段解析代码就是最小实现。
3. **引用每条 token 都发一遍**。10 倍流量，而且前端每次都要重排侧栏。
   引用只在 `meta` 发一次，`token` 里只有 `{"text": piece}`。
4. **先算预算再拼接**。顺序反了就会出现「超了再回退」，`usage.output_tokens` 会超上限。
   正确做法是先判断 `estimate_tokens(answer + piece) > max_output_tokens`，超了就 `break` 并标 `truncated`。
5. **截断后不发 `done`**。前端会永远停在「生成中」。截断也必须发 `done`，只是 `finish_reason=length`。
6. **上下文装不下就 `break`**。一块 800 字的长块装不下，后面一块 100 字的短块可能正好能答 ——
   要「跳过继续试」，不能整段放弃。
7. **以为 HTTP 状态码还能改**。头一发出，后面出错只能发 `error` 事件。这就是为什么必须定义 `error` 事件。
8. **nginx 缓冲把流攒成一坨**。响应头必须带 `X-Accel-Buffering: no`，否则线上看不到逐字。

## 验收清单

- [ ] `python main.py` 输出 `结果：51 通过 / 0 失败`
- [ ] 页面上能看到逐字出现，`首字` 明显小于 `总耗时`
- [ ] 把 `max_output_tokens` 改成 40，答案是完整句子的前缀，`finish_reason=length`，页面标「被截断」
- [ ] 故障注入填 2，页面顶部出现红色错误横幅，事件时间线里 `error` 排在 `token` 之后
- [ ] 点「中止连接」，后端日志里能看到 `CancelledError`，且**没有** `done`
- [ ] 能说出为什么引用只在 `meta` 里发一次

## 下一单元

Unit 28 · 引用 UI —— 消费这里的 `meta.citations` 与正文 `[n]`，把角标做成能跳回原文、跳不了会说清原因的东西。
