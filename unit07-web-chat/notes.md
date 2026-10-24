# Unit 7 笔记 · 全栈打通（CORS）

> 边做边填。模拟面试的复习材料。
>
> 状态：自检 15/15 通过；跨域流式实测通过；已做完「故意改错 ALLOW_ORIGINS」实验。

## 本次做了什么

- 后端：FastAPI + SSE + **CORSMiddleware**，`/health` 给前端探测
- 前端：独立静态页（不是 Next.js，避免 node_modules 依赖），三卡片排错法
- 实测：真实模型 29 个分块、TTFT 1.06s

补充几条容易忘的环境事实：

- `main.py` **没有 mount static**（和 unit06 不一样），前端必须单独起服务器：
  `python3 -m http.server 3000`
- 后端：`uv run uvicorn main:app --reload --port 8000`
- 前端 `static/index.html` 里 `const BACKEND = "http://127.0.0.1:8000"`，靠这个常量切后端地址
- 正因为前后端分两个端口，才产生跨域、才需要 CORS。**unit06 是同源（页面由 FastAPI 自己托管），压根碰不到 CORS** —— 这是两个单元最本质的区别

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

而 `GET /health` 是简单请求，**不发预检** —— 这个差别在做改错实验时能看得特别清楚：
一个死在预检，一个死在被吞掉的响应。

### 实测的预检响应（白名单命中）

用 curl 复刻浏览器的预检（curl 本身不受 CORS 约束，这里只是看服务端怎么答）：

```bash
curl -i -X OPTIONS http://127.0.0.1:8000/chat/stream \
  -H 'Origin: http://127.0.0.1:3000' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type'
```

```
HTTP/1.1 200 OK
vary: Origin, Access-Control-Request-Method, Access-Control-Request-Headers, Access-Control-Request-Private-Network
access-control-allow-methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT, QUERY
access-control-max-age: 600
access-control-allow-credentials: true
access-control-allow-origin: http://127.0.0.1:3000
access-control-allow-headers: content-type
```

三个值得注意的地方：

- `access-control-allow-origin` 是**原样回显**请求的 Origin，不是 `*`。因为开了 credentials，`*` 会被浏览器判为无效。
- `vary: Origin` 告诉缓存层「这份响应随 Origin 变化」。少了它，CDN/nginx 可能把 A 源的响应发给 B 源。
- `QUERY` 是较新 Starlette 加的。说明 `allow_methods=["*"]` 是被**展开成具体方法列表**的，不是把字面量 `*` 发出去。

## 故意改错 ALLOW_ORIGINS（必做实验）

### 怎么改

改 `.env` 第 7 行，**不是** `main.py` 里的默认值 —— `.env` 存在时 `os.getenv` 读的是它，
`main.py` 第 40 行那串默认值只是兜底，永远不会生效。

```bash
cp .env .env.bak
sed -i 's|^ALLOW_ORIGINS=.*|ALLOW_ORIGINS=http://127.0.0.1:3001|' .env
```

把当前页面用的源（3000）从白名单里踢掉，就是最标准的「改错」。

**改完必须手动重启 uvicorn。** `--reload` 只监听 `.py`，**不监听 `.env`**。
不重启的话改完毫无反应，这是本实验最容易卡死的地方。

### 实测：三种请求的差别

| 请求 | 后端返回 | 关键响应头 | 浏览器侧 |
| --- | --- | --- | --- |
| `OPTIONS /chat/stream`（源在白名单） | 200 | `access-control-allow-origin: http://127.0.0.1:3000` | 放行，POST 继续发 |
| `OPTIONS /chat/stream`（源不在） | **400**，body `Disallowed CORS origin` | 无 ACAO | Console 报 preflight 失败，**POST 根本没发出** |
| `GET /health`（源不在） | **200**，body 完整返回 | 无 ACAO | `fetch` 直接 reject，**拿不到数据** |

### 这次实验真正的收获

两种失败发生在**完全不同的阶段**：

- **预检失败**：请求在「发出去之前」就被掐死。后端 access log 里只有一条 `400 OPTIONS`，业务代码一行没跑。
- **简单请求失败**：请求真的发出去了，后端正常处理、200 正常返回，**但浏览器检查响应头不合格，拒绝把响应交给 JS**。

所以 `fetch` 失败可能是「请求没发」也可能是「响应被吞」，Console 的文案会告诉你是哪一种。
第三行那种「后端 200、Network 面板看着像成功、JS 里却报错」的情况最反直觉 —— 也是这个实验最大的价值。

### 改回

```bash
cp .env.bak .env   # 然后重启 uvicorn
```

## 五个必须记住的坑

**1. `localhost` 和 `127.0.0.1` 是两个不同的源**
白名单里两个都要写，否则「用 localhost 打开能跑，换 127.0.0.1 就不行」。

**2. `allow_origins="*"` 不能和 `allow_credentials=True` 共存**
浏览器会直接拒绝。要么列白名单，要么关掉 credentials。

**3. CORS 报错只在浏览器控制台**
后端**进程不抛异常**，uvicorn 日志里没有 traceback，所以很容易误判「后端没事」。
更准确的说法是：
- 请求确实到了 FastAPI，但被 `CORSMiddleware` 层拦下，**没进入路由函数**；
- access log 里其实是有记录的（预检失败就是一条 400，不是「什么都没有」）；
- 真正的判决发生在浏览器：它拿到响应后比头，不合格就不交给 JS。

