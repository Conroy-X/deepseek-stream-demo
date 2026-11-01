# Unit 22 笔记 · 知识库管理

> 这一单元不教新的检索技术，教的是"把上传做对"的代价：安检、归属、级联删除，每件事都有一个能静默坏掉的开关。状态：86 项自检全过（离线，临时 SQLite）。

## 本次做了什么

- `main.py`：4 张表（`kbs`/`docs`/`chunks`/`jobs`）+ 8 个接口 + 86 项自检
- 上传走 base64 JSON，不引 `python-multipart`：文件小，少一个依赖
- `static/index.html`：建库 / 上传 / 看块 / 删文档 / 删库，外加 4 个攻击按钮
- `_conn()` 统一管 `commit / rollback / close` + `PRAGMA foreign_keys=ON`

## 一、安检的顺序：白名单 → 大小 → 魔数

三道关不并列，按代价排：白名单是字符串比较，大小是 `len()`，魔数要读字节（docx 还得开 zip）。

| 顺序 | 检查 | 拦什么 |
| ---: | --- | --- |
| 1 | 扩展名白名单 | `.exe` / `.svg`（能带脚本的都不放） |
| 2 | 大小上限 2 MB | 把内存和磁盘打满 |
| 3 | 魔数比对 | `invoice.pdf` 里塞 PNG |

魔数最容易做成"查黑名单"然后漏：黑名单永远不全。这里反过来 —— `sniff()` 给出实际类型，
再和扩展名声明的类型比对，不等就拒：

```python
expected = KIND_BY_EXT[ext]   # .pdf → "pdf"
got = sniff(data)             # %PDF- → "pdf"，\x89PNG → "png"
if got != expected:           # PNG 改名 .pdf 在这里掉出去
    raise UploadRejected("magic_mismatch", ...)
```

docx/xlsx/pptx 都是 zip，靠 central directory 里的 `word/` `xl/` `ppt/` 区分 —— 所以"裸 zip 改名 .docx"也会被拦，自检里有一条专门测它。

## 二、去重的作用域：`UNIQUE(tenant_id, kb_id, sha256)`

这是本单元最容易写错的一行。写成 `UNIQUE(sha256)` 会发生：A 传合同写入 `doc_xxx`；
B 传同一份命中约束复用 `doc_xxx`；B 删掉自己的"文档"，A 的文件也消失；
更糟的是 B 从 `docs` 行里读到 A 的 `name` / `tenant_id`。

实测边界：同一份 1884 字节 md，`acme` 传进库 A 再去重命中；`acme` 传进库 B 是新行；
`globex` 传进自己的库也是新行 —— 三个 `doc_id`，sha256 前 12 位完全相同。
**去重省的是存储与解析成本，不是权限。**

## 三、租户过滤 + 404，缺一条就泄露存在性

领域函数第一个参数都是 `tenant`，SQL 第一个条件都是 `tenant_id = ?`：

```python
row = c.execute("SELECT * FROM kbs WHERE id=? AND tenant_id=?", (kb_id, tenant)).fetchone()
if row is None:
    raise NotFound("知识库不存在")     # 不是 Forbidden
```

`NotFound` 到 HTTP 层就是 404，与"这个 id 根本不存在"完全一样。403 要先判断"存在但无权"，
那个判断本身就是一次探测接口。自检 7 条覆盖：跨租户读列表 / 删文档 / 上传、
非法 `kb_id` 格式（400）、合法但不存在的 `kb_id`（404）。

## 四、级联删除的三个真坑

**坑 1：`PRAGMA foreign_keys` 每个连接默认 OFF。** 建表语句里写着 `ON DELETE CASCADE`，
删 doc 后 chunk 一条不少，而且不报错。自检里两条盯着它：查 `PRAGMA foreign_keys == 1`，
以及绕过业务函数直接 `DELETE FROM docs`、验证 chunk 仍然消失 —— 证明级联靠 FK，不靠代码。

