# Unit 23 · 文档处理流水线

> 上传成功不等于可检索。文件进库只是 `pending`，把它变成 chunk 的是 worker —— 而 worker 会崩、
> 会重跑、会被同一个人连点两次。

**完成标准**：能证明三件事 —— 重试不重复写 chunk、租约过期能被接管、失败有 `error` 且不会无限重试。

## 快速开始

```bash
cd ~/ai-journey/unit23-doc-pipeline
uv sync
uv run uvicorn main:app --reload --port 8023
```

打开 <http://127.0.0.1:8023/>：点「塞入 4 篇内置文档」→「全部跑完」，文档从 `pending` 变 `done`，
每篇切出 3–4 块；勾上「注入解析失败」再上传，会看到 `pending → pending → failed` 和 error 文案。

## 自检

```bash
uv run python main.py
```

67 项，全离线，SQLite 落在临时目录（不碰 `data/`）。八组：建文档与入队 / 领取与租约 /
处理（Parse→Chunk）/ 幂等 / 失败注入 / 手动重试与并发入队 / 内置样例实测 / HTTP 层。

## 这个目录里有什么

| 文件 | 说明 |
| --- | --- |
| `pipeline.py` | 文档表 + 任务表 + chunk 表，状态机、租约、`tick` |
| `main.py` | 9 个接口 + 67 项自检 |
| `parser.py` | 从 Unit 17 复制：PDF/MD/TXT/DOCX/CSV → `ParsedDoc` |
| `chunker.py` `embedding.py` | 从 Unit 18 复制，本单元只用 `recursive` 这一种 |
| `static/index.html` | 上传 / tick / 任务表 / 块预览 |
| `data/pipeline.db` | 运行时生成（WAL，旁边还有 `-wal` / `-shm`） |

## 实测

4 篇内置文档，`recursive / size=200 / overlap=30`（自检打印的就是这些数）：

| 文档 | 类型 | 字数 | 块数 | 平均块长 | tick 耗时 |
| --- | --- | ---: | ---: | ---: | ---: |
| 员工手册.md | md | 562 | 4 | 140.5 | 0.25 ms |
| 数据安全.md | md | 470 | 3 | 156.7 | 0.20 ms |
| 发布流程.md | md | 433 | 3 | 144.3 | 0.91 ms |
| 退款政策.txt | txt | 389 | 3 | 129.7 | 0.17 ms |
| **合计** | — | **1854** | **13** | — | — |

- **1 条**：同一文档重复入队，tasks 表恒为 1 条；4 个线程同时入队也是 1 条（部分唯一索引兜底）
- **4 块 × 3 次**：员工手册连跑 3 次，块数恒为 4，首块文本逐字相同，`COUNT(*) = COUNT(DISTINCT idx)`
- **3 次**：失败注入后第 1、2 次回 `pending`，第 3 次转 `failed`，之后再 `claim` 恒返回 `None`
- **0.2 ms**：单次 tick（解析 562 字 + 切 4 块 + 8 条 SQL）的量级 —— 慢的从来不是这里

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 库内统计 + chunk 参数 + 租约/重试参数 |
| POST | `/docs` | 上传（multipart 或表单 text）→ pending + 建任务 |
| GET | `/docs` | 文档列表 + 最后一条任务的状态 |
| GET | `/docs/{id}` | 单文档 + chunk 列表 |
| POST | `/docs/{id}/retry` | 失败任务重置为 pending（已 done → 409） |
| GET | `/tasks` | 任务队列 + 分状态计数 |
| POST | `/worker/tick` | 领一条跑完；没活返回 `claimed=false` |
| POST | `/worker/drain` | 循环 tick，上限 20 |
| POST | `/demo/seed` | 塞 4 篇内置文档 |
| GET | `/swagger` | OpenAPI 文档（`/docs` 已被上面那组接口占走） |

## 常见坑

**1. `BackgroundTasks` 不是队列，是"响应发完之后顺手跑一下"**
任务活在进程内存里：重启即丢、多副本各跑各的、没有去重也没有重试，状态永远 `processing`。

**2. 先 `SELECT` 再无条件 `UPDATE`，两个 worker 会跑同一份文档**
`claim` 必须 `BEGIN IMMEDIATE` + 带条件 `UPDATE`（`WHERE status='pending' OR leased_until < now`）再看 `rowcount`。

**3. `attempts` 记在失败时，崩溃就没人记账**
崩在 `claim` 之后、`done` 之前的那次不算数，坏任务就被无限接管。所以 `attempts` 在 `claim` 时 `+1`。

**4. 用 `ON CONFLICT` 去重会留下多余的旧块**
重切片可能让块变少，`ON CONFLICT` 只覆盖同 `idx` 的行。正确做法是一个事务里 `DELETE` 后批量 `INSERT`。

**5. 解析异常没接住，任务永远 `running`**
`except` 里必须把任务写回 `pending`/`failed` 并落 `error`；chunk 写入与状态收尾也必须在同一事务，
否则会出现"块能搜到、任务还 running"，用户一重试就重复。

## 验收清单

- [ ] `uv run python main.py` → 67 通过 / 0 失败
- [ ] 上传一篇文档，`/docs` 里状态是 `pending`，`/tasks` 里有对应任务
- [ ] 点一次 tick → `done`，块数 = `/docs/{id}` 返回的 `chunk_count`；再点一次 → `claimed=false`，块数不变
- [ ] 勾「注入解析失败」→ 第 3 次失败后 `failed`，error 有字，之后 tick 不再碰它
- [ ] 手动把 `leased_until` 改成过去时间，另一个 worker 能接管；说得出为什么 `attempts` 在 `claim` 时加

## 下一单元

Unit 24 把这 13 个 chunk 灌进向量库，做 Top-K 检索与阈值过滤 —— 到那时"可检索"才有真定义：
不是切出来了，而是问一句能召回到对的那一块。
