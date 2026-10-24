# Unit 12 笔记 · 用户系统：注册 / 登录 / JWT / 越权

> 边做边填。模拟面试的复习材料。
>
> 状态：25 项自检全过，含越权、篡改签名、过期 token、禁用用户四条攻击路径。

## 本次做了什么

- `/register`：密码用 Argon2id 哈希后落库
- `/login`：验哈希，签 JWT（HS256）
- `get_current_user`：把 token 变成 `User` 对象，任何接口挂上它即可要求登录
- 越权防护：会话查询的 `user_id` 来自 token，**不接受前端传参**

## 认证链路（背下来，面试必问）

```
注册：email + 密码  →  Argon2 哈希（带随机盐）  →  存 users.password_hash
登录：email + 密码  →  查库取哈希  →  验通过  →  签 JWT（sub=user_id, exp）
请求：Authorization: Bearer <token>
      →  验签（算法白名单 + exp）
      →  解出 sub → 查 User → 注入到路由函数
      →  业务逻辑
```

## 三个必须想清楚的设计

### 1. 密码为什么只能存哈希，而且不能自己加盐

```python
password_hash = PasswordHash((Argon2Hasher(),))
stored = password_hash.hash(plain)      # $argon2id$v=19$m=65536,t=3,p=4$<salt>$<hash>
```

哈希串里自带盐和参数，验的时候 `verify(plain, stored)` 自己会拆。
**不要**写 `hashlib.sha256(password + salt)` —— 那是自己拼方案，参数不可升级、
不带内存硬化，GPU 撞库几乎无成本。

为什么是 Argon2id 不是 bcrypt：
- bcrypt 有 **72 字节截断**问题（超长密码后半段直接被忽略）
- bcrypt 没有内存硬化，抗 GPU/ASIC 弱
- OWASP 现在的首选就是 Argon2id

为什么不用 passlib：它已停止维护，和 bcrypt 4.x 有著名的 `__about__` 兼容报错。
FastAPI 官方文档现在用 `pwdlib`。

### 2. 登录失败的信息必须"一样"

```python
if user is None or not verify_password(form.password, user.password_hash):
    raise _unauthorized("邮箱或密码错误")
```

用户不存在和密码错误返回**同一句**，否则：
- "该邮箱不存在" → 攻击者可以批量枚举平台有哪些用户
- 这是经典的**账号枚举漏洞**

同理，`verify_password` 遇到格式非法的哈希要 `return False` 而不是抛异常 ——
500 和 401 的差异同样会泄露信息。

### 3. 别人的资源返回 404，不是 403

```python
if conv is None or conv.user_id != user.id:
    raise HTTPException(status_code=404, detail="conversation 不存在")
```

403 = "东西存在，但你没权限"，等于确认了这个 id 是有效的，攻击者可以拿它遍历全站。
404 = 什么都不说。

### 4. 越权防线写在"数据来源"上，不是写在参数校验上

```python
# ✅ 正确：user_id 来自 token
stmt = select(Conversation).where(Conversation.user_id == user.id)

# ❌ 错误：user_id 来自前端
# GET /conversations?user_id=123   ← 改个数字就看别人的了
```

这是最容易被写错的地方：很多人把 `get_current_user` 挂上了，却忘了用它来过滤数据，
变成"登录了但能看到所有人的东西"。**认证 ≠ 授权。**

## JWT 的六个坑

1. **JWT 不是加密，只是签名。** 任意人 base64 解开就能读 payload。
   页面上那个"这个 token 里到底有什么"就是把 `sub/exp/iat/jti` 明明白白摆出来证明这件事。
   所以里面绝不能放密码、手机号、身份证。

2. **算法必须显式指定白名单**：
   ```python
   jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
   ```
   历史上最经典的 JWT 漏洞就是"不校验 alg" —— 攻击者把 `alg` 改成 `none`，
   或者把 RS256 的公钥当 HS256 的密钥用，直接绕过签名。`require` 则强制必须有 `exp/sub`。

