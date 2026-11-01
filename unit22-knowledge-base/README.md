# Unit 22 · 知识库管理

> 上传不是"存个文件"：它要过安检、有主人、能连根拔。

**完成标准**：PNG 改名 `.pdf` 被 400 拦下；同一份字节被两个租户上传是两条记录、各自能删；删完之后 `chunks` 里查不到指向已删文档的行。

## 快速开始

```bash
cd ~/ai-journey/unit22-knowledge-base
uv sync
uv run uvicorn main:app --reload --port 8022
```

打开 <http://127.0.0.1:8022/>：建库 → 选文件上传 → 看块 → 删文档 / 删库。首次请求会建
`data/kb.db`（SQLite，无外部服务）。租户**只**走请求头 `X-Tenant`（缺省 `demo`），不接受前端传参。

## 自检

```bash
uv run python main.py
```

86 项，全离线（临时目录里的 SQLite，跑完即删）：白名单、魔数（PNG/PDF/docx/裸 zip 互相改名）、
2 MB 上限、同库去重、跨租户不共享、越权 404、删文档清块与任务、删库级联、切块、HTTP 层、孤儿行检查。

## 这个目录里有什么

| 文件 | 说明 |
| --- | --- |
| `main.py` | 4 张表 + 上传安检 + 8 个 API（另加首页）+ 86 项自检 |
| `static/index.html` | 建库 / 上传 / 看块 / 删除，外加 4 个攻击按钮 |
| `pyproject.toml` | 只有 `fastapi` / `httpx` / `python-dotenv` |

## 实测

一份 1000 字（2600 字节）的 md，切块上限 180 字；标「1884 字节」的行是另一份 768 字样例：

| 场景 | 输入 | 结果 |
| --- | --- | --- |
| 正常上传 | 2600 字节 md | 201，切出 **6 块**，无一块超过 180 字 |
| 同一字节重传 | 同名同内容 | `dedup=true` + 原 `doc_id`，表里仍 **1 行** |
| 连传 20 次 | 1884 字节 md | 5.3 ms 走完，`docs` 1 行、`chunks` 5 行 |
| 换租户传同字节 | 同字节 | 新 `doc_id`，sha256 前 12 位一样 → **不去重** |
| PNG 改名 .pdf | 70 字节 PNG | 400 `magic_mismatch`（内容像 png） |
| 2 MB + 1 字节 | 2097153 字节 | 400 `too_large` |
| 删文档 | 5 块 + 1 任务 | 回报 `chunks_removed=5 / jobs_removed=1`，再查为 0 |
| 删知识库 | 3 文档 | 回报 `docs_removed=3 / chunks_removed=3 / jobs_removed=3` |
| 越权读 | `globex` 拿 `acme` 的 kb_id | 404「知识库不存在」（不是 403） |
| HTTP 单次耗时中位 | TestClient ×25 | 上传 5.05 / 去重 4.87 / 拒绝 4.56 / 列表 4.81 ms |

## 接口

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/health` | 本租户的库/文档/块数、白名单、上限、外键是否开 |
| POST | `/kb` | 建库 `{name}` → 201 + `kb_id` |
| GET | `/kb` | 我的库列表（带 doc_count / chunk_count） |
| GET | `/kb/{kb_id}/docs` | 某库文档列表（带 sha256 前 12 位、块数） |
| POST | `/kb/{kb_id}/docs` | 上传 `{filename, content_b64}`，同库同内容去重 |
| GET | `/kb/{kb_id}/chunks?doc_id=` | 看块，验级联删除用 |
| DELETE | `/kb/{kb_id}/docs/{doc_id}` | 删文档，回报清掉多少块/任务 |
| DELETE | `/kb/{kb_id}` | 删库，回报连带的文档/块/任务数 |

## 常见坑

**1. `PRAGMA foreign_keys` 默认 OFF，`with sqlite3.connect()` 只 commit 不 close**

SQLite 每个连接默认关外键，`ON DELETE CASCADE` 写了也不生效，删 doc 后 chunk 全留着还不报错；
而 `with connect()` 是事务上下文，不会 close 连接。`_conn()` 三件事都手写：开外键、commit/rollback、close。

**2. 去重键少一个 `tenant_id` 就变成跨租户共享**

`UNIQUE(tenant_id, kb_id, sha256)` 才是对的。写成 `UNIQUE(sha256)` 时，第二个租户上传同一份
合同会拿到第一个租户的 `doc_id` —— 他删掉你的文件也没了，而且他读得到你的元数据。

**3. 越权返回 403 等于告诉对方「这个 id 存在」**

所有查询第一个条件都是 `tenant_id`，查不到就是 `NotFound` → 404。403 要先判断"存在但无权"，
那个判断本身就是一次存在性泄露。

**4. 魔数校验要比对「声明」和「实际」，不是查黑名单**

只做扩展名白名单，`invoice.pdf` 里塞 PNG 照样进流水线，下游解析器直接崩。这里 `sniff()`
返回实际类型，与声明的类型不等就拒；docx/xlsx/pptx 靠 zip 里的目录名区分。

## 验收清单

- [ ] `uv run python main.py` → 86 通过 / 0 失败
- [ ] 页面上传内置样例 md 两次，第二次显示「命中已有内容（没有新增行）」
- [ ] 「PNG 改名 .pdf」与「2 MB + 1 字节」两个按钮都返回 400
- [ ] 「跨租户读别人的库」返回 404，且提示里没有文件信息
- [ ] 看块之后删文档，块卡片清空，库行上的「块」变 0
- [ ] 能说出去重的作用域为什么是「租户 + 库 + 内容」

## 下一单元

Unit 23 接上这条流水线：上传成功只是 `pending`，把它变成 chunk 的是 worker ——
重试不能重复写块、租约过期要能被接管、失败要有 `error` 且不无限重试。
