# Unit 67 · Auth：认证 vs 授权

> 目标：**分清「你是谁」（认证）和「你能干什么」（授权），401 与 403 分清楚。**

---

## 一、两个独立步骤

```
请求进来
  │
  ├─ 1. 认证 Authentication —— 你是谁？
  │    没 token / token 坏 / 过期 → 401
  │
  ├─ 2. 授权 Authorization —— 你能干什么？
  │    认证通过了，但没这个权限 → 403
  │
  └─ 3. 业务处理
```

**最容易混的**：401 和 403 的区别。

| 状态码 | 语义 | 例子 |
| --- | --- | --- |
| 401 Unauthorized | **没认证**（你是谁都不知道） | 没带 token / token 过期 |
| 403 Forbidden | **认证了但没权限** | 普通用户想删账号 |

## 二、手写最小 JWT

JWT 是三段 base64url，中间用 `.` 连：

```
header . payload . signature
{"alg":"HS256"} . {"sub":"rik","role":"user","exp":...} . HMAC(header.payload, secret)
```

本单元手写 HS256（HMAC-SHA256），关键认知：

1. **签名防篡改**——改 payload 里任何字符，签名对不上，`verify` 就失败
2. **过期内建**——payload 里 `exp` 是安全属性，不是业务字段
3. **secret 是唯一的防线**——泄露 = 任何人都能伪造任意身份的 token

> 生产用 PyJWT / python-jose，不要手写。手写是为了看懂三段结构。

## 三、RBAC 最小权限

```python
ROLES = {
    "admin": {"ask", "list_users", "set_quota", "delete_user"},
    "user":  {"ask"},
    "guest": {"ask"},
}
```

原则：**每个角色只给完成本职所需的最小权限**。本单元还加了额度表
（admin 100 / user 10 / guest 2），演示「授权之外还有资源限制」。

## 四、怎么跑

```bash
cd ~/ai-journey/unit67-auth
uv sync
uv run python main.py            # 离线自检

uv run uvicorn main:app --port 8067

# 拿 token
TOKEN=$(curl -sS -X POST "http://127.0.0.1:8067/login?role=user" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

# 没 token → 401
curl -sS -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8067/ask"

# 带 token 问 /ask → 200
curl -sS -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8067/ask"

# user 访问 admin 接口 → 403
curl -sS -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:8067/admin/set-quota"
```

## 五、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 没 token → 401；user 访问 admin 接口 → 403
- [ ] 篡改 token 的 role → 签名不匹配被拒
- [ ] 过期 token → 401
- [ ] 能解释 401 与 403 的区别
- [ ] 能说 JWT 三段分别是什么

## 六、面试追问

**Q: JWT 是无状态的，怎么吊销？**
→ 短 TTL + 刷新 token；或维护黑名单（Redis 存已吊销的 jti）；或换 secret 全体失效。

**Q: token 放哪安全？**
→ 服务端渲染放 HttpOnly Cookie（防 XSS 读不到）；SPA 放内存不放 localStorage。
localStorage 里的 token 会被 XSS 直接偷走。

**Q: 为什么 secret 不能写死在代码里？**
→ 代码进 git = 公开。secret 泄露后任何人都能签出 admin token。

**Q: 认证和授权能合并成一步吗？**
→ 实现上可以，但逻辑上必须分开：认证失败和授权失败要给不同状态码，
排障时才分得清「没登录」还是「没权限」。

---

## 七、资料

- [jwt.io](https://jwt.io/introduction) —— 交互式看三段结构
- [PyJWT](https://pyjwt.readthedocs.io/) —— 生产用
- [MDN：401](https://developer.mozilla.org/zh-CN/docs/Web/HTTP/Status/401) ｜ [403](https://developer.mozilla.org/zh-CN/docs/Web/HTTP/Status/403)
- [OWASP：RBAC](https://owasp.org/www-community/Access_Control)
