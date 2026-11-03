# Unit 17 笔记 · 文档解析

> 阶段二真正的地基。解析阶段丢掉的页码，后面花多少钱买模型都找不回来。
>
> 状态：**三层全过** —— 环境 12/12、单元 30/30、真实样本 12/12，端到端冒烟通过（全程不联网）。

## 本次做了什么

- `parser.py`：`parse_bytes(name, data) -> ParsedDoc`
  - 原生解析 TXT / Markdown / HTML / CSV（纯标准库）
  - PDF 走 `pypdf`（逐页），DOCX 走 `python-docx`（段落 + 表格）
  - 编码探测：utf-8-sig → utf-8 → gb18030 → big5 → 替换兜底
  - 魔数复核：`%PDF-` / `PK\x03\x04`
- `main.py`：`/parse`、`/parse-text`、`/sample` + 30 项自检
- `check_env.py`：12 项环境自检（依赖、四种格式、拒绝路径）
- `make_fixtures.py` + `verify_samples.py`：11 个真实样本 + 12 条断言（实测表由它生成）
- `smoke.sh`：端到端（multipart 上传真实 PDF / DOCX、伪装 PDF 415）
- `static/index.html`：上传/样例 → 结构卡片 + 正文 + 警告

## 一、为什么输出的是 pages，不是一整段字符串

Citation 要回答"这句话在第 3 页"。如果解析阶段只留一整段文本，
Unit 21 就得把 PDF 重新解析一遍来反查页码 —— 那是返工。

`pages: list[str]` 已经够用，页号 = 下标 + 1：

```python
def page_of(self, offset: int) -> int:
    acc = 0
    for i, p in enumerate(self.pages):
        acc += len(p) + 1        # +1 是拼接时的换行
        if offset < acc:
            return i + 1
```

那个 `+1` 是最容易错的地方：`"\n".join(pages)` 会比 `sum(len(p))` 多出 n-1 个字符，
漏掉它，跨页的偏移就会整体前移一页。

## 二、编码：BOM 和 GBK 是两件事

### BOM 必须最先处理

`utf-8-sig` 要排在 `utf-8` 前面。否则 BOM（`\ufeff`）会变成正文第一个字符，
chunk 里带上它，检索时永远匹配不上 —— 而且肉眼完全看不出来。

自检里那条 `BOM 不残留在正文` 就是防这个。

### 中文 Windows 导出的 TXT 大概率是 GB18030

按 `utf-8 → gb18030 → big5` 顺序试，都能解就取第一个成功的。
全都失败才用 `errors="replace"` 兜底，并且必须留下警告 ——
**静默返回乱码比直接报错更糟**，用户会以为文件是好的。

## 三、为什么只归一全角字母数字，不用 NFKC

一开始我写的是 `unicodedata.normalize("NFKC", text)`，一行搞定。
跑完样例就发现不对：

```
输入：# 员工手册（2026 版）
NFKC：员工手册(2026 版) ← 全角括号被改了
```

问题有两个：

1. 中文排版变丑（，。：（）全变半角）
2. Citation 要展示**原文片段**，归一越多，展示的和用户 PDF 里的越对不上

所以改成只翻译 `０-９Ａ-Ｚａ-ｚ` 三段：

```python
_FULLWIDTH_ALNUM = {
    **{c: c - 0xFEE0 for c in range(0xFF10, 0xFF1A)},
    **{c: c - 0xFEE0 for c in range(0xFF21, 0xFF3B)},
    **{c: c - 0xFEE0 for c in range(0xFF41, 0xFF5B)},
}
```

`ＡＢＣ１２３` → `ABC123`，括号逗号原样不动。

## 四、扩展名不可信

```python
_MAGIC = ((b"%PDF-", "pdf"), (b"PK\x03\x04", "docx"))
```

- 声明 `.pdf` 但文件头不是 `%PDF-` → **拒绝**（改名的可执行文件常走这条路）
- 声明 `.txt` 但文件头是 `%PDF-` → 按 PDF 处理 + 警告（用户只是名字写错了）

注意：能解析 ≠ 安全。真正上线还要限制大小、隔离解析进程、限制解析超时 ——
pypdf 遇到构造过的 PDF 是可以吃满 CPU 的。本单元只做大小上限（20 MiB）。

## 五、DOCX 没有"页"

Word 的页码是排版结果，磁盘上的 `.docx` 里不存在这个信息。
硬编一个页号只会让引用指错地方，所以整篇按 1 页处理 + 警告。

要页码就先在 Word 里「另存为 PDF」—— 这是唯一可靠的做法。

## 六、实测（可复现）

`uv run python make_fixtures.py && uv run python verify_samples.py` 输出的真实结果：

