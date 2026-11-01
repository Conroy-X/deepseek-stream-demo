# Unit 57 · SQL 安全：四道闸

> unit52 讲了「只读连接 vs 词法检查」两条。本单元把它扩成四道：
> 只读账号 / 语句白名单 / 强制 LIMIT / 行级权限。
> 6 条真攻击全被拦，2 条注释样例剥注释后正常跑（DROP 没执行），3 条合法复杂 SQL 不误杀。

**完成标准**：能说出四道闸各自负责什么；
能解释行级权限为什么必须由「自动注入 WHERE」而不是「指望模型记住」；
能徒手把 8 条 OWASP 样例分到对应的闸。

## 快速开始

```bash
cd ~/ai-journey/unit57-sql-security
uv sync
uv run uvicorn main:app --port 8057
```

## 自检

```bash
uv run python main.py
# 结果：46 通过 / 0 失败
```

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `security.py` | 四道闸 + OWASP 8 攻击 + 3 合法 + 跨租户测试 |
| `main.py` | 5 接口 + 46 自检 |
| `static/index.html` | 四闸面板 + SQL 实验台 + 跨租户 + OWASP 表 |

## 实测

| 项目 | 结果 |
| --- | --- |
| 闸 1 只读连接 INSERT | 拒，`attempt to write a readonly database` |
| 闸 2 多语句 / PRAGMA / ATTACH / UPDATE | 全拦 |
| 闸 2 行注释 / 块注释 | 剥注释后正常 SELECT，DROP 没执行（表还在 6 行） |
| 闸 3 无 LIMIT | 自动补 100 |
| 闸 3 LIMIT 99999 | 改写成 1000 |
| 闸 4 行级权限 | A 租户只看到 2 笔（t-acme），G 租户只看到 2 笔（t-globex） |
| 跨租户 | A 看不到 G 的订单（`acme_sees_globex = false`） |
| 合法 JOIN / WHERE / 聚合 | 全部通过，返回行 |

## 常见坑

1. **行级权限指望模型记住**。模型不会每次都加 `WHERE tenant_id=?`，必须由工具层自动注入。
   真实生产用 PostgreSQL RLS 或视图，本单元用 SQL 改写演示原理。
2. **注释样例当成「被拦」**。剥注释后是合法 SELECT，DROP 没执行 —— 这是「防御成功」不是「没被拦」。
   断言应该是「表还在」而不是「blocked_by 非空」。
3. **四道闸漏一道**。漏闸 1，词法写错时写操作直接穿透；漏闸 4，A 租户能查到 B 租户的数据。
   每道闸防一类问题，叠起来才安全。

## 下一单元

Unit 58 · Error Recovery —— SQL 报错喂回模型自动修正，最多 3 次。