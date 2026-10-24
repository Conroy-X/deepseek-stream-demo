# Unit 1 · FastAPI 基础

> 对应原计划的 Day 1：路由、Path/Query、Pydantic、Async、异常、文件上传、Streaming。
> 那一天没留下代码，这里补一个**单文件可跑**的全貌示例 —— 后面所有单元都建立在这套底座上。

---

## 一、为什么这个单元重要

你后面写的每一个 LLM 接口，本质都是这几个东西的组合：

```
HTTP 路由  +  参数校验(Pydantic)  +  异步调用上游  +  流式返回
```

所以这个单元的验收标准不是"背 API"，而是**能独立写出一个带校验、带错误处理、能流式返回的接口**。

---

## 二、怎么跑

```bash
cd ~/ai-journey/unit01-fastapi
uv sync                    # 建 .venv + 按 uv.lock 装依赖（幂等，可反复执行）

# 方式一：不启动服务，直接跑路由自检（最快，验证全部逻辑）
uv run python main.py

# 方式二：起服务，用 /docs 交互式试
uv run fastapi dev
# 打开 http://127.0.0.1:8000/docs
```

---

## 三、文件里的 8 个知识块

| # | 位置 | 学什么 |
| --- | --- | --- |
| 1 | `/` | 最基础的路由与 GET 语义 |
| 2 | `/items/{item_id}` | Path 参数：声明成 `int` 就自动校验，`"abc"` 直接 422 |
| 3 | `/search` | Query 参数：默认值 + `ge/le` 范围 + 必填 |
| 4 | `/chat/validate` | **Pydantic 请求体**：嵌套模型 + 正则 + 范围，后面所有 LLM 接口都靠它 |
| 5 | `/db/{id}` | 异常：400 与 404 的语义区别（而不是全抛 500） |
| 6 | `/async-demo` | Async：`asyncio.gather` 并发等待，为什么 LLM 接口必须 async |
| 7 | `/upload` | 文件上传：`UploadFile` + 读取字节 |
| 8 | `/stream` | Streaming：`StreamingResponse` + `text/event-stream`（SSE 底座） |

---

## 四、必须记住的 4 个点

1. **声明类型 = 自动校验**。`item_id: int` 省掉了手写 try/except 和 if 判断，
   非法输入由 FastAPI 统一返回 422 并附字段级错误信息。
2. **Pydantic 是请求体的守门员**。`Field(min_length=1, le=100)` 这类约束，
   在 Unit 5 会被用来校验 LLM 的结构化输出 —— 同一套工具，两个方向。
3. **async 不等于快，而等于"不阻塞"**。等待上游 IO 时让出事件循环，
   一个 worker 能同时扛很多请求。写成同步的 `requests` 会把 worker 卡死。
4. **SSE 的 MIME 必须是 `text/event-stream`**，报文格式是 `data: xxx\n\n`。
   Unit 3/6 的流式回答就是这套机制。

---

## 五、完成标准

- [ ] `uv run python main.py` 自检全部 PASS
- [ ] 能打开 `/docs`，手动试三个接口（含故意传错参数看 422）
- [ ] 能解释 422 / 400 / 404 / 500 分别在什么场景用
- [ ] 能说出为什么 LLM 接口要用 `async def`
- [ ] 能默写一个 `StreamingResponse` 的最小例子

---

## 六、资料

- [FastAPI 中文文档](https://fastapi.tiangolo.com/zh/)（这个单元建议从上往下读一遍 Tutorial）
- [Path / Query 参数](https://fastapi.tiangolo.com/tutorial/path-params/)
- [请求体与 Pydantic](https://fastapi.tiangolo.com/tutorial/body/)
- [Async 与并发](https://fastapi.tiangolo.com/async/)
- [StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse)
- [MDN：Server-Sent Events](https://developer.mozilla.org/zh-CN/docs/Web/API/Server-sent_events/Using_server-sent_events)
- [Pydantic v2 文档](https://docs.pydantic.dev/latest/)
