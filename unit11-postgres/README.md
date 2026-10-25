# Unit 11 · PostgreSQL：把对话真正存进数据库

> 一句话：前面几个单元的对话都存在内存或 SQLite 里，这个单元把「存」这件事做成真的——
> 换成 PostgreSQL，用**三张表 + 外键 + 索引**，并用查询计划证明它真的生效。

**本单元用 8011 端口**（不要用 8000，原因见第 5 节排错手册第 1 条）。

---

## 0. 先搞清楚这个单元在讲什么

### 0.1 你在整条路线上的位置

| 单元 | 数据存在哪 | 能做什么 | 缺什么 |
| --- | --- | --- | --- |
| Unit 9 多轮对话 | SQLite 单文件 | 会话 + 消息 + 多轮上下文 | 并发写弱、没有用户/权限概念 |
| Unit 10 上下文管理 | 内存 + SQLite | 超长历史裁剪 | 还是单机文件，撑不住多进程 |
| **Unit 11（本单元）** | **PostgreSQL 服务** | **三张表 + 级联 + 索引** | 还没有登录鉴权（Unit 12 补） |

### 0.2 一句话目标

把「用户 → 会话 → 消息」这层关系落到 PostgreSQL 的三张表里，并**证明**三件事：

1. **重启不丢**：换一个全新连接（甚至重启服务/容器）还能读到；
2. **删父带子**：删会话，消息自动消失；删用户，会话自动消失——由数据库保证，不靠代码自觉；
3. **查得动**：按 `(conversation_id, created_at)` 查消息时，索引真的被用上（看查询计划）。

### 0.3 为什么值得单独花一个单元

- **一致性不能靠"记得写"**：SQLite 时代你要在代码里手动删消息，漏一行就留一堆孤儿数据；外键 + `ON DELETE CASCADE` 是数据库层强制的。
- **索引不是建了就生效**：只有几行数据时 PostgreSQL 会故意走全表扫；统计信息过期时也会无视索引。这个单元让你**亲手看到**这两种情况。
- **这两件事是后端面试的硬通货**：外键/级联、复合索引顺序、`EXPLAIN` 读法。

### 0.4 学完你能自己回答

- 连接串 `postgresql+psycopg://rik@/ai_journey?host=/var/run/postgresql` 每一段分别是什么？
- `ON DELETE CASCADE` 是谁在执行？应用层删了几次？
- 为什么联合索引是 `(conversation_id, created_at)` 而不是反过来？
- `Seq Scan` / `Index Scan` / `Bitmap Index Scan` 有什么区别，分别什么时候出现？

---

## 1. 背景概念（照着这张表过一遍就够，别陷进去）

| 概念 | 一句话解释 | 在本单元哪里能看到 |
| --- | --- | --- |
| 数据库服务 vs 数据库 | PostgreSQL 是**常驻进程**，`ai_journey` 只是它内部的一个库 | `pg_isready`、`psql -d ai_journey` |
| 连接串 | `驱动://用户@/库名?host=socket路径`，四个部分缺一不可 | `db.py` 的 `DATABASE_URL` |
| Unix socket 认证 | 本机走 socket + peer（系统用户 `rik` 对上就免密），不经过 TCP | `.env` 里的 `?host=/var/run/postgresql` |
| 主键 PK | 每行的唯一身份证，通常自增整数 | `users.id` / `conversations.id` / `messages.id` |
| 外键 FK | 这一行的父记录必须存在；写孤儿数据直接被库拒绝 | `messages.conversation_id` |
| 级联删除 | 父记录被删时，数据库自己删掉所有子记录 | `ondelete="CASCADE"` |
| 复合索引 | 多列合成一个索引，**列顺序决定能不能用上** | `(conversation_id, created_at)` |
| 查询计划 | 数据库告诉你"我打算怎么查"，用 `EXPLAIN ANALYZE` 看 | `GET /debug/explain` |
| 统计信息 | 优化器估成本用的数据，`ANALYZE` 刷新；不新就可能不用索引 | 自检里那句 `ANALYZE messages` |

**为什么索引顺序是 `(conversation_id, created_at)`**：查询永远是「先锁定一个会话，再按时间排序」。
把 `conversation_id` 放前面，索引才能先精确定位到这个会话的那一小段，且段内天然按 `created_at` 有序——
一次索引扫描就能同时完成"过滤"和"排序"。反过来 `(created_at, conversation_id)` 就得先扫所有会话的时间轴，白搭。

