# Unit 11 · PostgreSQL：把对话真正存进数据库

三张表 `users` / `conversations` / `messages`，外键级联 + 复合索引，
把 Unit 9/10 那种"进程一重启就没了"的存储换成真的持久化。

## 快速开始

```bash
# 1. 确认库在跑（本机已装 PG 17）
pg_isready -h /var/run/postgresql && psql -d ai_journey -c "select 1"

# 2. 装依赖
uv sync

# 3. 配环境（默认走 Unix socket，不需要密码）
cp .env.example .env

# 4. 起服务
uv run uvicorn main:app --reload --port 8000
```

打开 <http://127.0.0.1:8000/> 就是数据面板；接口文档在 `/docs`。

## 自检（不调模型、不花钱）

```bash
uv run python main.py
```

覆盖 15 项：建表、唯一约束、外键拒绝孤儿数据、级联删除、
跨连接持久化、索引命中、查询计划。

库没起时会直接告诉你 `先起库：docker compose up -d` 然后退出，不会抛一堆 traceback。

## 两种连接方式

| 方式 | 连接串 | 适用 |
| --- | --- | --- |
| **本机 PG（默认）** | `postgresql+psycopg://rik@/ai_journey?host=/var/run/postgresql` | 这台机器，socket + peer 认证，免密 |
| Docker | `postgresql+psycopg://postgres:postgres@127.0.0.1:5433/ai_journey` | 换机器 / 要隔离 |

> 本机 5432 已被系统 PostgreSQL 17 占用，所以 `docker-compose.yml` 映射的是 **5433**。
> 用 Docker 时记得把 `.env` 里的 `DATABASE_URL` 换成上表第二行。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 探活，返回 PG 版本 + 表名列表 |
| POST | `/users` | 建用户（email 唯一，重复返回 409） |
| GET | `/users` | 列用户 |
| POST | `/conversations` | 建会话（user 不存在返回 404） |
| GET | `/conversations?user_id=` | 列会话，按最近活跃排序 |
| DELETE | `/conversations/{id}` | 删会话，消息级联删除 |
| POST | `/conversations/{id}/messages` | 追加消息，顺带刷新会话 `updated_at` |
| GET | `/conversations/{id}/messages` | 拉消息，按时间正序 |
| GET | `/debug/explain?conversation_id=` | `EXPLAIN ANALYZE`，看索引有没有生效 |

## 表结构

```
users                     conversations                  messages
─────                     ─────────────                  ────────
id (PK)                   id (PK)                        id (PK)
email (UNIQUE)      ←──  user_id (FK→users, CASCADE)     conversation_id (FK→conversations, CASCADE)
display_name              title                          role
created_at (tz)           created_at (tz)                content
                          updated_at (tz)                tokens
                                                         created_at (tz)

索引：
  ix_users_email                      UNIQUE
  ix_conversations_user_updated       (user_id, updated_at)
  ix_messages_conversation_created    (conversation_id, created_at)   ← 本单元重点
```

## 验收清单

- [ ] `uv run python main.py` → 15 通过 / 0 失败
- [ ] `/health` 里能看到 `users, conversations, messages` 三张表
- [ ] 面板里建用户 → 建会话 → 写消息，刷新页面数据还在
- [ ] `docker restart pg-ai`（或 `sudo systemctl restart postgresql`）后数据还在
- [ ] 删会话后，`messages` 里查不到该会话的消息
- [ ] `/debug/explain` 返回 `uses_index: true`

## 下一单元

Unit 12 在这个数据层上加 JWT：`/register`、`/login`、`get_current_user`，
以及"只能看自己的会话"的越权校验。
