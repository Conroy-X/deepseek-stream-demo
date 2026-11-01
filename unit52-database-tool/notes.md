# Unit 52 笔记 · Database Tool

> 这一章先想到的是「写一个能跑的 SQL 工具」。
> 写完发现真正的教学价值在「**只读**是什么」—— 是连接属性，不是动作属性。

## 本次做了什么

- 写 `server.py`：`MCPServer` 暴露 3 个工具 + 内部 `_try_write_on_ro` / `_try_write_on_rw` 对照函数。
- 写 `client.py`：`MCPClient` 封装（stdio）。
- 写 `main.py`：6 接口 + 56 自检，0 失败，连跑两次一致。
- 写 `static/index.html`：SQL 实验台 + 攻击样例 + ro/rw 对照 + MCP 子进程握手按钮。

## 一、只读不是动作，是连接的属性

最常见的「只读 SQL 工具」写法是：

```python
if not sql.upper().startswith("SELECT"):
    return "只允许 SELECT"
```

很多人就敢说这是「只读」。但它能被这些绕过：

| 攻击 | 为什么通过词法 | 为什么不该通过 |
| --- | --- | --- |
| `SELECT 1; DROP TABLE x` | startswith SELECT | 第二句会真删表 |
| `SELECT 1 --\n; DROP TABLE x` | startswith SELECT | 注释里藏分号 |
| `SELECT * FROM x WHERE 1=1; DELETE FROM y` | startswith SELECT | 多语句 |
| `SELECT load_extension(...)` | startswith SELECT | 可加载恶意 .so |

正确的做法是**用只读连接**：

```python
sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
```

这种连接在 SQLite 驱动层就拒写。实测：

```
ro.execute("INSERT INTO authors VALUES ('a-x', ...)")
→ sqlite3.OperationalError: attempt to write a readonly database
```

而同样一句在写连接里直接成功。这就是「**词法放行了，连接拦死了**」的直接证据。

## 二、三层防御：词法 + 多语句 + 连接

本单元的 `run_readonly_sql` 三层叠加：

1. **词法白名单**：剥注释后，第一词必须是 SELECT（PRAGMA/ATTACH/UPDATE 等全部拒）。
2. **多语句拆分**：剥注释后还含分号就拒。
3. **真只读连接**：即使词法和多语句都过了，到驱动层写操作还是炸。

第三层是兜底。即便你前两层写错了（比如忘了剥注释），驱动层还能再救一次。
**安全设计上，永远不要假设你前面写对了。**

## 三、`sqlite3.execute` 单语句这件事

SQLite Python binding 的 `cursor.execute(sql)` 严格要求**单条语句**。
传 `SELECT 1; DROP TABLE x` 会抛 `ProgrammingError: You can only execute one statement at a time.`

这是好事 —— 等于多了一层保险。但这层是 SQLite 客户端库的行为，**不是 SQLite 引擎**，
换成 `executescript()` 它就没了。如果你做了多语句检查，那调用 `execute` 就够；
如果你不放心，永远先用 `executescript` 测试再退回 `execute`。

## 四、强制 LIMIT：防全表扫

`run_readonly_sql` 强制 LIMIT：

```python
final = _enforce_limit(body, default=100, max_limit=1000)
```

- 没有 LIMIT → 自动补 100
- 有 LIMIT 但 > 1000 → 改写成 1000

为什么必须强制：模型生成的 SQL 经常没有 LIMIT，会一次拉几百万行。
工具层强制 LIMIT 是对模型「不可控输出」的兜底。

## 五、攻击样例：8 类全实测

| 类别 | 样例 | 拦截层 |
| --- | --- | --- |
| 注入 | `SELECT 1; DROP TABLE articles` | 多语句检查 |
| 注入 | `SELECT * FROM articles -- ; DROP TABLE articles` | 剥注释后正常 SELECT |
| 注入 | `/* SELECT */ SELECT 1` | 剥注释后正常 SELECT |
| 注入 | `PRAGMA database_list` | 词法白名单 |
| 写 | `UPDATE authors SET ...` | 词法白名单 |
| 写 | `DELETE FROM articles WHERE id='p-101'` | 词法白名单 |
| 写 | `INSERT INTO authors VALUES (...)` | 词法白名单 |
| 写 | `ATTACH DATABASE '/tmp/x.db' AS x` | 词法白名单 |

合法对照：`SELECT id, title FROM articles LIMIT 3` 跑通返回 3 行。

## 六、为什么不用 `sqlite3.complete_statement`

它要求 SQL 末尾有分号才返回 True。本单元的工具签名 `run_readonly_sql(sql: str)` 不要求分号，
用户（Agent）给的 SQL 通常不带分号。如果用它当闸，会把所有合法查询都拦掉。

它的真正用途是：**判断一段文本是不是「完整的 SQL」**，用于像 SQL REPL 这种「等用户输入完」的场景。
不是「只读闸」。

## 完成标准对照

| 标准 | 达成情况 |
| --- | --- |
| 说清词法 vs 连接 | 实测 ro/rw 对照，INSERT 在只读连接抛 readonly，写连接成功 |
| 8 类攻击样例 | 全部被三层之一拦下 |
| 强制 LIMIT | 测内部函数：无→100、99999→1000、5→保持 |
| 离线可自检 | 56 条断言，0 失败，连跑两次一致 |

## 一句话总结

**词法是契约，连接是兜底 —— 你写错了词法，只读连接救你一命；你写错了连接，没人能救你。**

## 面试追问

**Q：用 `startswith("SELECT")` 拦，为什么不安全？**
A：因为 SQL 多语句。`SELECT 1; DROP TABLE x` 字面上以 SELECT 开头，但执行时会跑两条，
第二条是写操作。要拦必须做语句拆分，或者直接用只读连接 —— 后者更稳，因为驱动层就拒。

**Q：为什么不用 PostgreSQL 的 GRANT 而要靠连接？**
A：GRANT 是应用层权限模型，要 DBA 配。SQLite 没有用户系统，靠 `mode=ro` 是最接近的等价物。
PostgreSQL 生产里两者都要：连接用户本身设只读，应用层再用 GRANT 限制表级访问。
连接是地基，权限是上层。