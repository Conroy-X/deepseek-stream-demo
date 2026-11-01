"""Unit 17 · 文档解析：把 PDF / Markdown / TXT / DOCX 统一成 text + pages + meta。

后面所有单元（切分、向量化、引用）都只认这一个结构，
所以这里定下的字段就是整条 RAG 管线的地基：

    ParsedDoc(name, kind, text, pages, meta, warnings)

为什么要有 pages：
    Citation 要能说"这句话在第 3 页"。如果这里只留一整段文本，
    Unit 21 就得回头重解析一遍 —— 那是返工，不是功能。

为什么 pages 是 list[str] 而不是 list[Page]：
    Page 只会带来一层没必要的包装。页号 = 下标 + 1，够用了。
"""

from __future__ import annotations

import csv
import html as htmllib
import io
import re
from dataclasses import dataclass, field
from pathlib import PurePath

# 允许上传的扩展名。注意：扩展名只是"客户端声明"，下面 detect_kind 会用魔数复核。
SUPPORTED = (".txt", ".md", ".markdown", ".html", ".htm", ".csv", ".pdf", ".docx")

MAX_BYTES = 20 * 1024 * 1024  # 20 MiB，超过直接拒绝，别让内存先炸

_MAGIC = (
    (b"%PDF-", "pdf"),
    (b"PK\x03\x04", "docx"),  # docx 本质是 zip
)


class ParseError(Exception):
    """解析失败。所有失败都要变成这一种异常，路由层才好统一回 4xx。"""


# 全角字母数字 → 半角。范围刻意只覆盖 ０-９Ａ-Ｚａ-ｚ，不碰中文标点
_FULLWIDTH_ALNUM = {
    **{c: c - 0xFEE0 for c in range(0xFF10, 0xFF1A)},
    **{c: c - 0xFEE0 for c in range(0xFF21, 0xFF3B)},
    **{c: c - 0xFEE0 for c in range(0xFF41, 0xFF5B)},
}


@dataclass
class ParsedDoc:
    name: str
    kind: str
    text: str
    pages: list[str]
    meta: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page_of(self, offset: int) -> int:
        """字符偏移 → 页号（从 1 开始）。Citation 靠它把片段定位到页。"""
        if offset <= 0:
            return 1
        acc = 0
        for i, p in enumerate(self.pages):
            acc += len(p) + 1  # +1 是拼接时的换行
            if offset < acc:
                return i + 1
        return max(1, len(self.pages))

    def preview(self, n: int = 600) -> str:
        return self.text[:n] + ("…" if self.chars > n else "")

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "chars": self.chars,
            "page_count": self.page_count,
            "pages": [{"no": i + 1, "chars": len(p)} for i, p in enumerate(self.pages)],
            "text": self.text,
            "preview": self.preview(),
            "meta": self.meta,
            "warnings": self.warnings,
        }


def normalize(text: str) -> str:
    """统一空白与全角字母数字。看着无聊，但直接影响后面的 chunk 大小与检索。

    这里**故意不用** `unicodedata.normalize("NFKC")`：NFKC 会把全角逗号、括号、冒号
    也一起变成半角，中文排版变丑，而且 Citation 要展示原文片段，改动越多越对不上。
    所以只归一"全角字母数字"这一段（０-９ Ａ-Ｚ ａ-ｚ）。
    """
    text = text.translate(_FULLWIDTH_ALNUM)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def decode_bytes(data: bytes) -> tuple[str, str]:
    """返回 (文本, 实际编码)。

    顺序有讲究：utf-8-sig 放最前，否则 BOM 会变成正文第一个字符，
    chunk 里出现 \\ufeff，检索时永远匹配不上。
    """
    for enc in ("utf-8-sig", "utf-8", "gb18030", "big5"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    # 兜底：不抛异常，但要留下警告，别让用户以为文件是好的
    return data.decode("utf-8", errors="replace"), "utf-8(replace)"


def detect_kind(name: str, data: bytes) -> tuple[str, list[str]]:
    """扩展名 + 魔数双重复核。返回 (kind, warnings)。"""
    warnings: list[str] = []
    ext = PurePath(name).suffix.lower()
    by_ext = {
        ".txt": "txt", ".md": "md", ".markdown": "md",
        ".html": "html", ".htm": "html", ".csv": "csv",
        ".pdf": "pdf", ".docx": "docx",
    }.get(ext)
    if by_ext is None:
        raise ParseError(f"不支持的扩展名 {ext or '(无)'}，支持：{', '.join(SUPPORTED)}")

    by_magic = next((k for sig, k in _MAGIC if data[:8].startswith(sig)), None)

    # PDF/DOCX 有自己的魔数，必须对得上
    if by_ext in ("pdf", "docx"):
        if by_magic != by_ext:
            raise ParseError(
                f"扩展名声明是 {by_ext}，但文件头不是 —— 可能是改过名的文件，拒绝解析"
            )
        return by_ext, warnings

    # 反过来：普通文本扩展名，内容却是 PDF/DOCX，说明扩展名写错了
    if by_magic:
        warnings.append(f"扩展名是 {ext}，但文件头是 {by_magic}，已按实际内容处理")
        return by_magic, warnings

    return by_ext, warnings


def _markdown_to_text(raw: str) -> str:
    """去掉 Markdown 语法，只留可读文本。保留行结构，因为标题层级对切分有用。"""
    t = re.sub(r"```.*?```", lambda m: m.group(0).strip("`"), raw, flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)            # 图片
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)        # 链接只留文字
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)   # 标题井号
    t = re.sub(r"(\*\*|__|~~|`)", "", t)                  # 行内强调
    t = re.sub(r"^\s*[-*+]\s+", "- ", t, flags=re.M)      # 统一列表符号
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)            # 引用块
    return t


