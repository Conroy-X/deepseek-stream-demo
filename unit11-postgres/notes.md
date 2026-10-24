# Unit 11 笔记 · PostgreSQL：把对话真正存进数据库

> 边做边填。模拟面试的复习材料。
>
> 状态：三张表 + 外键级联 + 复合索引全部实测通过。

## 本次做了什么

- 三张表：`users` / `conversations` / `messages`，靠外键串起来
- 外键全部带 `ON DELETE CASCADE`：删会话，消息自动没；删用户，会话自动没
- 复合索引 `(conversation_id, created_at)`，并用 `EXPLAIN ANALYZE` 验证真的被用上
- 一个能看到"数据真写进去了"的数据面板（`static/index.html`）

## 为什么这个单元非做不可

前几个单元的存储是"假的"：

| 单元 | 存储 | 进程重启后 |
| --- | --- | --- |
| Unit 9 | SQLite 文件 | 还在，但并发写会锁库 |
| Unit 10 | 内存 dict | **全没了** |
| Unit 11 | PostgreSQL | 还在，且能扛并发 |

SQLite 不是不能用，它是"单机单进程友好"。一旦你要跑 `uvicorn --workers 4`，
多个进程抢同一个 SQLite 文件，写入会直接 `database is locked`。
PG 是独立进程 + 自己的连接池，天生支持多客户端并发写。

## 建表时踩的三个点

### 1. 时间戳交给数据库生成

```python
created_at: datetime = Field(
    sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now())
)
```

`server_default=func.now()` 的意思是"这个值由 PG 在 INSERT 时自己填"，
应用代码压根不传。好处：多台应用服务器时区不一致、或者某台机器时钟漂了，
写进去的时间仍然是数据库的时钟，不会互相打架。

另外一定要用 `DateTime(timezone=True)`（对应 PG 的 `timestamptz`）。
用 naive 的 `timestamp` 存，跨时区一换算就错。

### 2. 级联删除要写在数据库层，不要写在 Python 层

```python
ForeignKey("conversations.id", ondelete="CASCADE")
```

如果我写成"在删除会话的接口里先 `DELETE FROM messages WHERE ...` 再删会话"，
那么只要有**任何一条**别的代码路径删了会话（后台任务、手工 SQL、另一个接口），
就会留下孤儿消息。

把规则交给数据库，就是"无论谁删、从哪删，都保证一致"。这叫**数据完整性由 DB 兜底**。

实测验证过两层级联：
- 删 `conversation` → 它的 `messages` 清零
- 删 `user` → 他的 `conversations` 清零

### 3. `create_all()` 不会改已有表

`SQLModel.metadata.create_all()` 只做一件事：**建缺的表**。
表已经存在就跳过，字段改了、删了、重命名了，它一律不管。

所以：
- 学习阶段、表还很新 → `create_all()` 够用，跑得快
- 一旦有真数据、要改结构 → 必须上 **Alembic** 迁移

Alembic 的最小用法（等真需要时再上）：

```bash
uv add alembic
alembic init migrations                    # 生成目录
# 改 migrations/env.py，把 target_metadata 指向 SQLModel.metadata
alembic revision --autogenerate -m "add tokens to messages"
alembic upgrade head                       # 应用
```

面试说法：**`create_all` 是幂等建表，不是迁移工具；生产上改 schema 只有 Alembic（或同类工具）一条路。**

## 索引：为什么是 `(conversation_id, created_at)` 这个顺序

最常用的查询是"拉某个会话的消息，按时间排"：

```sql
SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at LIMIT 50
```

复合索引遵守**最左前缀**原则，所以 `(conversation_id, created_at)` 能同时服务两件事：

1. `WHERE conversation_id = ?` 用索引定位（等于用第一列）
2. `ORDER BY created_at` **直接顺带有序**（第二列在索引里已经是排好的）

一条索引干了两件事，PG 不需要额外 `Sort` 一步。

反过来写成 `(created_at, conversation_id)` 就废了：查某个会话时第一列用不上，
只能全表扫。

