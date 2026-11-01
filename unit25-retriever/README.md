# Unit 25 · Retriever 模块

> 检索必须只有一个入口。参数散在各个路由里，你就再也说不清"改这个到底变好还是变差"。

**完成标准**：能拿出 Recall@K 与 MRR 的数字，并解释同义改写为什么会掉分。

## 快速开始

```bash
cd ~/ai-journey/unit25-retriever
uv sync
uv run uvicorn main:app --reload --port 8025
```

打开 <http://127.0.0.1:8025/>：能改租户、阈值、source/页码范围，点「评测」看两组指标。

## 自检

```bash
uv run python main.py
```

36 项，离线：指标手算校验（recall@1/2/3、MRR、nDCG）、索引与排序、
**租户隔离**（acme 搜不到 beta）、三种过滤、阈值、检索日志、评测集。

## 这个目录里有什么

| 文件 | 说明 |
| --- | --- |
| `retriever.py` | `Retriever` / `Filters` / `Hit` + `recall_at_k` / `mrr` / `ndcg_at_k` |
| `main.py` | 检索、日志、评测接口 + 36 项自检 |
| `embedding.py` | 从 Unit 15 复制（本地哈希向量） |
| `static/index.html` | 检索面板 + 评测表 |

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 各租户 chunk 数、向量后端 |
| POST | `/seed` | 灌入演示语料（`{"reset":true}` 先清空） |
| POST | `/search` | 租户 + query + top_k + threshold + 过滤 |
| GET | `/logs?tenant=` | 最近检索日志 |
| POST | `/eval` | 按字面/改写分组的 Recall@K / MRR / nDCG |

## 实测（本单元的核心产出）

22 条 acme 语料、9 道题、`top_k=5`：

| 提问方式 | n | recall@5 | MRR |
| --- | ---: | ---: | ---: |
| 字面（沿用原文措辞） | 6 | **1.000** | **1.000** |
| 同义改写（用户真实说法） | 3 | **0.667** | **0.417** |

改写组逐题：

| 问题 | 召回前 3 | recall | MRR |
| --- | --- | ---: | ---: |
| 感冒了看病要开什么单据 | rf-3, pu-2, pu-3 | 1.00 | 0.25 |
| 钱多久能退回来 | **rf-2**, rl-2, pu-3 | 1.00 | 1.00 |
| 出差回来多久内要贴票 | sc-2, rf-2, rf-1 | **0.00** | **0.00** |

第三题完全没召回 `hb-3`（差旅报销 10 个工作日），因为「贴票」和「附发票原件」
一个字都不重合。**这不是 bug，是本地哈希向量的能力边界** ——
换成真 embedding 模型前，任何"检索效果不错"的结论都不算数。

## 三条不能省的设计

**1. 租户条件写死在 SQL 里，不接受客户端传字段**

```sql
WHERE tenant_id = ? AND {filters 拼出来的条件}
```

`Filters` 只允许 `source / source_prefix / page_min / page_max` 四个字段，
没有"传任意 where 条件"的口子。这不是 API 设计问题，是越权漏洞的分界线。

**2. 阈值先过滤再 Top-K**

先按分数过滤，再取前 k。反过来的话，阈值可能把结果全部清空，
而 `top_k` 还能返回"不足 k 条"—— 两种顺序都有人写，但语义必须明确。

**3. 每次检索都落日志**

`retrieval_logs` 记了 query、top_k、threshold、filters、命中数、top_score、耗时、结果 id。
线上被问"为什么这题没搜到"时，这是唯一能复盘的东西。

## 常见坑

- **`with sqlite3.connect(...)` 只提交，不关闭连接**。跑几千次检索就会耗光句柄，
  必须自己包一层上下文管理器。
- **recall 的分母是相关总数，不是 k**。写成 `len(hits[:k]) / k` 会得到虚高的分数。
- **空 `tenant_id` 必须报错**。默认成 `"default"` 是最常见的越权来源。
- **过滤要在检索前生效**，否则过滤后可能一条都不剩（因为 Top-K 已经被别的来源占满）。

## 验收清单

- [ ] `uv run python main.py` → 36 通过 / 0 失败
- [ ] 页面上 acme 搜「Beta 公司年假」搜不到 beta 的数据
- [ ] `source=退款政策.md` 时结果只剩这个文件
- [ ] `threshold=0.5` 时结果条数变少，且分数都 ≥ 0.5
- [ ] 点「评测」看到两组指标差距，能解释第三题为什么 0 分
- [ ] 看懂 MRR 与 recall 的区别（前者看排名，后者看覆盖）

## 下一单元

Unit 26 在召回之后加重排：向量先取 Top-20，再用 CrossEncoder 重排取 Top-5，
用 MRR/nDCG 量化"重排到底赚回了多少"。
