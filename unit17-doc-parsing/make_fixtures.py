"""生成 Unit 17 的真实测试样本（fixtures/）。

为什么不用手写死数据：README 里那张"实测表"必须能被一条命令复现，
否则数字迟早和代码对不上。这里的每个文件都对应一个真实的解析分支：

    utf8.md       Markdown 语法剥离
    bom.md        带 BOM 的 UTF-8（BOM 不能进正文）
    gbk.txt       GB18030 编码（中文 Windows 导出最常见的坑）
    fullwidth.txt 全角字母数字（只归一半角，中文标点不动）
    table.csv     逗号/引号/换行的行拼接
    script.html   含 <script>（脚本内容必须被丢弃）
    fake.pdf      改名成 .pdf 的纯文本（必须 415 拒绝）
    multi.pdf     两页、ASCII 文本的真实 PDF（验证页数与 page_of）
    blank.pdf     无文本层的 PDF（扫描件路径）
    simple.docx   两段文字 + 一张表（DOCX 无页的概念）
    empty.md      0 字节（必须拒绝）

    uv run python make_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

FIX = Path(__file__).with_name("fixtures")


def _pdf(pages_text: list[str]) -> bytes:
    """手写一个最小合法 PDF。

    不引入 reportlab 之类的依赖：RAG 单元不需要"生成 PDF"的能力，
    这里只是为了造出 pypdf 能读、页码真实的样本。
    文本用 ASCII（Helvetica 只有标准编码，中文需要 CID 字体，另一套复杂度）。
    """
    n = len(pages_text)
    font_id = 3 + 2 * n
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n))
    objs: dict[int, str] = {
        1: "<< /Type /Catalog /Pages 2 0 R >>",
        2: f"<< /Type /Pages /Kids [{kids}] /Count {n} >>",
        font_id: "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for i, text in enumerate(pages_text):
        page_id, content_id = 3 + 2 * i, 4 + 2 * i
        objs[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        )
        stream = f"BT /F1 14 Tf 72 760 Td ({text}) Tj ET"
        objs[content_id] = f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream"

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for oid in sorted(objs):
        offsets[oid] = len(out)
        out += f"{oid} 0 obj\n{objs[oid]}\nendobj\n".encode("latin-1")
    xref_pos = len(out)
    max_id = max(objs) + 1
    out += f"xref\n0 {max_id}\n".encode()
    out += b"0000000000 65535 f \n"
    for oid in range(1, max_id):
        if oid in offsets:
            out += f"{offsets[oid]:010d} 00000 n \n".encode()
        else:
            out += b"0000000000 65535 f \n"
    out += (
        f"trailer\n<< /Size {max_id} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return bytes(out)


def main() -> int:
    FIX.mkdir(exist_ok=True)

    (FIX / "utf8.md").write_text(
        "# 员工手册（2026 版）\n\n"
        "## 一、年假\n\n入职满一年享 5 天年假，满三年 10 天。详见 [内网](https://hr.example.com)。\n\n"
        "## 二、报销\n\n差旅报销需在出差结束后 10 个工作日内提交。\n",
        encoding="utf-8",
    )
    (FIX / "bom.md").write_bytes(
        "\ufeff# 带 BOM 的文档\n\n这一行前面的 BOM 不能进正文。\n".encode("utf-8")
    )
    (FIX / "gbk.txt").write_bytes(
        "退款政策说明\n\n1. 未发货订单可全额退款，1 个工作日内处理。\n"
        "2. 已发货订单需先拒收，仓库签收后 3 个工作日内退款。\n".encode("gb18030")
    )
    (FIX / "fullwidth.txt").write_text(
        "型号 ＡＢＣ１２３ 的全角字母数字应转成 ABC123，但（括号）和，逗号，保留全角。\n",
        encoding="utf-8",
    )
    (FIX / "table.csv").write_text(
        '月份,收入,支出\n1月,12000,8000\n2月,"15000,含奖金",9000\n',
        encoding="utf-8",
    )
    (FIX / "script.html").write_text(
        "<html><head><title>公告</title><script>alert('不该出现');</script></head>"
        "<body><h1>系统维护</h1><p>本周六 02:00-04:00 停机。</p></body></html>",
        encoding="utf-8",
    )

    (FIX / "multi.pdf").write_bytes(
        _pdf(
            [
                "Page one: annual leave is 5 days after one year.",
                "Page two: reimbursement needs invoices within 10 working days. "
                "This page has more text so char counts differ.",
            ]
        )
    )
    (FIX / "blank.pdf").write_bytes(_pdf([""]))
    (FIX / "fake.pdf").write_bytes("这其实是纯文本，只是名字叫 .pdf\n".encode("utf-8"))
    (FIX / "empty.md").write_bytes(b"")

    # DOCX：段落 + 表格（python-docx 需要时才 import，避免 check_env 之外的地方硬依赖）
    from docx import Document

    doc = Document()
    doc.add_heading("员工手册（2026 版）", level=1)
    doc.add_paragraph("入职满一年享 5 天年假，满三年 10 天，满五年 15 天。")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "额度"
    table.cell(1, 0).text = "差旅住宿"
    table.cell(1, 1).text = "每晚 500 元"
    doc.save(FIX / "simple.docx")

    for p in sorted(FIX.iterdir()):
        print(f"  {p.name:16} {p.stat().st_size:>7} 字节")
    print(f"\n共 {len(list(FIX.iterdir()))} 个样本，目录 {FIX}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