| 样本 | kind | 页数 | 字数 | 编码 | warnings |
| --- | --- | ---: | ---: | --- | ---: |
| `utf8.md` | md | 1 | 77 | utf-8-sig | 0 |
| `bom.md` | md | 1 | 28 | utf-8-sig | 0 |
| `gbk.txt` | txt | 1 | 62 | gb18030 | 0 |
| `fullwidth.txt` | txt | 1 | 43 | utf-8-sig | 0 |
| `table.csv` | csv | 1 | 52 | — | 0 |
| `script.html` | html | 1 | 28 | utf-8-sig | 0 |
| `multi.pdf` | pdf | 2 | 158 | — | 0 |
| `simple.docx` | docx | 1 | 68 | — | 1 |
| `fake.pdf` / `blank.pdf` / `empty.md` | 拒绝 | — | — | — | — |

三条值得记住的读数：

1. **`utf8.md` 的 encoding 是 `utf-8-sig` 而不是 `utf-8`** —— 因为探测顺序里
   `utf-8-sig` 排在最前。它解普通 UTF-8 也一样正确（没有 BOM 就不剥任何东西），
   这是**故意的**：顺序反过来，带 BOM 的文件就会把 `\ufeff` 留在正文第一个字符里。
2. **DOCX 68 字、1 条警告**，而同样的内容在 Markdown 里是 77 字 ——
   DOCX 的表格被展开成「项目 / 额度」两列文本，段落之间没有 Markdown 的语法字符。
3. **`blank.pdf` 是被拒绝的**，不是"解析成功返回空"。

### 一处纸面承诺与实现的偏差（实测抓出来的）

README 原来写：「扫描版 PDF：解析成功但正文空，`warnings` 提示需要 OCR」。
真拿一个无文本层的 PDF 去跑，实际行为是：

```
ParseError: 解析结果为空：文件里没有可提取的文字
```

问题有两层：

- **理由错**：用户的文件是扫描件，不是"空文件"。报"结果为空"会把人引向错误方向。
- **信息丢**：`_pdf_pages` 明明已经往 `warnings` 里写了「第 1 页没有文本层，可能是扫描件」，
  但异常一抛，这个 list 就没人看得到了。

修法：在统一出口按 `kind` 分流，全空的 PDF 给出可执行的提示：

```python
if not text.strip():
    if kind == "pdf":
        raise ParseError(f"PDF 共 {len(pages)} 页但没有任何文本层，疑似扫描件，需要 OCR（本单元不提供）")
    raise ParseError("解析结果为空：文件里没有可提取的文字")
```

边界保持清晰：**部分页无文本层 → 照常返回 + warning；整篇无文本层 → 明确拒绝**。
前者还能用（图文混排的 PDF 很常见），后者返回一个空文档只会把问题推到下游。

## 完成标准对照

- [x] 四种格式都能出文本（TXT/MD/HTML/CSV 原生，PDF/DOCX 依赖就绪）
- [x] 保留文件名、页数、每页字数、编码
- [x] 空文件、超限、错扩展名、伪装 PDF 都有明确拒绝理由
- [x] `page_of()` 能把偏移映射回页号
- [x] 警告可见：部分页无文本层（扫描页）、DOCX 无分页、编码兜底
- [x] 整篇无文本层的 PDF：明确拒绝并提示需要 OCR（不是静默返回空）

## 一句话总结

解析这一步只做一件事：把"文件"变成"可追溯的文本"。
**能追溯到哪一页，比能提取多少字更重要** —— 前者决定引用能不能做，后者只是参数问题。

## 面试追问

1. **「扫描版 PDF 怎么处理？」**
   → 解析器只能拿到空文本，必须判定 `needs_ocr` 并走 OCR（PaddleOCR / 云 OCR）。
   工程上先做检测：整篇文本层为空、或单页字符数远低于页面面积预期，就标记为扫描件。
   不要在解析层偷偷接 OCR —— 它会拖慢整条流水线，且成本翻倍。

2. **「为什么要用魔数而不是扩展名？」**
   → 扩展名和 Content-Type 都是客户端说了算。上传接口如果只信这两样，
   攻击者可以传 `.pdf` 的可执行文件或构造的压缩炸弹。魔数至少能保证"这个文件确实是它声称的类型"。

3. **「PDF 表格怎么解析？」**
   → 文本层抽出来是流式的，表格结构会丢。要结构得用 `pdfplumber` 的 `extract_tables()`
   或版面分析模型（LayoutLM）。这是为什么很多 RAG 项目对表格另走一条链路。

4. **「编码探测为什么不用 chardet？」**
   → 可以用，但短文本误判率高，而且多一个依赖。中文场景下按
   `utf-8 → gb18030 → big5` 顺序试，覆盖 99% 的情况，还完全可控。