### 表结构

```
users                     conversations                  messages
─────                     ─────────────                  ────────
id (PK)                   id (PK)                        id (PK)
email (UNIQUE)      ←──  user_id (FK→users, CASCADE)     conversation_id (FK→conversations, CASCADE)
display_name              title                          role (system/user/assistant)
created_at (tz)           created_at (tz)                content
                          updated_at (tz)                tokens
                                                         created_at (tz)

索引：
  ix_users_email                      UNIQUE     ← email 不能重复
  ix_conversations_user_updated       (user_id, updated_at)      ← 列"我的会话"并按活跃排序
  ix_messages_conversation_created    (conversation_id, created_at) ← 本单元重点：拉一个会话的消息
```

---

## 2. 环境准备（3 分钟）

```bash
cd ~/ai-journey/unit11-postgres

# 1) 确认数据库在跑（本机装的是 PostgreSQL 17）
pg_isready -h /var/run/postgresql
# 期望：/var/run/postgresql:5432 - accepting connections

# 2) 确认库和账号能进去
psql -d ai_journey -c "select version();"
# 期望：PostgreSQL 17.11 ... 一行

# 3) 同步依赖（幂等，可反复跑）
uv sync

# 4) 环境变量（已有 .env 就跳过）
cp .env.example .env
```

`.env` 里唯一重要的是这一行（默认走本机 socket，免密）：

```bash
DATABASE_URL=postgresql+psycopg://rik@/ai_journey?host=/var/run/postgresql
```

---

## 3. 四段动手（10 + 60 + 10 + 30 分钟）

### A 段（10 分钟）：把库跑起来，并看懂"连的到底是哪个库"

```bash
pg_isready -h /var/run/postgresql
psql -d ai_journey -c "\conninfo"
```

`\conninfo` 会打印你实际连上的 socket、用户、库名。**这一步是为了排错时能自证**：
后面任何"连不上/表不存在"，都是这三项里某一项不对。

> 如果本机没装 PostgreSQL，两种替代方案：
> - Docker：`docker compose up -d`（映射到 **5433**，因为本机 5432 已被系统 PG 占用），
>   然后把 `.env` 的 `DATABASE_URL` 换成
>   `postgresql+psycopg://postgres:postgres@127.0.0.1:5433/ai_journey`
> - 原生安装：`sudo apt install postgresql && sudo -u postgres createdb ai_journey`

**这一段要建立的直觉**：数据库是独立进程，你的 FastAPI 只是它的客户端。
所以"重启后端数据还在"是理所当然的——但"重启数据库数据还在"才叫持久化，C 段会验。

---

### B 段（60 分钟）：读懂四个关键点，而不是重写代码

这个单元的代码已经写好了，**你的任务是把下面四处读明白**。每读懂一处，回来打勾。

#### B1. `db.py` 三个 Model —— 字段和约束就是数据字典

```python
class User(SQLModel, table=True):
    email: str = Field(sa_column=Column(String(255), unique=True, nullable=False))
```

看懂三件事：

- `unique=True` 是**数据库层**的唯一约束，不是 Pydantic 的校验 → 所以重复 email 会抛 `IntegrityError`，接口才要 `except` 后返回 409。
- `ForeignKey("users.id", ondelete="CASCADE")` 写在 `Conversation.user_id` 上。
- `Index("ix_messages_conversation_created", "conversation_id", "created_at")` 写在 `Message.__table_args__` 里 → 这是"建索引"的唯一地方。

- [ ] 能说出：唯一约束和 Pydantic 校验的区别是什么

#### B2. `init_db()` 什么时候被调用 —— 为什么"自检通过 ≠ 服务能跑"

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    store.init_db()      # 服务启动时才建表
    yield
```

自检脚本里是**手动**调 `init_db()` 的，所以自检过了不代表 `uvicorn` 启动时也建了表。
这类"自检绿、一跑就 500"的坑，前面几个单元也踩过——记住排查动作：先看 `/health`。

- [ ] 能说出：`lifespan` 和已弃用的 `@app.on_event("startup")` 是什么关系

#### B3. 删会话为什么不用手动删消息

```python
@app.delete("/conversations/{conversation_id}")
def delete_conversation(...):
    session.delete(conv)          # 只删会话本身
    session.commit()              # 消息由数据库 ON DELETE CASCADE 带走
