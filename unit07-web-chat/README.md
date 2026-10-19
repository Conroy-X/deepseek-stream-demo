# Unit 7 · 全栈打通：浏览器 → FastAPI → LLM

> 目标：**浏览器能对话，而且是逐字出现的**。
> 这个单元的重点不是功能有多全，而是把 CORS 这个坎彻底搞清楚。

---

## 一、和 Unit 6 的区别

| | Unit 6 | Unit 7 |
| --- | --- | --- |
| 页面来源 | FastAPI 自己托管（同源） | 独立静态页（**跨源**） |
| CORS | 不存在这个问题 | **必须显式配置** |
| 考察点 | SSE 原理 | 前后端协作 + 跨域 |

Unit 6 的页面和后端是同一个 origin，所以请求不会触发 CORS。
Unit 7 把前端单独起在 `:3000`，后端在 `:8000`，浏览器会先发 `OPTIONS` 预检，
没配 `CORSMiddleware` 就直接被拦——**而且报错只在浏览器控制台，后端日志里什么都看不到**。

---

## 二、怎么跑

### 1. 后端

```bash
cd ~/ai-journey/unit07-web-chat
uv venv --python /usr/bin/python3
uv pip install -r requirements.txt
cp .env.example .env        # 填 API Key
chmod 600 .env

.venv/bin/python main.py                          # 自检（不花钱，15 项）
.venv/bin/uvicorn main:app --reload --port 8000   # 起服务
```

### 2. 前端

```bash
cd ~/ai-journey/unit07-web-chat
python3 -m http.server 3000 -d static
```

打开 **http://127.0.0.1:3000/**

> 用 `file://` 直接打开也行（Origin 是 `null`，已加进白名单），
> 但推荐用 HTTP 服务器，行为更接近真实部署。

### 3. 验证顺序（这个顺序很重要）

页面上的三个卡片就是排错顺序：

1. **先点「检测 /health」** —— 通了说明跨域没问题
2. **再跑「不花钱的链路验证」** —— 不调模型，只测 SSE 通不通
3. **最后发真实问题** —— 这时才花 API 钱

这样出问题时能立刻定位是「跨域挂了」还是「模型挂了」。

---

## 三、CORS 到底在做什么

浏览器有**同源策略**：`http://a.com` 的 JS 不能读 `http://b.com` 的响应。
CORS 是**服务端主动声明**「我允许哪些源访问我」。

### 两种请求

**简单请求**（GET/POST + 简单头）→ 直接发，服务端响应带 `Access-Control-Allow-Origin`

**预检请求**（POST + `Content-Type: application/json`）→ 浏览器**先发一个 `OPTIONS`**：

```
OPTIONS /chat/stream
Origin: http://localhost:3000
Access-Control-Request-Method: POST
Access-Control-Request-Headers: content-type
```

服务端要回：

```
Access-Control-Allow-Origin: http://localhost:3000
Access-Control-Allow-Methods: POST, GET, OPTIONS
Access-Control-Allow-Headers: content-type
Access-Control-Max-Age: 600
```

**注意**：我们的 `POST /chat/stream` 带 JSON body，属于**非简单请求**，
所以每次会话都会先预检一次。预检结果会被浏览器缓存（`max-age`）。

### 配错的表现

| 现象 | 原因 |
| --- | --- |
| `No 'Access-Control-Allow-Origin' header` | 后端没加 `CORSMiddleware` |
| `origin 'null' has been blocked` | 用 `file://` 打开，Origin 是 `null`，需加进白名单 |
| 预检 405 | `allow_methods` 没包含 OPTIONS（用 `["*"]` 可避免） |
| 带 cookie 时失败 | `allow_origins` 用了 `"*"` 但 `allow_credentials=True`，**两者不能共存** |

---

## 四、为什么 API Key 只能放后端

```javascript
// ❌ 绝对不要这样
fetch("https://api.deepseek.com/chat/completions", {
  headers: { Authorization: `Bearer ${API_KEY}` }   // Key 暴露在浏览器！
});
```

前端代码对用户完全可见（F12 → Sources 就能看到），Key 一旦放过前端就等于公开。
正确做法：**前端只调自己的后端，后端持有 Key**。这是面试高频考点。

本单元的前端代码里**没有任何 Key**，只有 `BACKEND = "http://127.0.0.1:8000"`。

---

## 五、文件结构

```
unit07-web-chat/
├── main.py              # FastAPI：SSE + CORS + /health
├── static/index.html    # 前端：三卡片排错法
├── requirements.txt
├── pyproject.toml
└── .env.example         # 含 ALLOW_ORIGINS
```

### 关键代码位置

| 位置 | 内容 |
| --- | --- |
| `main.py` `add_middleware(CORSMiddleware, ...)` | CORS 配置，**核心** |
| `main.py` `/health` | 前端探测用，返回允许的来源列表 |
| `main.py` `/chat/stream` | SSE 流式 |
| `static/index.html` `BACKEND` 常量 | 改这里切换后端地址 |

---

## 六、完成标准

- [ ] `.venv/bin/python main.py` → 15 通过 / 0 失败
- [ ] 后端起来后，浏览器点「检测 /health」显示「已连通」
- [ ] 跑「不花钱的链路验证」，文字逐字出现
- [ ] 发真实问题，回答逐字出现
- [ ] 故意把 `ALLOW_ORIGINS` 改错，观察浏览器控制台报错（**一定要试一次**）
- [ ] 能解释：为什么预检请求是 `OPTIONS` 而不是 `POST`
- [ ] 能解释：为什么不能把 API Key 放前端

---

## 七、常见问题

| 现象 | 解决 |
| --- | --- |
| `Failed to fetch` | 后端没启动 / 端口不对 / CORS 没配。先看浏览器 Console |
| 点检测按钮没反应 | 后端 8000 端口没起，或 `BACKEND` 常量写错 |
| 预检返回 400 | `allow_origins` 里的值和你访问的地址不完全一致（`127.0.0.1` 和 `localhost` 是不同源！） |
| 流式变成一次性输出 | 检查 `X-Accel-Buffering: no`，以及是否有反向代理 |

> **最容易踩的坑**：`http://localhost:3000` 和 `http://127.0.0.1:3000` 是**两个不同的源**。
> 白名单里两个都要写。

---

## 八、资料

- [FastAPI CORS](https://fastapi.tiangolo.com/tutorial/cors/)
- [MDN：跨源资源共享（CORS）](https://developer.mozilla.org/zh-CN/docs/Web/HTTP/CORS)
- [MDN：同源策略](https://developer.mozilla.org/zh-CN/docs/Web/Security/Same-origin_policy)
- [Next.js 官方文档](https://nextjs.org/docs/app) —— 想用 Next.js 时的正式方案
- [Vercel AI SDK](https://sdk.vercel.ai/docs/introduction) —— `useChat` 钩子，省掉手写 SSE 解析
