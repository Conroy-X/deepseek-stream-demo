# Unit 12 · 用户系统：JWT 注册登录 + 越权防护

在 Unit 11 的数据层上加认证与授权：注册、登录、`get_current_user` 依赖注入、
以及"只能碰自己的数据"的归属校验。

## 快速开始

```bash
uv sync
cp .env.example .env          # 数据库连接沿用 Unit 11
uv run uvicorn main:app --reload --port 8012
```

- <http://127.0.0.1:8012/> 注册登录面板（右侧会实时解析 JWT payload）
- <http://127.0.0.1:8012/docs> 点右上角 **Authorize**，用 email + 密码登录后再试接口

> 本单元用 **8012**（Unit 11 是 8011，Unit 9/10 是 8000），避免端口抢占。

## 自检

```bash
uv run python main.py
```

25 项，覆盖：Argon2 存储、随机盐、账号枚举防护、token 结构、过期/篡改/禁用、
以及 B 用户对 A 用户会话的读/写/删三种越权路径。

## 接口

| 方法 | 路径 | 鉴权 | 说明 |
| --- | --- | --- | --- |
| POST | `/register` | — | 注册，密码 ≥ 8 位，重复邮箱 409 |
| POST | `/login` | — | 表单登录（`username` 填 email），返回 `access_token` |
| GET | `/me` | ✅ | 当前用户 |
| POST | `/conversations` | ✅ | 建会话，自动归属当前用户 |
| GET | `/conversations` | ✅ | 只返回自己的会话 |
| DELETE | `/conversations/{id}` | ✅ | 删自己的会话，别人的返回 404 |
| GET | `/conversations/{id}/messages` | ✅ | 拉消息，校验归属 |
| POST | `/conversations/{id}/messages` | ✅ | 追加消息，校验归属 |
| GET | `/debug/token?token=` | — | 教学用：不验签直接看 payload |

鉴权方式：`Authorization: Bearer <access_token>`

## 调用 `/login` 的三种正确姿势（以及 422 排错）

### 症状

```bash
curl -X POST http://127.0.0.1:8012/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","password":"Passw0rd!123"}'
```

返回：

```json
{"detail":[
  {"type":"missing","loc":["body","username"],"msg":"Field required","input":null},
  {"type":"missing","loc":["body","password"],"msg":"Field required","input":null}
]}
```

### 为什么会这样

`/login` 用的是 FastAPI 的 `OAuth2PasswordRequestForm`，它按 **OAuth2 密码模式**的规定取两个**表单字段**：

- `Content-Type` 必须是 `application/x-www-form-urlencoded`，**不是 JSON**；
- 字段名固定叫 `username` / `password`，**不能叫 `email`**；
- 值上把 **email 塞进 `username`**。

JSON body 里的 `email` 它根本读不到，于是报"缺 username、缺 password"。
`"input": null` 也是线索：它连请求体都没解析出来。

**还有一种同样报 422 的情况**：body 明明是表单内容，但**请求头被写成了 `application/json`**。
前端"只要 body 是字符串就补 JSON 头"的写法很容易踩到（本单元面板 2026-10-25 就这么错过一次）。
判据在 DevTools 里：**Payload 显示 `username=...&password=...`，但 Request Headers 的
`Content-Type` 是 `application/json`** —— 内容和声明不一致，服务端按声明解析，自然找不到字段。

正确的做法是「只补默认值，不覆盖调用方显式声明的 Content-Type」：

```js
const hasContentType = Object.keys(headers).some((k) => k.toLowerCase() === "content-type");
if (opts.body && typeof opts.body === "string" && !hasContentType) {
  headers["Content-Type"] = "application/json";
}
```

### 三种正确调用

**① curl 表单**（最常用于脚本）

```bash
curl -s -X POST http://127.0.0.1:8012/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'username=me@example.com' \
  --data-urlencode 'password=Passw0rd!123'
```