```

验证方式在 D 段和验收清单里：删完去 `psql` 里查 `messages`，一条都不剩。

- [ ] 能说出：如果外键没写 `ondelete="CASCADE"`，删会话会报什么错

#### B4. `touch_conversation()` 为什么要存在

```python
def touch_conversation(session, conversation_id):
    conv = session.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = datetime.now()
```

因为 `GET /conversations` 是按 `updated_at` 倒序排的——不刷新它，聊得最热的会话会沉到列表底下。
这是"冗余字段换查询效率"的典型做法（另一条路是每次 `MAX(created_at)` 子查询，更贵）。

- [ ] 能说出：`updated_at` 是业务字段还是数据库自动维护的（提示：看代码里谁在写它）

---

### C 段（10 分钟）：用 psql 亲眼看到数据真的进去了

先起服务（**8011**，不是 8000）：

```bash
cd ~/ai-journey/unit11-postgres
uv run uvicorn main:app --reload --port 8011
```

另开一个终端，走一遍最小链路：

```bash
# 1) 建用户
curl -s -X POST http://127.0.0.1:8011/users \
  -H 'Content-Type: application/json' \
  -d '{"email":"me@example.com","display_name":"我"}'
# 期望：{"id":1,"email":"me@example.com"}

# 2) 建会话（user_id 用上一步返回的 id）
curl -s -X POST http://127.0.0.1:8011/conversations \
  -H 'Content-Type: application/json' \
  -d '{"user_id":1,"title":"第一段对话"}'
# 期望：{"id":1,"title":"第一段对话","user_id":1}

# 3) 写一条消息
curl -s -X POST http://127.0.0.1:8011/conversations/1/messages \
  -H 'Content-Type: application/json' \
  -d '{"role":"user","content":"你好，这是第一句话","tokens":6}'
# 期望：{"id":1,"created_at":"2026-10-25T16:17:16.370423+08:00"}
```

然后**绕开你的代码**，直接问数据库：

```bash
psql -d ai_journey -c "\dt"                              # 看到 users / conversations / messages
psql -d ai_journey -c "select id,email from users;"
psql -d ai_journey -c "select id,conversation_id,role,left(content,20) from messages;"
psql -d ai_journey -c "\d messages"                      # 看索引和约束
```

最后一步是重点：`\d messages` 的 `Indexes:` 里应该出现 `ix_messages_conversation_created`。
**能在 psql 里看到，才叫"真的存进去了"**——接口返回 200 只证明代码没报错。

---

### D 段（30 分钟，选做但强烈建议）：让索引生效，并看懂为什么

```bash
curl -s "http://127.0.0.1:8011/debug/explain?conversation_id=1"
```

返回四个关键字段：

| 字段 | 含义 |
| --- | --- |
| `scan_type` | `Seq Scan`（全表扫）/ `Index Scan` / `Bitmap Index Scan` / `Index Only Scan` |
| `uses_index` | 计划里是否出现了我们的索引名 |
| `seq_scan` | 是否全表扫 |
| `needs_sort` | 是否多了一次排序（说明没吃到索引的有序性） |

**现在做个对比实验**（这是本单元最值钱的 30 分钟）。三种形态本机都实测到了，你对号入座：

| 场景 | 实测 `scan_type` | `needs_sort` | 说明 |
| --- | --- | --- | --- |
| 该会话只有 3 条消息、全表也很小 | `Seq Scan` | true | 优化器算过：扫几行比走索引便宜，**这是正确行为** |
| 会话 3 条、但表里被别处垫了 300+ 条 | `Bitmap Index Scan` | true | 索引用来**过滤**，但 bitmap 拿不到有序性，仍要排一次 |
| 该会话自己有 1000 条、表里主要就是它 | `Index Scan using ix_messages_conversation_created` | **false** | 最优形态：索引直接给出有序结果，`LIMIT 50` 扫够 50 行就停 |

如果你灌完数据还是 `Seq Scan`，先执行
`psql -d ai_journey -c "ANALYZE messages"` 再看——
**统计信息过期时优化器会无视新索引**，这是生产里最常见的"索引没生效"原因。

灌数据的命令（用完记得删，别污染库）：

```bash
psql -d ai_journey <<'SQL'
insert into conversations (user_id, title, created_at, updated_at)
select (select id from users order by id limit 1), '压测会话', now(), now();

