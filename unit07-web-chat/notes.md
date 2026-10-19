# Unit 7 笔记 · 全栈打通（CORS）

> 边做边填。模拟面试的复习材料。
>
> 状态：材料已就绪（后端 15 项自检，跨域流式实测通过）。

## 本次做了什么

- 后端：FastAPI + SSE + **CORSMiddleware**，`/health` 给前端探测
- 前端：独立静态页（不是 Next.js，避免 node_modules 依赖），三卡片排错法
- 实测：跨域头正确、真实模型 29 个分块、TTFT 1.06s

## CORS 到底在解决什么

浏览器有**同源策略**：`a.com` 的 JS 不能读 `b.com` 的响应。
CORS 是服务端主动声明「我允许哪些源访问」。

### 两种请求的区别

| 类型 | 触发条件 | 浏览器行为 |
| --- | --- | --- |
| 简单请求 | GET/POST + 简单头 | 直接发，看响应头 |
| **预检请求** | JSON body / 自定义头 | **先发 OPTIONS**，通过才发真实请求 |

我们的 `POST /chat/stream` 带 `Content-Type: application/json`，属于**非简单请求**，
所以每次新会话都会先来一发 `OPTIONS`。

### 实测的预检响应

```
HTTP/1.1 200 OK
access-control-allow-origin: http://localhost:3000
access-control-allow-methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
access-control-allow-headers: content-type
access-control-max-age: 600
access-control-allow-credentials: true
```

## 三个必须记住的坑

**1. `localhost` 和 `127.0.0.1` 是两个不同的源**
白名单里两个都要写，否则「用 localhost 打开能跑，换 127.0.0.1 就不行」。

**2. `allow_origins="*"` 不能和 `allow_credentials=True` 共存**
浏览器会直接拒绝。要么列白名单，要么关掉 credentials。

**3. CORS 报错只在浏览器控制台**
后端日志里什么都看不到 —— 因为请求根本没到达业务代码（被浏览器拦在响应阶段）。
排查第一步永远是：**F12 → Console**。

## 为什么 API Key 只能放后端

前端代码对用户完全可见（F12 → Sources）。Key 放过前端 = 公开。
正确做法：前端只调自己的后端，后端持有 Key。

本单元前端代码里没有任何 Key。

## 我自己的话解释「预检请求为什么是 OPTIONS 而不是 POST」

（待填：提示——OPTIONS 是 HTTP 里语义为「询问支持什么」的方法，
用它做探测不会产生副作用，符合 HTTP 语义）

## 我自己的话解释「为什么 CORS 错了后端日志看不到」

（待填：提示——请求确实到了后端，但浏览器拿到响应后检查头不对，
拒绝把响应交给 JS；所以后端日志有记录，只是没有报错）

## 完成标准对照

- [ ] `python main.py` → 15 通过 / 0 失败
- [ ] 浏览器点「检测 /health」显示已连通
- [ ] 真实提问能逐字出现
- [ ] 故意改错 `ALLOW_ORIGINS`，观察控制台报错
- [ ] 能解释预检请求
- [ ] 能解释 Key 为什么不能放前端

## 一句话总结

（待填）

## 面试追问

1. **「CORS 是浏览器还是服务端在限制？」**
   → 浏览器。服务端只是声明「允许谁」，执行限制的是浏览器。
   用 curl 请求完全不受 CORS 影响（实测过）。

2. **「预检结果会缓存吗？」**
   → 会，由 `Access-Control-Max-Age` 控制。本单元设 600 秒。

3. **「生产环境前端后端怎么部署？」**
   → 同域（nginx 反代 `/api`）可以完全避开 CORS；分域则必须配白名单，
   且要注意带 cookie 时不能用 `*`。