排查第一现场永远是：**F12 → Console**，第二现场是 Network 里那条 `OPTIONS` 的 400。

**4. `file://` 直接打开会失败**
双击 HTML 打开时 `Origin` 是字符串 `null`。本目录的 `.env` 里**没有**写 `null`
（`main.py` 的默认值里有，但被 `.env` 覆盖了），实测 `Origin: null` 的预检直接 400。
要么在 `.env` 里补一个 `null,`，要么乖乖用 http 服务器打开。

**5. 改了 `.env` 必须重启**
`--reload` 只监听 `.py` 文件变化，环境变量不在监听范围。

## 为什么 API Key 只能放后端

前端代码对用户完全可见（F12 → Sources）。Key 放过前端 = 公开。
正确做法：前端只调自己的后端，后端持有 Key。

本单元前端代码里没有任何 Key，`/health` 返回的 `key_configured` 只是个布尔值，不泄露任何内容。

## 我自己的话：预检为什么是 OPTIONS 而不是 POST

`OPTIONS` 在 HTTP 语义里是「安全方法」——调用它不会改动资源，只是问这个地址支持什么。
浏览器要问的是「我能不能用 POST 发一段 JSON 给你」，如果用 POST 去发这个问句，
那问题本身就已经是一次真实的写入了，逻辑上自相矛盾，中间的代理和缓存也没法安全处理。

所以预检借用 `OPTIONS`，并且这一问**不带真实 body**，只用
`Access-Control-Request-Method` / `-Headers` 描述「我打算怎么发」，
服务端再用 `Access-Control-Allow-*` 回答「准了，或者不批」。

一句话面试版：OPTIONS 是 HTTP 定义的安全方法，用它探测不影响资源状态；
用 POST 探测等于先执行了一次真实操作。

## 我自己的话：为什么 CORS 错了这么难查

CORS 的判决发生在**浏览器**，不在服务端。服务端只是老实声明「我允许谁」，然后照常返回 200。
浏览器拿到响应后发现头不对，就把响应丢掉，只在 Console 打一行红字。于是：

- 后端不抛异常，uvicorn 日志没有 traceback，看起来一切正常
- `fetch` 只是 reject，Error 对象里连状态码都没有，`e.message` 就一句 `Failed to fetch`
- curl / Postman 根本不受 CORS 约束，用它们测永远是通的

最后这点正好解释了那个经典症状：**「Postman 能通，浏览器不行」= 十有八九是 CORS**。

## 完成标准对照

- [x] `python main.py` → 15 通过 / 0 失败
- [ ] 浏览器点「检测 /health」显示已连通
- [ ] 真实提问能逐字出现
- [ ] 故意改错 `ALLOW_ORIGINS`，观察控制台报错
- [x] 能解释预检请求
- [x] 能解释 Key 为什么不能放前端

## 一句话总结

CORS 不是后端的安全机制，是浏览器替你设的一道门：服务端在白名单上签字，
浏览器负责验签，验不过就把响应没收 —— 所以错了永远只红在 Console 里，后端一脸无辜。

## 面试追问

1. **「CORS 是浏览器还是服务端在限制？」**
   → 浏览器。服务端只是声明「允许谁」，执行限制的是浏览器。
   用 curl 请求完全不受 CORS 影响（实测过）。

2. **「预检结果会缓存吗？」**
   → 会，由 `Access-Control-Max-Age` 控制。本单元设 600 秒。
   注意副作用：改了 `.env` 之后浏览器可能还在用旧的预检结果，
   调不通时先勾上 DevTools 的 Disable cache 或硬刷新。

3. **「生产环境前端后端怎么部署？」**
   → 同域（nginx 反代 `/api`）可以完全避开 CORS；分域则必须配白名单，
   且要注意带 cookie 时不能用 `*`。

4. **「`allow_credentials=True` 时为什么不能用 `*`？」**
   → 规范硬性禁止。带 cookie 的响应如果对任意源开放，等于允许任何网站以用户身份调你的接口，
   是 CSRF 的温床。所以必须精确列源，响应里也只能回显具体 Origin。

5. **「SSE 跨域有什么额外要注意的？」**
   → `EventSource` 只支持 GET、不能带自定义头和请求体，所以 unit06/07 都用
   `fetch` + `ReadableStream` 手动解析。既然走 fetch，就完全按普通请求过 CORS。
   另外必须禁掉反向代理缓冲（`X-Accel-Buffering: no`），否则流会被攒成一坨一次性吐出。

6. **「`vary: Origin` 有什么用？」**
   → 有 CDN / nginx 缓存时，只按 URL 缓存会把 A 源的响应发给 B 源，
   造成「有时能通有时不能通」的随机性故障。

## 附：本单元埋着的两个小隐患（自查用）

1. `chat_stream` 用 `finally: yield "data: [DONE]\n\n"`，好处是**所有路径都发结束标记**
   （这点比 unit06 严谨 —— unit06 的 `except Exception` 分支 `return` 掉了，不发 DONE）。
   但 async generator 在 `finally` 里 `yield`，遇到客户端硬断开触发 `aclose()` 时，
   会抛 `RuntimeError: async generator ignored GeneratorExit`。
   正常路径碰不到，极端情况下日志会脏。更稳的写法是在正常路径末尾发 DONE，`finally` 里只做清理。
2. `demo` 里 `import asyncio` 写在 `for` 循环内部，每次迭代都执行一次（Python 有缓存，开销可忽略），
   但风格上应该提到文件顶部。
