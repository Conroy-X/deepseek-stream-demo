# Unit 17 · 文档解析

> 所有格式进，同一种结构出。解析阶段丢掉的页码和文件名，后面花多少钱买模型都找不回来。

**完成标准**：四种格式都能出文本，且 `ParsedDoc` 能回答"这段字来自哪个文件、第几页"。

## 快速开始

```bash
cd ~/ai-journey/unit17-doc-parsing
uv sync
uv run uvicorn main:app --reload --port 8017
```

打开 <http://127.0.0.1:8017/>：点内置样例或上传自己的文件，立刻看到统一结构。

> 端口 **8017**（Unit 15=8015、16=8016，延续"一单元一端口"）。

## 自检

```bash
uv run python main.py
```

30 项，全离线：编码（UTF-8 / BOM / GBK / 全角）、Markdown 语法剥离、HTML 去脚本、
CSV 行拼接、空文件/超限/错扩展名/伪装 PDF 的拒绝、页码定位、HTTP 层。

## 这个目录里有什么

| 文件 | 说明 |
| --- | --- |
| `parser.py` | 真正的解析器：`parse_bytes(name, data) -> ParsedDoc` |
| `main.py` | FastAPI 路由 + 30 项自检 |
| `static/index.html` | 上传/样例 → 看结构、页数、警告、正文 |
| `notes.md` | 编码坑、页码映射、实测数据、面试追问 |

## 输出契约（下游全依赖这个）

```python
ParsedDoc(name, kind, text, pages, meta, warnings)
```

- `pages` 是 `list[str]`，页号 = 下标 + 1。不做 `Page` 包装，少一层没用的东西。
- `page_of(offset)` 把字符偏移映射回页号 —— **Unit 21 的引用定位全靠它**。
- `warnings` 不是装饰：扫描件、DOCX 无分页、编码兜底，都必须显式说出来。

## 两个关键决定

**1. 扩展名不可信，必须用魔数复核**

```python
_MAGIC = ((b"%PDF-", "pdf"), (b"PK\x03\x04", "docx"))
```

改名成 `.pdf` 的文本文件会被直接拒绝；反过来，内容是真 PDF 但扩展名写错的，
按实际内容处理并给警告。只信 `Content-Type` 或扩展名的解析器，是文件上传漏洞的常见入口。

**2. DOCX 没有"页"**

Word 的页码是排版结果，文件里并不存在。强行编一个页号只会让引用指错地方。
所以 DOCX 整篇算 1 页，并在 `warnings` 里说清楚 —— 要页码就先转 PDF。

**3. 不用 NFKC 归一**

`unicodedata.normalize("NFKC", text)` 看着最省事，但它会把全角逗号、括号、冒号
全变成半角：`（2026 版）` → `(2026 版)`，`，` → `,`。中文排版变丑是小事，
Citation 要展示原文片段时对不上才是大事。

这里只归一全角字母数字（`０-９Ａ-Ｚａ-ｚ`），中文标点原样保留：

```python
_FULLWIDTH_ALNUM = {c: c - 0xFEE0 for c in range(0xFF10, 0xFF1A)}  # 只这三段
```

## 实测

| 输入 | 结果 | 备注 |
| --- | --- | --- |
| UTF-8 中文 Markdown | 正常 | 井号、链接语法被剥离，正文保留 |
| GB18030 中文 TXT | 正常 | 自动识别，`meta.encoding` 记为 `gb18030` |
| 带 BOM 的 UTF-8 | 正常 | BOM 不进入正文（否则检索永远匹配不上） |
| 全角 `ＡＢＣ１２３` | → `ABC123` | NFKC 归一 |
| HTML 含 `<script>` | 脚本内容被丢弃 | 只留可见文本 |
| 改名成 `.pdf` 的纯文本 | 拒绝，415 | 文件头校验 |
| 0 字节 / 超 20 MiB | 拒绝 | 不让内存先炸 |
| 扫描版 PDF | 解析成功但正文空 | `warnings` 提示需要 OCR |

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 支持格式、大小上限、PDF/DOCX 依赖是否就绪 |
| POST | `/parse` | 上传文件（multipart）→ 统一结构 |
| POST | `/parse-text` | 直接贴文本，不落盘 |
| GET | `/sample?name=` | 解析内置的 4 个样例 |

## 常见坑

- **先解码再判断格式**：`%PDF` 是字节，别等解码成 str 才检查。
- **PDF 单页失败不该毁掉整篇**：捕获单页异常，记 warning 继续。
- **`utf-8-sig` 必须排在 `utf-8` 前面**，否则 BOM 会留在正文第一个字符。
- **别在解析层切块**：Unit 18 才做 chunking，这里切了后面还要拼回去。

## 验收清单

- [ ] `uv run python main.py` → 30 通过 / 0 失败
- [ ] 页面上点「员工手册.md」，能看到 3 个小节和字数
- [ ] 上传一个 GBK 编码的 TXT，正文不乱码
- [ ] 把 `x.txt` 改名成 `x.pdf` 再上传，被 415 拒绝
- [ ] 上传一个真实的 PDF，页数 > 1，`pages` 每页字数不同
- [ ] 能解释「为什么 DOCX 没有页号」

## 下一单元

Unit 18 拿这里的 `pages` 去切块：固定长度 / 递归 / 语义三种策略，
用固定问题集对比召回，而不是凭肉眼看"切得挺整齐"。
