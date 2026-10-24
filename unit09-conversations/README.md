# Unit 9 · 多轮对话：Conversation / Message 模型

> 目标：**刷新页面后历史还在，而且模型能记住上文**。

---

## 一、数据模型：为什么是两张表

```
Conversation 1 ──── N Message
```

```sql
CREATE TABLE conversations (
    id          TEXT PRIMARY KEY,     -- uuid 前 12 位
    title       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content         TEXT NOT NULL,
    created_at      REAL NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX idx_msg_conv ON messages(conversation_id, created_at);
```

### 三个设计决定及理由

**1. 为什么不能只存最后一条消息？**

LLM 是**无状态**的，每次请求都要把完整历史重新发一遍。
只存最后一条，模型就不知道「刚才说了什么」，第二句就会答非所问。

**2. 为什么 `role` 要加 `CHECK` 约束？**

模型只认 `system` / `user` / `assistant` 三种。
写错成 `robot` 不会报错，但会让模型行为异常，很难排查。
**在数据库层拦住**，比在应用层校验更可靠。

**3. 为什么要 `ON DELETE CASCADE`？**

删会话时如果只删 `conversations` 表，`messages` 会变成孤儿数据，
越积越多且永远查不到。级联删除保证一致性。

**4. 为什么要按 `id` 排序而不是 `created_at`？**

`created_at` 是浮点秒，同一秒内插入多条时顺序不确定。
`id` 是自增主键，**严格保证插入顺序**。

---

## 二、怎么跑

```bash
cd ~/ai-journey/unit09-conversations
uv sync                                     # 建 .venv + 按 uv.lock 装依赖（幂等）
cp .env.example .env && chmod 600 .env      # 填 Key

uv run python main.py                       # 自检：20 项，不花钱
uv run uvicorn main:app --reload --port 8000
```

前端另起一个终端：

```bash
cd ~/ai-journey/unit09-conversations
uv run python -m http.server 3000 -d static
```

打开 **http://127.0.0.1:3000/**

---

## 三、接口一览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/conversations` | 会话列表（按最近更新倒序，带消息数） |
| POST | `/conversations` | 新建会话 |
| GET | `/conversations/{id}` | 会话详情 + 全部消息 |
| PATCH | `/conversations/{id}` | 重命名 |
| DELETE | `/conversations/{id}` | 删除（级联删消息） |
| POST | `/conversations/{id}/messages` | **发消息（SSE 流式）** |

### 发消息的内部流程

```
1. 先把 user 消息落库          ← 即使模型挂了也不丢
2. 取出该会话的完整历史
3. 拼成 [system] + [history...]
4. 调用模型，流式返回
5. 把 assistant 回答落库
6. 如果是首轮，用第一句话当标题
```

**第 1 步的顺序很关键**：先落库再调模型。
如果反过来，模型报错时用户的话就丢了。

---

## 四、前端：会话侧边栏

- 左侧列出所有会话，按最近更新排序
- 点会话 → 加载历史消息（渲染成 Markdown）
- 悬停显示 `×` 删除
- 首轮对话后自动用你的第一句话当标题
- 移动端侧边栏变成抽屉

---

## 五、验证「多轮」真的生效

这是本单元的核心验收：

```
你：我叫张三，正在学 AI 应用开发
AI：好的张三，…（回答）

你：我叫什么？在学什么？
AI：你叫张三，正在学 AI 应用开发。   ← 证明历史被正确带上
```

然后**刷新页面**，点回这个会话 —— 历史应该完整还原。

### 命令行验证

```bash
CID=$(curl -s -X POST http://127.0.0.1:8000/conversations \
  -H "Content-Type: application/json" -d '{"title":"测试"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")

curl -N -X POST "http://127.0.0.1:8000/conversations/$CID/messages" \
  -H "Content-Type: application/json" -d '{"content":"我叫张三"}'

curl -s "http://127.0.0.1:8000/conversations/$CID" | python3 -m json.tool
```

---

## 六、SQLite 的局限（为 Unit 11 铺垫）

| 局限 | 后果 | Unit 11 怎么解决 |
| --- | --- | --- |
| 单文件、单写入者 | 并发写会锁表 | 换 PostgreSQL |
| 无用户概念 | 所有人共享会话 | 加 `users` 表 + JWT |
| 无全文检索 | 搜历史只能 `LIKE` | PG 的 `tsvector` 或 pgvector |
| 无连接池 | 每请求开连接 | SQLAlchemy 连接池 |

SQLite 适合学习和小工具，**生产要多用户就得上 PostgreSQL**。

---

## 七、完成标准

- [ ] `uv run python main.py` → 20 通过 / 0 失败
- [ ] 能建多个会话，侧边栏正确排序
- [ ] 连续 10 轮对话，模型始终记得前文
- [ ] 刷新页面，历史完整还原
- [ ] 删除会话，数据库里对应消息也被删（可用 `sqlite3 chat.db` 查）
- [ ] 能解释：为什么不能只存最后一条消息
- [ ] 能解释：`ON DELETE CASCADE` 不写会怎样

---

## 八、资料

- [SQLite 官方文档](https://www.sqlite.org/docs.html)
- [SQLModel](https://sqlmodel.tiangolo.com/) —— Unit 11 换 PG 时会用
- [FastAPI + SQL 数据库教程](https://fastapi.tiangolo.com/tutorial/sql-databases/)
- [OpenAI Chat Completions 消息结构](https://platform.openai.com/docs/api-reference/chat)
- [DeepSeek 对话 API](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion)
