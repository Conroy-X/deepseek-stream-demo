# Unit 1 笔记 · FastAPI 基础

> 边做边填。模拟面试的复习材料。
>
> ⚠️ 状态：材料已就绪（`main.py` 单文件 + 18 项路由自检）。

## 本次做了什么

- 把 Day 1 学过的 FastAPI 概念落成一个可跑的单文件：`unit01-fastapi/main.py`
- 8 个知识块：路由 / Path / Query / Pydantic / 异常 / async / 文件上传 / Streaming
- 18 项路由自检全部通过

## 8 个知识块速查

| # | 接口 | 学什么 |
| --- | --- | --- |
| 1 | `GET /` | 最基础的路由与 GET 语义 |
| 2 | `GET /items/{item_id}` | Path 参数：声明成 `int` 就自动校验 |
| 3 | `GET /search` | Query 参数：默认值 + `ge/le` 范围 + 必填 |
| 4 | `POST /chat/validate` | **Pydantic 请求体**：嵌套模型 + 正则 + 范围 |
| 5 | `GET /db/{id}` | 异常：400 与 404 的语义区别 |
| 6 | `GET /async-demo` | Async：`asyncio.gather` 并发等待 |
| 7 | `POST /upload` | 文件上传：`UploadFile` |
| 8 | `GET /stream` | Streaming：`StreamingResponse` + `text/event-stream` |

## 必须记住的 4 个点

1. **声明类型 = 自动校验**。`item_id: int` 省掉了手写 try/except，
   非法输入由 FastAPI 统一返回 422 并附字段级错误。
2. **Pydantic 是请求体的守门员**。`Field(min_length=1, le=100)` 这类约束，
   在 Unit 5 会被用来校验 LLM 的结构化输出——同一套工具，两个方向。
3. **async 不等于快，而等于「不阻塞」**。等待上游 IO 时让出事件循环。
   写成同步的 `requests` 会把 worker 卡死。
4. **SSE 的 MIME 必须是 `text/event-stream`**，报文格式 `data: xxx\n\n`。

## 状态码语义

| 状态码 | 什么时候用 |
| --- | --- |
| 422 | 请求参数格式/类型不对（FastAPI 自动返回） |
| 400 | 参数格式对但业务上不合法（如 ID 为负） |
| 404 | 资源不存在 |
| 500 | 服务端自身错误（未捕获异常） |

## 我自己的话解释「为什么 LLM 接口要用 async」

（待填：提示——等待上游 API 的那几秒里，同步写法会把整个 worker 卡住）

## 我自己的话解释「StreamingResponse 做了什么」

（待填：提示——不是等全部生成完再返回，而是边生成边发送）

## 完成标准对照

- [ ] `python main.py` 自检全部 PASS（18 项）
- [ ] 能打开 `/docs`，手动试三个接口（含故意传错参数看 422）
- [ ] 能解释 422 / 400 / 404 / 500 分别在什么场景用
- [ ] 能说出为什么 LLM 接口要用 `async def`
- [ ] 能默写一个 `StreamingResponse` 的最小例子

## 一句话总结

（待填）
