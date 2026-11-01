# Unit 25 笔记 · Retriever 模块

> 检索层的价值不在于"能搜到"，而在于**能被测量、能被复盘、不能越权**。
>
> 状态：36 项自检全过（离线 SQLite + 本地哈希向量）。

## 本次做了什么

- `retriever.py`：`Retriever` 类 + `Filters` + `Hit`
  - 租户隔离写死在 SQL 的 WHERE 里
  - `source / source_prefix / page_min / page_max` 四种过滤
  - 阈值 → 排序 → 截断 top_k
  - 每次查询写 `retrieval_logs`
  - `recall_at_k` / `mrr` / `ndcg_at_k` 三个指标函数
- `main.py`：`/seed`、`/search`、`/logs`、`/eval` + 36 项自检
- `static/index.html`：检索面板 + 字面/改写两组评测

## 一、为什么检索必须收敛成一个模块

在 Unit 19 里，检索逻辑还能写在路由函数里。到了这一单元必须抽出来，原因很实际：

- `top_k`、阈值、过滤条件会被三个地方用到（问答、评测、调试），写在路由里必然重复
- 重复就会漂移：改了一处忘了另一处，评测结果和线上行为不一致
- 抽出来以后，评测可以直接调 `Retriever`，不用起 HTTP

接口只有两个方法：

```python
index(tenant_id, items) -> int
search(tenant_id, query, top_k, threshold, filters) -> list[Hit]
```

**换 pgvector 时只改 `index` / `search` 内部，上层一行不动** —— 这才是"抽象"该有的样子。

## 二、租户隔离：不写在业务里，写在 SQL 里

```python
sql = f"SELECT ... FROM chunks WHERE tenant_id = ? AND {cond}"
rows = c.execute(sql, [tenant_id, *args]).fetchall()
```

`cond` 只可能来自 `Filters.to_sql()`，而 `Filters` 只有四个字段。
如果允许调用方传一段 SQL 片段（很多 demo 就这么干），
那隔离就等于没有 —— 一个 `1=1 OR tenant_id='beta'` 就能读走别人的库。

空 `tenant_id` 直接抛异常，不做默认值兜底。理由：默认值会让测试永远绿，
而线上第一个漏传 tenant 的请求就会把全库返回给用户。

## 三、指标：先能手算，再谈调参

`recall_at_k` 的分母是**相关文档总数**，不是 k：

```python
return len(set(retrieved[:k]) & relevant) / len(relevant)
```

自检里用手算样例钉死语义：

| retrieved | relevant | k | recall@k | MRR |
| --- | --- | ---: | ---: | ---: |
| `[c, a, b]` | `{a, b}` | 1 | 0.0 | 0.5 |
| `[c, a, b]` | `{a, b}` | 2 | 0.5 | 0.5 |
| `[c, a, b]` | `{a, b}` | 3 | 1.0 | 0.5 |

MRR 只看**第一个**相关结果的位置，所以它衡量"用户要不要往下翻"；
recall 衡量"该有的有没有都出来"。两个一起看才知道问题出在哪：
recall 高但 MRR 低 = 都召回了但排得乱 → 该加重排（Unit 26）。

## 四、实测：字面提问全中，改写就掉

22 条 acme 语料，9 道题：

| 组 | n | recall@5 | MRR |
| --- | ---: | ---: | ---: |
| 字面 | 6 | 1.000 | 1.000 |
| 同义改写 | 3 | 0.667 | 0.417 |

- 「感冒了看病要开什么单据」→ 病假块排到了第 4（MRR 0.25）
- 「出差回来多久内要贴票」→ 完全没召回（0 分）

这两题暴露的是**词表不重合**：正文写「附发票原件与行程单」，用户说「贴票」；
正文写「二级以上医院证明」，用户说「看病要开单据」。
本地哈希向量只认 bigram 重叠，这种改写它就是看不见。

所以我没把这两题从评测集里删掉 —— 它们才是这个 embedder 的真实水平。
**评测集里只留能过的题，是自欺欺人。**

## 五、踩到的坑

### 1. `with sqlite3.connect()` 不关连接

```python
with sqlite3.connect(path) as c:    # 只 commit，不 close
    ...
```

Python 文档里写得很清楚，但太容易忽略。检索是高频调用，句柄会慢慢漏。
最后包了个 `_ConnCtx`，提交/回滚/关闭三件事都做。

### 2. 过滤和 Top-K 的顺序

先 `ORDER BY 分数 LIMIT k` 再过滤，会出现"过滤后只剩 1 条"的情况 ——
因为前 20 名被别的来源占满了。正确顺序是**先过滤，再排序截断**。

### 3. 日志里存 filters 的 JSON

一开始存了 `str(filters)`，复盘时看到 `<Filters object at 0x7f...>`，等于没记。
必须 `json.dumps(filters.to_dict())`，并且 `ensure_ascii=False` 让中文可读。

## 完成标准对照

- [x] 检索独立成模块，接口只有 index/search
- [x] 租户隔离有断言（acme 搜不到 beta）
- [x] 四种过滤都有断言
- [x] 阈值生效，且结果分数全部达标
- [x] 每次检索落日志，含 filters 与耗时
- [x] Recall@K / MRR / nDCG 有手算校验
- [x] 评测集分字面/改写两组，差距有数字

## 一句话总结

Retriever 是 RAG 里**唯一能用数字衡量**的一层。
把它抽出来、给它日志、给它评测集，你才有资格谈优化；
否则每次"调参"都只是换一种错觉。

## 面试追问

1. **「怎么防止越权检索？」**
   → 租户/权限条件由服务端在 SQL 里拼死，不接受客户端传字段名或 SQL 片段；
   过滤字段做白名单；空 tenant 直接报错而不是默认值。再加一条：日志里记 tenant，
   出事后能证明"这条请求只查了这些数据"。

2. **「召回率低怎么排查？」**
   → 三段定位：① 答案块是否在库里（不在 → 解析/切分问题）；
   ② 在库里但没进 Top-K（→ 向量模型或阈值问题）；
   ③ 进了 Top-K 但排太后（→ 该加重排）。这三种的修法完全不同。

3. **「Recall@K 的 K 怎么定？」**
   → 取决于下游能吃多少上下文。RAG 里 K 通常 = 重排前的候选数（20–50），
   最终喂给模型的只有 3–5 条。K 定太小，重排没有腾挪空间。

4. **「为什么要存检索日志？有监控不够吗？」**
   → 监控告诉你"慢了/错了"，日志告诉你"这题为什么没搜到"。
   排查一次线上 badcase，靠的就是当时的 query、filters、命中的 chunk id。