**坑 2：`with sqlite3.connect()` 只 commit 不 close。** 它是事务上下文，不是文件句柄上下文。
连接一直挂着，Windows 上还会让文件删不掉。`_conn()` 手写 commit / rollback / close。

**坑 3：`ON DELETE CASCADE` 不给 rowcount。** 所以 `delete_doc` 先 `DELETE FROM chunks`
拿 `rowcount`，再删 `jobs`，最后删 `docs`。接口因此能回答 `chunks_removed=5, jobs_removed=1`。
`jobs` 是故意留的"解析任务记录"：只清 chunk 不清任务，重跑队列会拿着已不存在的 `doc_id`
继续干活 —— 这就是 Unit 23 里 worker 重复写块的来源。

## 五、实测

| 指标 | 数字 | 怎么来的 |
| --- | --- | --- |
| 1000 字 md 切块 | 6 块，最长 ≤180 字 | `CHUNK_CHARS=180`，段落装袋 + 超长硬切 |
| 上传耗时中位 | 5.05 ms | TestClient 25 次取中位，含 base64 + 切块 + 2 次写表 |
| 拒绝路径 | 4.56 ms | 在 `INSERT` 之前 return，比成功路径还快 |
| 20 次重复上传 | 5.3 ms，`docs` 仍 1 行 | 去重是"查 + 返回"，不是"写 + 冲突" |
| 删 1 份文档 / 1 个库 | 5 块 + 1 任务 / 3 文档 | `chunks_removed=5`；整库时报 3→3→3，一条 DELETE 链走完 |

## 完成标准对照（合计 86 条，与 `uv run python main.py` 输出一致）

| 要求 | 实现 | 自检条数 |
| --- | --- | ---: |
| 白名单 + 魔数 | `inspect_upload()` + `sniff()`，比对声明与实际 | 15 |
| 单文件上限 | `MAX_BYTES = 2 MiB`，超限 400 `too_large` | 3 |
| 同租户去重 / 不跨租户 | `UNIQUE(tenant_id, kb_id, sha256)`，命中返回原 doc_id | 11 |
| 越权 404 | `NotFound` → 404，不区分"不存在"与"不是你的" | 7 |
| 删文档级联（含直删父行验 FK） | 显式删 chunks/jobs + FK CASCADE 兜底 | 8 |
| 删库 / 切块 / HTTP / 孤儿行 | 文档、块、任务一起收；9 个路由；残留计数 0 | 42 |

## 一句话总结

上传接口的难点不在"把字节存下来"，而在**存之前敢拒、存之后能追、删之后不留**；
这三件事都能静默失效，只有自检能证明它们还在工作。

## 面试追问

1. **「文件类型校验看 Content-Type 够吗？」**
   → 不够，它和扩展名都是客户端声明的。要读字节做魔数比对，且是"声明 vs 实际"的双向比对，不是查黑名单；OOXML 还要开 zip 看内部目录名。魔数只证明"开头像"，不证明文件合法，解析前仍要限流（页数、解压后大小）。

2. **「内容去重了，权限能一起去重吗？」**
   → 去重优化存储和解析成本，权限是所有权问题。`UNIQUE(sha256)` 会让第二个租户拿到第一个租户的 `doc_id`，删一个等于删两个。正确作用域是 `(tenant_id, kb_id, sha256)`；要做全局内容寻址，就得加租户维度的引用计数。

3. **「为什么越权返回 404 而不是 403？」**
   → 403 意味着"存在但你无权"，这本身就是信息，可以拿状态码差异枚举 `kb_id`。统一 404 后越权与不存在在外部不可区分；内部日志当然要分开记。

4. **「删文档时 chunk 没删干净会怎样？」**
   → 检索层会召回已删除文档的块，把用户撤下的内容当依据答出来 —— 合规场景就是事故；重跑解析时 worker 还可能给同一 `doc_id` 再写一遍块，造成重复召回。所以删除要么靠 FK CASCADE（且每个连接开 `foreign_keys`），要么显式按 `doc_id` 删干净，并用孤儿行计数长期盯着。