insert into messages (conversation_id, role, content, tokens, created_at)
select currval('conversations_id_seq'), 'user', '填充 ' || g, 0, now()
from generate_series(1, 1000) g;

analyze messages;
SQL
```

再查一次 `/debug/explain`（`conversation_id` 用上面新建的「压测会话」id，
用 `psql -d ai_journey -c "select id,title from conversations order by id desc limit 3;"` 查），
`scan_type` 应该变成 `Index Scan using ...`、`needs_sort` 变成 `false`。

**为什么最后这种形态最值钱**：查询是 `ORDER BY created_at LIMIT 50`。
`(conversation_id, created_at)` 索引本身就按 `created_at` 有序，于是数据库顺着索引往下读，
**读够 50 行就停**，既不用全表扫，也不用额外排序——这就是"索引列顺序要对"的直接证据。

> 想收工就把灌进去的数据删掉：
> `psql -d ai_journey -c "delete from conversations where title='压测会话';"`

---

## 4. 接口手册：作用 / 预期行为 / 正确调用

| 方法 | 路径 | 作用 | 成功 | 典型失败 |
| --- | --- | --- | --- | --- |
| GET | `/health` | 探活 + 告诉你连没连上库、有哪几张表 | 200 + `{ok, version, database, tables}` | `ok:false` 说明连不上库 |
| GET | `/` | 数据面板（静态页） | 200 HTML | — |
| GET | `/docs` | 自动生成的接口文档，可点着试 | 200 | — |
| POST | `/users` | 建用户 | 200 `{id, email}` | 409 email 已存在；422 email 格式不对 |
| GET | `/users` | 列所有用户 | 200 数组 | — |
| POST | `/conversations` | 建会话 | 200 `{id, title, user_id}` | 404 user 不存在 |
| GET | `/conversations?user_id=1` | 列某用户会话（按活跃倒序） | 200 数组 | 422 少了 `user_id` 这个必填查询参数 |
| DELETE | `/conversations/{id}` | 删会话（消息级联） | 200 `{deleted: id}` | 404 会话不存在 |
| POST | `/conversations/{id}/messages` | 追加消息 | 200 `{id, created_at}` | 404 会话不存在；422 role 不在枚举里 |
| GET | `/conversations/{id}/messages?limit=100` | 拉消息（时间正序） | 200 数组 | 422 limit 超出 1..500 |
| GET | `/debug/explain?conversation_id=1` | 看查询计划 | 200 `{plan, scan_type, uses_index, seq_scan, needs_sort}` | — |

**正确调用的三条铁律**（这三条覆盖了 90% 的"接口报错"）：

1. **方法要对**：`POST /conversations` 用 GET 打过去 → **405 Method Not Allowed**。
2. **路径要对**：`GET /users/1` 这个路由**不存在**（只有 `GET /users`）→ **404 Not Found**。
3. **体要是 JSON 且字段名对**：少了 `Content-Type: application/json` 或字段名拼错 → **422 Unprocessable Entity**，
   响应里 `detail` 会明确指出是哪个字段。

最省事的验证方式：打开 `http://127.0.0.1:8011/docs`，每个接口都有「Try it out」按钮，
参数填错会直接告诉你缺什么——**排错时先用 /docs 复现一次，再回来查代码**。

---

## 5. 排错手册

### 症状 1：`[Errno 98] error while attempting to bind on address ('127.0.0.1', 8000): address already in use`

**这是最常遇到的一条**，因为它根本不是数据问题。

- **原因**：8000 端口已经被另一个进程占了。本机现在是 **Unit 10 的后端**常驻在 8000
  （`uv run uvicorn main:app --reload --port 8000`），你在同一个端口再起 Unit 11 就会撞。
- **判定**：报错里出现 `Errno 98` + `address already in use` 就是它，跟数据库、`.env` 全无关。
- **谁占着**：

  ```bash
  ss -ltnp | grep 8000          # 看 PID
  pgrep -af uvicorn             # 看是哪个单元的
  ```

- **解决**（三选一）：
  1. **换端口**（推荐）：本单元用 `--port 8011`，访问 `http://127.0.0.1:8011/`；
  2. 停掉占用的那个：`pkill -f "uvicorn main:app --port 8000"`；
  3. 确实要用 8000：先确认它属于哪个单元，再看要不要换。

