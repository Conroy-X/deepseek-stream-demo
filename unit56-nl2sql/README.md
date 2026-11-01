# Unit 56 · NL → SQL（schema 注入与方言适配）

> 同一个问题「上月华东区销售额前 3 的客户」，schema 给得不准 SQL 就写错；
> 方言给错 SQL 就跑不起来。这一章把这两件事都拿到桌面上量一遍。

**完成标准**：能说出 full / compact / relevant 三种 schema 表示的字符数与适用场景；
能解释「列名歧义 + ambiguous column」这条错误链；能徒手写出 4 种方言下的「前 10 行」。

## 快速开始

```bash
cd ~/ai-journey/unit56-nl2sql
uv sync
uv run uvicorn main:app --port 8056
```

打开 http://127.0.0.1:8056/ 。左侧三种 schema 表示的字符数对照；
右侧方言表，输入一句 SQL 看它在四种方言里成什么样。

## 自检

```bash
uv run python main.py
# 结果：57 通过 / 0 失败
```

四组：三种 schema 字符数、方言转换、列名歧义、生成 JOIN。

## 这个目录里有什么

| 文件 | 作用 |
| --- | --- |
| `db.py` | 建 `data/shop.db`（4 张表 + 索引 + 40 表规模合成 schema） |
| `schema.py` | 三种表示 + char_stats + synthetic_wide_schema |
| `dialect.py` | sqlite / postgres / mysql / sqlserver 四方言转换 |
| `agent.py` | `OfflineModel` 规则生成 SQL，含 JOIN + 列名限定 |
| `server.py` | MCPServer：`list_tables` / `describe_table` / `run_readonly_sql` |
| `client.py` | MCPClient 封装 |
| `main.py` | 6 接口 + 57 自检 |
| `static/index.html` | 对照台 + 方言转换器 |

## 实测

| 项目 | 结果 |
| --- | --- |
| full DDL 表示 | 928 字符 / ~232 tokens |
| compact 精简表示 | 465 字符 / ~175 tokens —— **小一半** |
| relevant 按问题预筛 | 382 字符 / ~145 tokens —— 比 full 小 60% |
| 同一题「前 3」end-to-end | 第 1 名王强 6742 元、第 2 李娜 6664 元、第 3 张伟 5514 元 |
| 方言转换 | sqlite/postgres/mysql 用 `LIMIT 10`，sqlserver 用 `TOP 10` |
| DISTINCT + LIMIT | sqlserver 的顺序 `SELECT DISTINCT TOP 10` 正确 |
| 大小写不敏感 | postgres 用 `ILIKE`，sqlite 退化成 `LOWER(...) LIKE` |
| 列名歧义 | `id` 在 4 张表里都有，`name` `region` 在 2 张表里都有 |
| 不限定列名的 JOIN | 直接报 `ambiguous column name` |
| 多轮内部用 MCP 调用 | 5 次（list_tables + 3×describe_table + run_readonly_sql） |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | server / db_table_count / dialect_count |
| GET | `/api/schema/compare?q=` | 三种表示 + 40 表规模对照 |
| GET | `/api/dialect?sql=&n=` | 转成 4 种方言 |
| POST | `/api/ask` | 端到端：问题 → schema → SQL → 执行 → 答案 |
| GET | `/api/trace` | 上一次端到端的轨迹 |
| GET | `/` | 页面 |

## 常见坑

1. ** Gould 仅限一张表时把 `id` 不带表别名**。多表 JOIN 必须 `c.id` / `o.id`，
   否则 SQLite 报 `ambiguous column name: id`。本单元实测踩过这条错，自检里有专门一条断言。
2. **sqlserver 用 LIMIT**。LIMIT 是 pg/mysql/sqlite 关键字，sqlserver 用 TOP。本单元把 4 种方言都做出来。
3. **方言转换只看「前 N 行」**。其他差异（窗口函数、JSON、序列化）不在本单元范围。
4. **schema 给全量**会让模型选择很多。40 表规模下 full 表示 4871 字符，relevant 只取 2 表 191 字符，
   比值 **25.5 倍**。这就是为什么 NL2SQL 一定要做 schema 预筛。
5. **「无法回答」不等于「瞎编 SQL」**。当问题关键词不命中任何表/列，OfflineModel 返回「无法回答」且不生成 SQL —— 这条断言很重要，否则可能产出幻觉。

## 验收清单

- [ ] `python main.py` 输出 `结果：57 通过 / 0 失败`
- [ ] schema 对照台显示三档字符数，full > compact > relevant
- [ ] 方言转换器输入 `SELECT id FROM orders`，sqlserver 输出 `SELECT TOP 10 id FROM orders`
- [ ] /api/ask 能跑通端到端，答案带具体数字
- [ ] 能不看文档说出三种 schema 表示的适用场景

## 下一单元

Unit 57 · SQL 安全 —— 把「只读不是一个动作的区别」展开成四道闸：只读账号、语句白名单、强制 LIMIT、行级权限。