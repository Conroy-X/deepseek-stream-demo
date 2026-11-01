# Unit 56 笔记 · NL → SQL

> 「让模型写 SQL」最难的不是 prompt，是 schema 怎么给。

## 本次做了什么

- 写 `schema.py`：full / compact / relevant 三种表示 + 字符统计 + 40 表合成版。
- 写 `dialect.py`：4 方言转换（sqlite / postgres / mysql / sqlserver）。
- 写 `agent.py`：OfflineModel 规则生成 SQL（含 JOIN 与列名限定）。
- 写 `main.py` / `server.py` / `client.py`：57 自检，0 失败，连跑两次一致。
- Agent 调用走的是 MCP：5 次（list_tables + 3×describe_table + run_readonly_sql）。

## 一、三种 schema 表示，字符数是硬指标

实测同一问题「上月华东区销售额前 3 的客户」：

| 模式 | 字符 | est token | 适用场景 |
| --- | --- | --- | --- |
| full DDL | 928 | ~232 | 表少、写新 DDL |
| compact 精简 | 465 | ~175 | **默认**。信息够、字符少一半 |
| relevant 预筛 | 382 | ~145 | 表多时唯一可行 |

40 表规模下：full 是 4871，只取 2 表精简成 191，比值 **25.5 倍**。

这是 NL2SQL 一定要做 schema 预筛的直接原因 —— 不只是省钱，是上下文窗口会被吵掉。

## 二、方言：四个语族

| 方言 | 前 N 行 | 大小写不敏感 | 标识符 |
| --- | --- | --- | --- |
| sqlite | LIMIT 10 | LOWER(c) LIKE | "x" |
| postgres | LIMIT 10 | ILIKE | "x" |
| mysql | LIMIT  complexity10 | LOWER(c) LIKE | `x` |
| sqlserver | TOP 10 | LOWER(c) LIKE | [x] |

`sqlserver` 的 `TOP` 必须在 `DISTINCT` 之后 —— 测试里专门断言 `SELECT TOP 10 ...` 与 `SELECT DISTINCT TOP 10 ...` 都正确。

自检里还断言 sqlite / pg / mysql 的输出里不出现 TOP，sqlserver 的输出里不出现 LIMIT —— 简单但能防止转换函数退化成空操作。

## 三、列名歧义：不限定就报错

四张表里：`id` 都有，`name` 在 customers/products 里有，`region` 在 customers/orders 里有。

裸写 `SELECT id FROM customers JOIN orders ON customers.id = orders.customer_id` →
SQLite 直接抛 `ambiguous column name: id`。

OfflineModel 在生成 SQL 时，所有列都加表别名前缀（`c.id = o.customer_id` 还是依赖 JOIN 条件）。
这就是为什么 schema 必须把表的别名也告诉模型 —— 本单元在 schema 里直接挂表简称。

## 四、端到端：5 次 MCP 调用

1. `list_tables` → 拿到 4 张表名
2. 对每张表 `describe_table` → 拿到列名/类型/注释（共 4 次里 3 次有外部键关联）
3. `run_readonly_sql` → 执行生成的 SQL，拿到结果

总共 5 次 MCP 调用，每次都起一次 harmony握手 / 子进程内部调用 —— Agent 的工具循环逻辑在 agent.py 里实现。

## 五、无法回答 ≠ 瞎编

OfflineModel 在关键词不命中任何表/列时返回「无法回答」且**不生成 SQL**。
历史上 LLM 在没有合理 schema 时会硬编造表名「products」、列名「sales」——
在真实生产库里这种 SQL 跑出来全是空结果，比报错还可怕，因为它给人「查到了」的错觉。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 三种 schema 表示的字符数 | full 928 / compact 465 / relevant 382 |
| 4 方言转换 | sqlite/pg/mysql LIMIT 10，sqlserver TOP 10，4 条断言 |
| 列名歧义报错实测 | `ambiguous column name` 抓到，自检过 |
| 生成 JOIN 跨表查准 | 王强 6742 元 / 李娜 6664 元 / 张伟 5514 元 |
| 离线可自检 | 57 条断言，0 失败，连跑两次一致 |

## 一句话总结

**给模型多少 schema，比给模型多少 prompt 更重要 —— 一份字符适中的精简 schema，是 NL2SQL 准确率的第一道分水岭。

## 面试追问

**Q：full DDL 太大，relevant 又需要预筛，怎么选？**
A：先看表数。表数 < 5 用 compact；5–20 必须做相关表预筛，预筛可以用关键词命中或上一轮的 LLM 生成「需要的表清单」；> 20 表必须做向量检索派生候选表，且仍要让模型自己加 JOIN 桥表。

**Q：方言适配怎么做才不污染 prompt？**
A：把方言变成转换层，不进 prompt。所有 SQL 在「统一中间表示」上生成，最后一步转成目标方言。本单元就是这么做的：模型只看标准 SQL，dialect.convert() 转换。