期望：`200` + `{"access_token":"eyJ...","token_type":"bearer", ...}`

**② Swagger UI**：打开 `/docs` → 右上角 **Authorize** → 填 email + 密码 → 之后点每个接口自动带 token。

**③ 自带面板**：`http://127.0.0.1:8012/` 的登录按钮（它内部发的就是表单）。

### 对照：`/register` 反而是 JSON

同一个服务里两种格式，最容易记混：

| 接口 | 请求格式 | 字段名 |
| --- | --- | --- |
| POST `/register` | JSON | `email` / `password` / `display_name` |
| POST `/login` | 表单 | `username`（填 email） / `password` |

记法：**登录走 OAuth2 规范 = 表单；注册是自家接口 = JSON。**
`OAuth2PasswordRequestForm` 不是 FastAPI 的怪癖，是它在遵守标准，
所以任何 OAuth2 客户端（Swagger、Postman 的 OAuth2 面板）都要求同样的格式。

### 拿 token 调其它接口

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8012/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'username=me@example.com' \
  --data-urlencode 'password=Passw0rd!123' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["access_token"])')

curl -s http://127.0.0.1:8012/me -H "Authorization: Bearer $TOKEN"
```

两个常见 401 原因：忘了 `Bearer ` 前缀（只有 token 不算）、把 token 放进了 query 参数。

### 另一种失败：DevTools 里是红色 X，但服务端其实返回了 200

如果 Network 面板里 `login` 请求图标是红色 ✗、**却能点开 Payload 看到表单内容**，
说明请求发出去了、服务端也处理了，**是浏览器把响应丢了**——典型原因是跨域（CORS）：

| 现象 | 含义 |
| --- | --- |
| 红色 ✗ + 有 Payload + Response 空白 | 响应被浏览器拦掉（缺 `Access-Control-Allow-Origin`） |
| 有状态码（401/422/500）+ 有 Response | 请求正常往返，是业务/参数问题 |

**怎么确认**：

```bash
curl -s -D - -o /dev/null -X POST http://127.0.0.1:8012/login \
  -H 'Origin: http://127.0.0.1:3000' \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'username=me@example.com' --data-urlencode 'password=Passw0rd!123' \
  | grep -i access-control
```

有 `access-control-allow-origin: ...` = 没跨域问题；没有 = 浏览器一定会拦。

**怎么解决**：

1. **首选**：直接用本服务自己的面板 `http://127.0.0.1:8012/`（同源，不需要 CORS）；
2. 用别的端口起静态页（如 `uv run python -m http.server 3000 -d static`）时，
   该端口必须在 `main.py` 的 `ALLOW_ORIGINS` 里（默认已含 3000/5500/5500/8011–8015）。

> 这条坑 Unit 11–15 一开始都没配 CORS，2026-10-25 已统一补上。

## 和 Unit 11 的区别

`users` 表多了两列，靠 `ensure_schema()` 里的幂等 `ALTER TABLE` 补上：

| 列 | 用途 |
| --- | --- |
| `password_hash` | Argon2id 哈希串（`$argon2id$v=19$...`） |
| `is_active` | 禁用用户后，他手里的 token 立即失效 |

> `create_all()` 不会给已有表加列，这就是"为什么需要迁移工具"的最小现场。

## 验收清单

- [ ] `uv run python main.py` → 25 通过 / 0 失败
- [ ] 库里 `select password_hash from users;` 看到的是 `$argon2id$` 开头，不是明文
- [ ] 不带 token 请求 `/conversations` 返回 401
- [ ] 用 B 的 token 访问 A 的会话 id 返回 404
- [ ] 手改 token 最后几位，立刻 401
- [ ] 把某用户 `is_active` 改成 false，他的 token 立刻失效

## 下一单元

Unit 13 抽 `llm_client.py`：超时、指数退避重试、统一异常、成本统计、限流。