**项目端口约定**（按单元号尾数，避免再撞）：

| 单元 | 后端端口 | 静态页端口 |
| --- | --- | --- |
| Unit 7 / 9 / 10 | 8000 | 3000 |
| Unit 11（本单元） | **8011** | 不用单独起（面板由后端 `/` 直接返回） |
| 临时验证 | 8090+ | 3010+ |

### 症状 2：打开 8000 看到的不是这个单元的面板

- **现象**：页面标题/界面跟你预期不符，或者前端发请求一直 404。
- **判定**：`curl -s http://127.0.0.1:8000/health`
  - 返回里有 `model`、`key_configured` → 是 **Unit 9/10** 的服务；
  - 返回里有 `version`、`tables` → 才是 **Unit 11**。
- **解决**：换端口访问，别以为代码错了。

### 症状 3：`/health` 返回 `{"ok": false, "error": "..."}`

按 `error` 分三种：

| error 关键词 | 原因 | 怎么办 |
| --- | --- | --- |
| `could not connect to server` / `No such file or directory` | 库没起 / socket 路径不对 | `pg_isready -h /var/run/postgresql`；Docker 方案要把 host 改成 `127.0.0.1:5433` |
| `password authentication failed` | 用了 TCP 却没给密码 | 改回 socket 连接串，或在 URL 里补 `:密码@` |
| `database "ai_journey" does not exist` | 库没建 | `sudo -u postgres createdb ai_journey` |

### 症状 4：`relation "messages" does not exist`

- **原因**：连上了库，但没建表。常见于换了库（Docker 5433）之后，`init_db()` 只在服务启动时跑一次。
- **解决**：重启服务（`lifespan` 会建表），或手动 `uv run python -c "import db; db.init_db()"`。

### 症状 5：`/debug/explain` 里 `uses_index: false`

三个原因，按顺序排：

1. **数据太少**：几行数据时 `Seq Scan` 是**正确行为**，不是 bug（D 段实验）；
2. **统计信息过期**：`psql -d ai_journey -c "ANALYZE messages"` 再查；
3. **查询条件和索引列对不上**：索引是 `(conversation_id, created_at)`，
   如果你按 `role` 或者 `content` 过滤，本来就用不上。

### 症状 6：`409` / `404` / `422` 分不清

| 状态码 | 意思 | 本单元的典型触发 |
| --- | --- | --- |
| 404 | 你要的东西不存在 | 建会话时 `user_id` 不存在；删不存在的会话 |
| 405 | 方法用错 | 用 GET 打 `POST /conversations` |
| 409 | 冲突 | 同一个 email 建两次用户 |
| 422 | 参数不合法 | 少了必填字段、email 格式错、role 不在枚举内、limit 超范围 |

---

## 6. 自检与验收

```bash
uv run python main.py      # 15 项，不调模型、不花钱
```

覆盖：建表、唯一约束、外键拒绝孤儿数据、级联删除、跨连接持久化、索引命中、查询计划。

库没起时会直接提示「先起库」，不会抛一堆 traceback。

**验收清单**

- [ ] `uv run python main.py` → 15 通过 / 0 失败
- [ ] `/health` 里能看到 `users, conversations, messages` 三张表
- [ ] 面板（`http://127.0.0.1:8011/`）里建用户 → 建会话 → 写消息，刷新页面数据还在
- [ ] 重启数据库后数据还在：`sudo systemctl restart postgresql` 或 `docker restart pg-ai`
- [ ] 删会话后，`psql -d ai_journey -c "select * from messages"` 里查不到该会话的消息
- [ ] `/debug/explain` 灌数据 + `ANALYZE` 后 `uses_index: true`
- [ ] 能口头解释：为什么索引列顺序是 `(conversation_id, created_at)`

> 第 4 条才是这个单元真正的完成标准——**重启库之后数据还在**。
> 接口返回 200 只证明代码没报错，不证明持久化成立。

---

## 7. 下一单元

Unit 12 在这层数据上补登录鉴权：`/register`、`/login`、`get_current_user` 依赖注入，
以及「只能看自己的会话」的越权校验——到时候你会发现，本单元的 `user_id` 外键就是权限边界的地基。
