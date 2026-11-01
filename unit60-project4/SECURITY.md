# 安全

## 威胁模型

| 威胁 | 攻击样例 | 防线 | 实测结果 |
| --- | --- | --- | --- |
| 多语句注入 | `SELECT 1; DROP TABLE orders` | 闸 2 多语句检测 | 被拦 |
| 写操作 | `UPDATE orders SET amount=0` | 闸 2 词法白名单 | 被拦 |
| PRAGMA 摸底 | `PRAGMA database_list` | 闸 2 词法白名单 | 被拦 |
| ATTACH 挂载 | `ATTACH DATABASE '/tmp/x' AS x` | 闸 2 词法白名单 | 被拦 |
| 行注释偷渡 | `SELECT * FROM orders -- ; DROP TABLE` | 闸 2 剥注释 | 剥后正常 SELECT，DROP 没执行 |
| 跨租户泄漏 | A 租户查 `SELECT * FROM orders` | 闸 4 行级权限 | A 只看到自己的 2 笔 |
| 全表扫 | `SELECT * FROM orders`（无 LIMIT） | 闸 3 强制 LIMIT | 自动补 LIMIT 100 |
| 词法写错穿透 | 假设词法漏了写操作 | 闸 1 只读连接 | 驱动层拒 `readonly database` |

## 四道闸

1. **只读账号**：`sqlite3.connect("file:...?mode=ro", uri=True)` —— 写操作在驱动层炸
2. **词法 + 多语句 + 注释**：剥注释 → 拒多语句 → 只允许 SELECT
3. **强制 LIMIT**：无 LIMIT 补 100；> 1000 改 1000
4. **行级权限**：自动注入 `WHERE tenant_id = ?`，tenant_id 来自认证上下文（不从模型参数）

## 已知局限

- SQLite 没有 PostgreSQL RLS，行级权限靠 SQL 改写（应用层）
- 没有做 UNION 跨表攻击的专门防护（行级权限间接限制了）
- 错误重试的 fix_sql 是简化版（前缀匹配），生产用编辑距离