### 怎么看索引有没有生效

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT * FROM messages WHERE conversation_id = 1 ORDER BY created_at LIMIT 50;
```

判断标准只有两条：

| 看到什么 | 含义 |
| --- | --- |
| `Index Scan using ix_messages_conversation_created` | ✅ 走索引了 |
| `Seq Scan` + `Sort` | ❌ 全表扫 + 额外排序 |

小表上 PG 可能**故意不走索引**（数据太少，全表扫更快，优化器算过账）。
所以这条验证要等 `messages` 有几千行才有意义 —— 这是新手最容易误判的地方：
"我建了索引怎么没生效？" 因为表里只有 3 行。

另外 `ANALYZE` 会真的执行查询（带统计），`EXPLAIN` 不带 `ANALYZE` 只估算不执行。
在写操作上直接跑 `EXPLAIN ANALYZE` 会真的改数据，要小心。

## 连接池：`pool_pre_ping` 是必选项

```python
create_engine(url, pool_pre_ping=True, pool_recycle=1800)
```

- `pool_pre_ping`：每次从池里取连接前先 `SELECT 1` 探一下。数据库重启过、
  或者连接被防火墙静默掐断时，池里的死连接会被自动丢弃重建。
  不设这个，第一个请求必然报 `server closed the connection unexpectedly`。
- `pool_recycle=1800`：连接活过 30 分钟就回收。很多云数据库（含 PG 的托管版）
  会在 30 分钟左右掐空闲连接，不回收就是定时炸弹。

## 本机实际情况（和计划不一样的地方）

计划里写的是 `docker run ... postgres:16 -p 5432:5432`，但**这台 WSL 上已经装了
PostgreSQL 17 并由 systemd 托管**，5432 被它占着，Docker 起不来
（`failed to bind host port 0.0.0.0:5432: address already in use`）。

两种处理方式，本单元选了前者：

**方案 A（当前采用）：直接用系统的 PG 17**
```bash
DATABASE_URL=postgresql+psycopg://rik@/ai_journey?host=/var/run/postgresql
```
走 Unix socket + peer 认证，不需要密码。前提是系统用户 `rik` 在 PG 里有同名角色。
比再跑一个容器省一大块内存（这台机器只有 2C2G）。

**方案 B：Docker 换端口**
```bash
docker compose up -d          # compose 里映射的是 5433:5432
DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5433/ai_journey
```
彻底隔离，不碰系统 PG。要换一台没装 PG 的机器时用这个。

> 面试可以聊的点：一个"用 Docker 起库"的教程，在一台已经装了 PG 的机器上直接照抄会挂。
> 排查顺序应该是 `ss -ltnp | grep 5432` → 看是谁占了 → 决定"复用"还是"换端口"，
> 而不是上来就 `docker rm` 别人的服务。

## 完成标准对照

- [x] 三张表建好，`/health` 能看到表名
- [x] 重启后端 + 重启数据库容器后，历史对话依然在
- [x] 删会话时消息级联清空
- [x] 删用户时会话级联清空
- [x] 一次 HTTP 写入后，**换一个全新连接**仍能读到（真持久化，不是 Session 缓存）
- [x] `EXPLAIN` 确认走了复合索引
- [x] 重复 email 返回 409，不存在的 user 返回 404

## 一句话总结

"存进数据库"不是把 `dict` 换成 `session.add()` 就完事，真正的分水岭是三件事：
**外键让数据库替你保证一致性，索引让查询不用全表扫，迁移工具让你敢改表。**

## 面试追问

1. **「为什么用 PG 不用 SQLite？」**
   → SQLite 是单文件、库级写锁，多进程写会 `database is locked`；
   PG 独立进程 + 行级锁 + MVCC，支持并发写。另外 PG 有丰富类型（jsonb、数组、pgvector），
   后面 Unit 15-30 的向量检索直接靠它。

2. **「删会话怎么保证消息不残留？」**
   → 外键 `ON DELETE CASCADE`，规则写在数据库层。应用层手写删除会漏路径，
   数据库层保证"无论谁删都一致"。

3. **「为什么索引是 (conversation_id, created_at) 而不是反过来？」**
   → 最左前缀。查询条件是 `conversation_id = ?`，第一列必须是它才能定位；
   第二列 `created_at` 让 `ORDER BY` 直接免掉排序。

4. **「建了索引但 EXPLAIN 显示 Seq Scan，怎么回事？」**
   → 表太小，优化器算完全表扫更便宜。数据量上来了才会走索引。
   另外也可能是查询条件没命中索引的第一列。

5. **「`create_all` 能改表结构吗？」**
   → 不能。它只建缺失的表。改字段必须 Alembic 迁移，否则生产上会静默不一致。
