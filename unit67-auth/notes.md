# Unit 67 笔记 · Auth

> 边做边填。模拟面试的复习材料。

## 本次做了什么

- 手写最小 JWT（HS256 / HMAC-SHA256）：三段结构 + 防篡改 + 过期
- 认证与授权拆成两个函数：authenticate（401）/ authorize（403）
- RBAC 角色权限表 + 额度表

## 核心认知

**认证是「你是谁」，授权是「你能干什么」——两步分开，状态码也分开：401 vs 403。**

| 状态码 | 语义 | 例子 |
| --- | --- | --- |
| 401 | 没认证 | 没带 token / 过期 / 签名错 |
| 403 | 认证了但没权限 | user 想删账号 |

## JWT 三段

```
header . payload . signature
```

- header：算法（HS256）
- payload：业务字段 + exp（过期）
- signature：HMAC(header.payload, secret)，防篡改

**secret 是唯一防线**：泄露 = 任何人能伪造 admin token。

## 我自己的话解释「401 和 403 的区别」

（待填：提示——401 是「不知道你是谁」，403 是「知道你是谁但不许」）

## 我自己的话解释「为什么 token 不能放 localStorage」

（待填：提示——XSS 能读 localStorage，读 cookie 读不到 HttpOnly cookie）

## 完成标准对照

- [ ] 自检全过
- [ ] 没 token → 401；user 访问 admin → 403
- [ ] 篡改 role → 签名不匹配
- [ ] 能解释 JWT 三段

## 一句话总结

（待填）

## 面试追问

1. **「JWT 怎么吊销？」** → 短 TTL + 刷新 token / 黑名单 / 换 secret
2. **「token 放哪？」** → HttpOnly Cookie 或内存，别放 localStorage
3. **「认证授权能合并吗？」** → 逻辑上必须分开，否则分不清「没登录」还是「没权限」
