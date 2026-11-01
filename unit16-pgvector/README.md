# Unit 16 · pgvector

> 把向量从内存搬进 Postgres。让"相似"变成一条能索引、能过滤、能进事务的 SQL。

**完成标准**：能讲清 HNSW 与 IVFFlat 的区别与取舍（写在 `notes.md` 里）。

## 快速开始

```bash
cd ~/ai-journey/unit16-pgvector

# 1. 建环境
uv sync

# 2. 环境自检（先跑这个，全绿再往下）
uv run python check_env.py

# 3. 填 main.py 里的 TODO 1-5，然后启动
uv run uvicorn main:app --reload --port 8016
```

打开 <http://127.0.0.1:8016/>

端口用 **8016**（Unit 15 是 8015，延续"一个单元一个端口"的约定，避免撞车）。

## 这个目录里有什么

| 文件 | 说明 |
| --- | --- |
| `check_env.py` | 环境自检：连库、查扩展、验 `<=>` 算子、验 HNSW 语法、列现有表 |
| `main.py` | **骨架**：5 个路由已定好，实现留空（TODO 1-5） |
| `embedding.py` | 从 Unit 15 复制过来的（LocalHashingEmbedder，512 维，离线免费） |
| `notes.md` | 三个必答问题 + 实测数据表 + 自检清单 |

## 要填的五个 TODO

| TODO | 做什么 | 关键考点 |
| --- | --- | --- |
| 1 | 建表（`vector(512)` 列） | 维度为什么必须是 512；换模型怎么办 |
| 2 | 入库（文本 → 向量 → `INSERT`） | **Python list 为什么不能直接塞进 SQL 参数** |
| 3 | 检索（`<=>` Top-K） | 为什么 `ORDER BY 距离` 而不是 `ORDER BY score` |
| 4 | 统计（行数/大小/索引） | 用来填实测表 |
| 5 | 建索引（HNSW / IVFFlat） | **IVFFlat 建在空表上等于白建** |

## 一个容易被跳过但很重要的动作

填完 `notes.md` 里那张实测表：

| 数据量 | 无索引 | HNSW | IVFFlat |
| --- | --- | --- | --- |
| 1 千 | | | |
| 1 万 | | | |
| 5 万 | | | |

`EXPLAIN ANALYZE` 是你填这张表的唯一工具：

```sql
EXPLAIN ANALYZE
SELECT id, 1 - (emb <=> '[0.1,0.2,...]') AS score
FROM chunks ORDER BY emb <=> '[0.1,...]' LIMIT 5;
```

看输出里有没有 `Index Scan using chunks_emb_hnsw`。没有就是没走索引。

## 和前后单元的关系

- **依赖 Unit 15**：向量怎么算出来的（`embedding.py` 直接复制，不重写）
- **依赖 Unit 11**：数据库连接、SQLAlchemy 用法（连接串沿用 `ai_journey` 库）
- **被 Unit 17-21 依赖**：后面的文档解析、切分、RAG 全部建在这张 `chunks` 表上

所以这张表的字段设计要在 Unit 17（文档解析）之前想清楚一件事：
**chunk 需不需要记录"来自哪个文件的第几页"？**
如果现在不加，Unit 21（Citation）会回来改表。
