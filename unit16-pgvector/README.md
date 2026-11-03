# Unit 16 · pgvector

> 把向量从内存搬进 Postgres。让"相似"变成一条能索引、能过滤、能进事务的 SQL。

**状态：已跑通。** `check_env.py` 5/5、`main.py` 自检 15/15、`bench.py` 实测数据已填进 `notes.md`。
**完成标准**：能讲清 HNSW 与 IVFFlat 的区别与取舍（`notes.md` Q1 有参考版，但请先自己写）。

## 快速开始

```bash
cd ~/ai-journey/unit16-pgvector

uv sync                              # 装依赖（numpy 只给压测用）
uv run python check_env.py           # 环境自检：连库、扩展、算子、索引语法
uv run python main.py                # 单元自检：15 项，不用起服务
uv run uvicorn main:app --port 8016  # 起服务，打开 http://127.0.0.1:8016/
uv run python bench.py               # 压测：1k/1万/5万 × 无索引/HNSW/IVFFlat
```

端口 **8016**（Unit 15 是 8015，一个单元一个端口，避免撞车）。

## 连接串：本机必须走 socket

```ini
# .env
DATABASE_URL=postgresql+psycopg://rik@/ai_journey?host=/var/run/postgresql
```

本机 PG 17 是 **Unix socket + peer 认证**，系统用户 `rik` 直接映射成数据库超级用户，没有密码。
骨架里默认的 `postgres:postgres@127.0.0.1:5432` 在本机必然报
`password authentication failed` —— 遇到这个错先想"该走 socket 还是 TCP"，别急着改 `pg_hba.conf`。

这和 Unit 11 完全一致，所以两个单元共用 `ai_journey` 库。

## 这个目录里有什么

| 文件 | 说明 |
| --- | --- |
| `check_env.py` | 环境自检：连库、查扩展、验 `<=>` 算子、验 HNSW 语法、列现有表 |
| `main.py` | 五个路由 + 15 项自检（`python main.py` 直接跑，不需要 uvicorn） |
| `bench.py` | 压测：造聚类数据 → 三种检索方式对比耗时与 recall@10 |
| `sweep.py` | 参数扫描：`ef_search` / `probes` 对延迟与召回的影响 |
| `static/index.html` | 单页 UI：建表 / 入库 / 检索（可切索引开关、看执行计划）/ 统计 / 建索引 |
| `embedding.py` | 从 Unit 15 复制（LocalHashingEmbedder，512 维，离线免费） |
| `notes.md` | 三个必答问题的参考答案 + 实测表 + 6 个真实踩坑 |

## 五个 TODO 的答案要点（先自己写，再对照）

| TODO | 路由 | 答案要点 |
| --- | --- | --- |
| 1 | `/init` | `CREATE EXTENSION IF NOT EXISTS vector` + `CREATE TABLE IF NOT EXISTS`。**512 必须和 embedder 输出维度一致**，改模型 = 改列类型 = 重算所有向量（`ALTER COLUMN ... TYPE vector(1024)` 在有数据时会失败，因为维度不同无法隐式转换） |
| 2 | `/insert` | Python list 会被 psycopg 当成 `float8[]`，vector 列不收 → 转 `'[..]'` 字符串 + `CAST(:e AS vector)`，或用 `pgvector` 的 `register_vector`。重复文本靠 `md5(text)` 唯一索引 + `ON CONFLICT DO NOTHING` 幂等 |
| 3 | `/search` | `ORDER BY emb <=> qv LIMIT k`（**不是** `ORDER BY 1-距离 DESC`，后者要全量算了再排序，索引白建）。`1 - 距离` 在归一化后就是余弦相似度。`use_index=false` 用 `SET LOCAL enable_indexscan=off`，**必须和查询在同一个事务里** |
| 4 | `/stats` | `count(*)` + `pg_total_relation_size` + `pg_indexes` 联 `pg_relation_size`。用来填实测表 |
| 5 | `/index` | HNSW 空表可建；**IVFFlat 必须先有数据**，空表建出来是废索引（且不会自动重聚类，要 `REINDEX`）。`lists ≈ rows/1000`，查询 `probes` 默认 1 太小 |

## 实测数据（详见 `notes.md`）

5 万行 / 512 维 / `top_k=10` / 5 次取中位数：

| 数据量 | 无索引 | HNSW | IVFFlat | 建 HNSW | 建 IVFFlat |
| --- | --- | --- | --- | --- | --- |
| 1,000 | 2.8 ms | 0.5 ms | 0.4 ms | 0.3s | 0.2s |
| 10,000 | 26.7 ms | 0.2 ms | 26.0 ms | 3.4s | 0.7s |
| 50,000 | 144.0 ms | 0.3 ms | 4.4 ms | 57.7s | 15.7s |

**三条必须先记住的结论**：

① 一万行以内全表扫就够快（26ms），索引未必划算 —— 本机 1 万行建了 IVFFlat，planner 直接不用它（计划是 Seq Scan）。
② 建了索引 ≠ 用上索引，一定要 `EXPLAIN` 确认。
③ `ef_search` / `probes` 不是越大越好：实测 `ef_search=200` 时 planner 判定走索引太贵，
退回 `Seq Scan + Sort`，延迟从 0.84ms 飙到 193.76ms。详见 `notes.md` 的参数取舍表。

## 压测数据为什么是"聚类 + 噪声"

第一版用纯随机向量，HNSW 的 recall@10 只有 0.10 —— 不是索引坏，是**高维随机向量彼此几乎等距**
（512 维随机余弦的标准差约 1/√512 ≈ 0.044），所有点距离一样时"找最近 10 个"是病态问题。
真实 embedding 是成簇的，簇内近簇间远，索引才有意义。所以 `bench.py` 用 K 个簇心 + 噪声造数，
并且**查询向量也从同一分布采样**（跨分布测召回没有意义）。

## 和前后单元的关系

- **依赖 Unit 15**：向量怎么算出来的（`embedding.py` 直接复制，不重写）
- **依赖 Unit 11**：数据库连接、SQLAlchemy 用法（同一个 `ai_journey` 库）
- **被 Unit 17–21 依赖**：后续文档解析、切分、RAG 全部建在这张 `chunks` 表上

**交接给 Unit 17 的一件事**：`meta` 里要记 `source`（文件名）、`page`（页码）、`chunk_index`。
现在加只是多几个字段，等表里堆了几万行再改就要迁移 —— Unit 21 做引用时会回来找你。

## 清理压测数据

```sql
DELETE FROM chunks WHERE text LIKE 'bench-%';
-- 50k 行约 141MB，删完记得 VACUUM
VACUUM chunks;
```
