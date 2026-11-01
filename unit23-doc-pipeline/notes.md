# Unit 23 笔记 · 文档处理流水线

> 上传接口只做三件事：存字节、写 `pending`、建任务，剩下的交给 worker —— 这个单元真正的产出
> 不是"能跑通"，而是**能证明它重复跑也不会错**。状态：67 项自检全过（离线，SQLite 落临时目录）。

## 本次做了什么

- `pipeline.py`：三张表（documents / tasks / chunks）+ 状态机 + 租约 + `tick`
- `main.py`：9 个接口 + 67 项自检（八组）
- `static/index.html`：上传、tick、任务表、块预览，注入前一律 `esc()`
- `parser.py` / `chunker.py` / `embedding.py`：从 Unit 17、18 复制（复制，不 import 别的单元）

## 一、为什么 `BackgroundTasks` 不够

| | BackgroundTasks | 任务表 + worker |
| --- | --- | --- |
| 进程重启 | 任务直接消失 | 还是 pending，下个 worker 捡起来 |
| 多副本 | 各跑各的，同一文档被处理多次 | 唯一索引 + 租约，只有一个人拿得到 |
| 失败 | 异常进日志，状态停在 running | 落 `error` + `attempts`，超预算转 failed |
| 可观测 | 只能看日志 | `GET /tasks` 就是一个监控面板 |

## 二、状态机（真跑出来的两条路径）

```
pending ──claim──> running ──ok──> done
   ▲                  │
   └── attempts<3 ────┘        └── attempts>=3 ──> failed
```

- 正常：`pending → running → done`，562 字切 4 块，0.25 ms
- 失败注入：`attempts=1 pending → 2 pending → 3 failed`，之后 `claim` 恒返回 `None`

`attempts` 在 **claim 时** `+1`，不是失败时。因为"崩在 claim 之后"的那次也必须记账，
否则 worker 一崩就没人计数，坏任务被无限接管，整个队列堵死在一个永远解析不了的 PDF 上。

## 三、租约：没有 Redis 也能接管

`claim` 时写 `leased_until = now + 30`，领取条件是
`status='pending' OR (status='running' AND leased_until < :now)`，改完再看 `rowcount`。
实测（fake clock，`T=1000000.0`）：

| 时刻 | 动作 | 结果 |
| --- | --- | --- |
| T | w1 claim | running，attempts=1，leased_until=T+30 |
| T+10 | w2 claim | `None`（租约没过期，抢不到） |
| T+31 | w2 claim | 拿到，attempts=2（接管也算一次） |

1. `BEGIN IMMEDIATE` 不能省：默认 DEFERRED 在第一次写之前只拿读锁，两个 worker 会 `SELECT` 到同一行然后一起跑。
2. 租约只解决"谁在干"，不解决"干了几次" —— 接管必然带来重复执行，所以必须有幂等。

## 四、幂等：先删后插，不是 `ON CONFLICT`

```python
conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
conn.executemany("INSERT INTO chunks(...) VALUES(...)", rows)
```

重切片可能让块**变少**（换小 size、文档被改短），`ON CONFLICT` 只覆盖同 idx 的行，
多出来的高位块会留下来继续被检索到 —— 那是脏数据，不是幂等。

实测：员工手册（4 块）连跑 3 次，块数恒为 4，首块文本逐字相同，`COUNT(*) = COUNT(DISTINCT idx) = 4`。
删除、插入、任务状态、文档状态必须在**同一个事务**里，否则会有"块能搜到、任务还是 running"的中间态。

## 五、踩到的三个坑（都是真踩的）

**1. `/docs` 被 FastAPI 内置的 swagger 抢走了**
`client.get("/docs")` 返回一整页 HTML，`r.json()` 直接抛 `JSONDecodeError`。
修法：`FastAPI(docs_url="/swagger", redoc_url=None)`。

**2. 语料太小，断言等于没断**
第一版内置文档 187 字，`size=200` 只切出 1 块，"块数各不相同"当场假失败；扩到 389–562 字（合计 13 块）才看得见切块行为。

**3. 拿上传的原文去比对 chunk，永远对不上**
`normalize()` 会去掉 markdown 的 `#`、合并空白，`raw.find(chunk_text) < 0`，得用 `parse_bytes(name, raw).text` 比。
这也是 documents 表存**原始字节**而不是解析结果的原因：重试能重新解析，解析规则升级后老文档也能重跑。

## 完成标准对照

| 标准 | 证据 |
| --- | --- |
| 上传不等于可检索 | `POST /docs` 返回 `pending`、`chars=0`；tick 之后才有 chunk |
| 任务持久化 | 三张表，`GET /tasks` 可查，进程重启不丢 |
| 重复入队不并行 | 部分唯一索引，4 线程并发入队后 tasks 恒 1 条 |
| 幂等 | 连跑 3 次块数不变、首块文本不变、idx 无重复 |
| 租约接管 | 未过期抢不到；过期能接管且 attempts 累加 |
| 失败可诊断 | error 单行、attempts 1→2→3、第 3 次转 failed，且不卡在 running |

## 一句话总结

可靠的后台任务不是"用个队列库"，而是三件事凑齐：**状态落盘（可恢复）、租约（可接管）、
幂等（接管后不错）**。少了任何一条，另外两条都会变成 bug。

## 面试追问

1. **「worker 崩了，任务怎么恢复？」**
   → 靠租约过期。崩溃的 worker 不会主动释放，所以"我还在干"要用续期证明；超时后别的 worker
   用同一个条件 `UPDATE` 抢占。代价是同一任务可能被执行两次，所以写结果必须幂等。

2. **「重试次数用完还失败，怎么办？」**
   → 转 `failed` 并保留 error，等人处理或人工 retry（`/docs/{id}/retry` 会把 attempts 清零）。
   不能无限重试：一条永久失败的任务会占满 worker，把整个队列饿死。

3. **「怎么保证 chunk 不重复？」**
   → 三层：活任务的唯一索引（一个文档同时只有一条）、写入侧 DELETE + INSERT（同事务）、
   表侧 `UNIQUE(document_id, idx)`。前两层防重复执行，第三层让重复在数据库层直接报错。

4. **「为什么不用 Celery / RQ？」**
   → 规模小时它们的运维成本（broker、结果后端、监控）大于收益，核心机制却和本单元一样：持久队列 +
   可见性超时（SQS 的 visibility timeout 就是租约）+ 重试预算。手写过一遍才知道 `acks_late` 为什么要调。
