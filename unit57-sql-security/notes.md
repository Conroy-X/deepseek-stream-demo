# Unit 57 笔记 · SQL 安全四道闸

> unit52 讲了两条（只读连接 + 词法）。这一章扩到四条，并加上 OWASP 8 条攻击实测。

## 本次做了什么

- 写 `security.py`：四道闸（gate1_readonly / gate2_lexical / gate3_enforce_limit / gate4_row_level）+ OWASP 8 攻击 + 3 合法 + 跨租户测试。
- 写 `main.py`：5 接口 + 46 自检，0 失败，连跑两次一致。

## 一、四道闸各自负责什么

| 闸 | 防什么 | 实现 |
| --- | --- | --- |
| 1 只读账号 | 词法写错时写操作穿透 | `sqlite3.connect("file:...?mode=ro", uri=True)` |
| 2 词法+多语句+注释 | 多语句注入、注释偷渡、PRAGMA/ATTACH | 剥注释 + 拒多语句 + 白名单 SELECT |
| 3 强制 LIMIT | 全表扫、OOM | 无 LIMIT 补 100、>1000 改 1000 |
| 4 行级权限 | 跨租户数据泄漏 | 自动注入 `WHERE tenant_id = ?` |

漏任何一道都会被某种攻击穿透。

## 二、行级权限必须自动注入

模型不会每次都加 `WHERE tenant_id=?`。即使你在 prompt 里写「你是 t-acme 租户，只能查 t-acme 的数据」，
模型也会忘 —— 尤其是复杂 JOIN 时。

正确做法：工具层拿 tenant_id（从认证上下文，不从模型参数），自动注入 WHERE。

本单元的 `gate4_row_level(sql, tenant_id)`：
- 有 WHERE → 追加 `AND tenant_id = ?`
- 没 WHERE → 加 `WHERE tenant_id = ?`

真实生产用 PostgreSQL RLS（Row Level Security）—— 数据库层强制，应用层改写都不需要。

## 三、注释样例不是「被拦」，是「防御成功」

```
SELECT * FROM orders -- ; DROP TABLE orders
```

剥注释后剩下 `SELECT * FROM orders`，正常执行返回行。DROP 没执行。

我第一版断言写「被拦」，跑挂了。实际上这是「剥注释成功防御」—— DROP 在注释里，根本没进执行。
正确的断言是「表还在」（count 仍是 6），而不是「blocked_by 非空」。

## 四、OWASP 8 条攻击分类

| 攻击 | 哪道闸拦 |
| --- | --- |
| 多语句 DROP / DELETE / UPDATE | 闸 2 多语句检测 |
| 行注释偷渡 | 闸 2 剥注释后正常 SELECT（DROP 没执行） |
| 块注释偷渡 | 同上 |
| PRAGMA 摸底 | 闸 2 词法白名单 |
| ATTACH 挂载 | 闸 2 词法白名单 |
| UNION 拉数据 | 闸 2 通过（合法 SELECT），但行级权限限制只看到自己租户 |

6 条真攻击被拦，2 条注释样例防御成功（DROP 没执行），全部安全。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 四道闸各司其职 | 4 个 gate 函数 + 各自断言 |
| 行级权限自动注入 | gate4 改写 SQL，跨租户实测 A 看不到 G |
| 8 条 OWASP 全安全 | 6 条被拦 + 2 条注释防御成功 |
| 3 条合法不误杀 | JOIN/WHERE/聚合全通过返回行 |
| 离线可自检 | 46 条断言，0 失败，连跑两次一致 |

## 一句话总结

**安全不是一道闸的事 —— 漏一道就被某种攻击穿透。四道叠起来才稳。**

## 面试追问

**Q：为什么不用 PostgreSQL RLS 而要靠应用层改写？**
A：本单元用 SQLite 演示原理，SQLite 没有 RLS。生产里 PostgreSQL 用 RLS 是最优解 ——
数据库层强制，应用层改写都不需要，模型生成的 SQL 自然被限制在租户范围内。
应用层改写是 SQLite 场景下的等价物，原理一样：tenant_id 不能来自模型，必须来自认证上下文。

**Q：UNION 攻击怎么防？**
A：UNION 本身是合法 SELECT，词法层不拦。但行级权限会注入 `WHERE tenant_id = ?`，
UNION 的第一段被限制在本租户。第二段 `SELECT name FROM tenants` 没有 tenant_id 列，
会报错 —— 这等于自动拦了。真实生产里还要加「禁止跨表 UNION」的规则。