3. **时间一律 UTC。** `exp` 是 Unix 时间戳，写本地时间必错。

4. **`sub` 按规范是字符串**，`user_id` 是 int 时要 `str()` 一下再塞。

5. **过期和无效要分开给前端**。"过期"该去刷新 token，"无效"该踢回登录页。
   同一个 401 里用不同 detail 区分。

6. **`JWT_SECRET` 泄露 = 鉴权归零。** 任何人能给你签任意用户的 token。
   生产必须配一个长随机串，且多实例必须共用同一个值（否则 A 签的到 B 验不过）。

## 关于"加字段"这件小事

Unit 11 的 `users` 表没有 `password_hash`。这个单元要加，但
`SQLModel.metadata.create_all()` **不会给已有表加列**，它只建缺的表。

所以写了 `ensure_schema()`：

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255) NOT NULL DEFAULT ''
```

三个细节值得记：
- `IF NOT EXISTS` 让迁移幂等，重复启动不报错
- `NOT NULL` 必须带 `DEFAULT`，否则表里已有数据时 PG 会直接拒绝
  （现有行不知道填什么）—— 这是加列最经典的坑
- 老用户的 `password_hash` 会被填成空串，**等于他们无法登录**。
  真实迁移要分三步：加可空列 → 回填 → 再改 NOT NULL。

这套手工 SQL 就是 Alembic 在做的事。生产环境用它，因为还需要
版本号、回滚、多人协作时的先后顺序。

## 完成标准对照

- [x] 注册能在库里看到 `$argon2id$...` 而不是明文
- [x] 同一密码两次注册，哈希不同（证明盐是随机的）
- [x] 登录拿到 token，`/docs` 的 Authorize 能验通
- [x] 不带 token 访问 `/me` → 401
- [x] 过期 token → 401，且原因为"已过期"
- [x] 篡改签名 → 401
- [x] 用户 B 读/写/删用户 A 的会话全部 404
- [x] 禁用某用户后，他手里的 token 立刻失效

## 一句话总结

认证解决"你是谁"，授权解决"你能碰什么"；JWT 只是把"你是谁"签了名带在身上，
真正挡住越权的是**每一处查询都用 token 里的 user_id 去过滤数据**。

## 面试追问

1. **「为什么不用 session，用 JWT？」**
   → JWT 无状态，服务端不存东西，天然适合多实例横向扩展；跨域/跨端也方便。
   代价是**无法主动吊销**（签发后到期前一直有效）。要吊销就得加黑名单（按 `jti` 存 Redis），
   那就又变成有状态了。所以选型看场景：内部系统 session 更简单可控，对外开放 API 常用 JWT。

2. **「token 存 localStorage 还是 cookie？」**
   → localStorage 简单但会被 XSS 偷；cookie 可以设 `HttpOnly` + `SameSite` 防 XSS，
   但要防 CSRF。生产常见做法是 access token 短命放内存、refresh token 放 HttpOnly cookie。

3. **「密码为什么不能用 MD5/SHA256？」**
   → 它们是为"快"设计的，GPU 每秒能撞几十亿次。密码哈希必须**故意慢**：
   Argon2id 靠内存硬化和可调参数把单次耗时压到几十毫秒，暴力破解成本直接上升几个数量级。

4. **「用户 B 拿到 A 的会话 id，能做什么？」**
   → 什么都不能。每个接口都会校验 `conversation.user_id == current_user.id`，
   不合就 404。并且列表接口的过滤条件来自 token，前端无法干预。

5. **「JWT 泄露了怎么办？」**
   → 短期内无解（无状态是双刃剑）。所以 access token 要短命（15-60 分钟），
   配 refresh token；再加异常检测（异地登录、频繁 401）；必要时按 `jti` 加黑名单强制下线。