def _html_to_text(raw: str) -> str:
    t = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return htmllib.unescape(t)


def _csv_to_text(raw: str) -> tuple[str, int]:
    rows = list(csv.reader(io.StringIO(raw)))
    lines = [" | ".join(c.strip() for c in r if c.strip()) for r in rows if any(c.strip() for c in r)]
    return "\n".join(lines), len(rows)


def _pdf_pages(data: bytes, warnings: list[str]) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - 依赖缺失路径
        raise ParseError("解析 PDF 需要 pypdf：uv add pypdf") from e

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:
        raise ParseError(f"PDF 打不开：{e}") from e

    out: list[str] = []
    for i, page in enumerate(reader.pages, 1):
        try:
            t = page.extract_text() or ""
        except Exception as e:  # noqa: BLE001 - 单页失败不该毁掉整个文档
            warnings.append(f"第 {i} 页抽取失败：{type(e).__name__}")
            t = ""
        if not t.strip():
            warnings.append(f"第 {i} 页没有文本层，可能是扫描件（需要 OCR，本单元不做）")
        out.append(normalize(t))
    if not out:
        raise ParseError("PDF 没有任何页面")
    return out


def _docx_pages(data: bytes, warnings: list[str]) -> list[str]:
    try:
        import docx
    except ImportError as e:  # pragma: no cover
        raise ParseError("解析 DOCX 需要 python-docx：uv add python-docx") from e

    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise ParseError(f"DOCX 打不开：{e}") from e

    blocks = [p.text for p in d.paragraphs if p.text.strip()]
    for t in d.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    warnings.append("DOCX 没有固定分页，整篇按 1 页处理；要页码请在 Word 里转成 PDF")
    return [normalize("\n".join(blocks))]


def parse_bytes(name: str, data: bytes) -> ParsedDoc:
    """唯一的入口。任何格式进来，出的都是 ParsedDoc。"""
    if not data:
        raise ParseError("空文件（0 字节）")
    if len(data) > MAX_BYTES:
        raise ParseError(f"文件 {len(data)} 字节，超过上限 {MAX_BYTES}")

    kind, warnings = detect_kind(name, data)
    meta: dict = {"bytes": len(data), "ext": PurePath(name).suffix.lower()}

    if kind == "pdf":
        pages = _pdf_pages(data, warnings)
        meta["pdf_pages"] = len(pages)
    elif kind == "docx":
        pages = _docx_pages(data, warnings)
    elif kind == "csv":
        text, rows = _csv_to_text(decode_bytes(data)[0])
        pages = [normalize(text)]
        meta["csv_rows"] = rows
    else:
        raw, enc = decode_bytes(data)
        meta["encoding"] = enc
        if enc.endswith("(replace)"):
            warnings.append("无法确定编码，已用替换字符兜底，正文可能有乱码")
        if kind == "md":
            text = _markdown_to_text(raw)
        elif kind == "html":
            text = _html_to_text(raw)
        else:
            text = raw
        pages = [normalize(text)]

    text = "\n".join(pages)
    if not text.strip():
        raise ParseError("解析结果为空：文件里没有可提取的文字")

    meta["lines"] = text.count("\n") + 1
    meta["chars"] = len(text)
    return ParsedDoc(name=name, kind=kind, text=text, pages=pages, meta=meta, warnings=warnings)


# ---------------------------------------------------------------------------
# 内置样例：页面和自检都用它，不用你自己准备文件
# ---------------------------------------------------------------------------
SAMPLES: dict[str, tuple[str, str]] = {
    "员工手册.md": (
        "md",
        """# 员工手册（2026 版）

## 一、年假

入职满一年享 5 天年假，满三年 10 天，满五年 15 天。
年假需提前 3 个工作日在系统提交申请，由直属主管审批。

## 二、报销

差旅报销需在出差结束后 10 个工作日内提交，附发票原件与行程单。
超过 5000 元的报销需部门总监二次审批。

## 三、退款

客户在签收后 7 天内可申请无理由退款，退款将在 3–5 个工作日原路退回。
""",
    ),
    "退款政策.txt": (
        "txt",
        """退款政策说明

1. 未发货订单：可随时申请全额退款，1 个工作日内处理。
2. 已发货订单：需先拒收或寄回，仓库签收后 3 个工作日内退款。
3. 虚拟商品：一经激活不支持退款，未激活可 7 天内申请。
""",
    ),
    "公告.html": (
        "html",
        """<html><head><style>.x{color:red}</style></head><body>
<h1>系统维护公告</h1>
<p>本周六 02:00–04:00 进行数据库维护，期间<b>无法下单</b>。</p>
<script>console.log("这段不该出现在正文里")</script>
</body></html>""",
    ),
    "对账单.csv": (
        "csv",
        """月份,收入,支出
1月,12000,8000
2月,15000,9000
""",
    ),
}
