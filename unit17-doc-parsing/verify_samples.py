"""Unit 17 实测：把 fixtures/ 里的每个样本真解析一遍，逐条断言。

    uv run python make_fixtures.py     # 先造样本
    uv run python verify_samples.py    # 再跑这个

输出的表可以直接贴进 notes.md —— README 里那张"实测表"就是这张表，
区别是这里有断言，说了"应该怎样"就一定会被检查。
"""

from __future__ import annotations

from pathlib import Path

from parser import MAX_BYTES, ParseError, parse_bytes

FIX = Path(__file__).with_name("fixtures")


def parse(name: str):
    return parse_bytes(name, (FIX / name).read_bytes())


def main() -> int:
    if not FIX.exists():
        print("没有 fixtures/，先跑：uv run python make_fixtures.py")
        return 2

    ok = bad = 0
    rows: list[str] = []

    def case(fname: str, check, desc: str) -> None:
        nonlocal ok, bad
        try:
            doc = parse(fname)
            detail = check(doc)
            ok += 1
            print(f"  ✓ {fname:14} {detail}")
            rows.append(
                f"| {fname} | {doc.kind} | {doc.page_count} | {doc.chars} | "
                f"{doc.meta.get('encoding', '—')} | {len(doc.warnings)} |"
            )
        except AssertionError as e:
            bad += 1
            print(f"  ✗ {fname:14} {e}")
            rows.append(f"| {fname} | **FAIL** | — | — | — | — |")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"  ✗ {fname:14} 异常：{type(e).__name__}: {e}")
            rows.append(f"| {fname} | 拒绝({type(e).__name__}) | — | — | — | — |")

    def expect_raises(fname: str, needle: str) -> None:
        nonlocal ok, bad
        try:
            parse(fname)
        except ParseError as e:
            if needle in str(e):
                ok += 1
                print(f"  ✓ {fname:14} 正确拒绝：{e}")
                rows.append(f"| {fname} | 拒绝 | — | — | — | — |")
            else:
                bad += 1
                print(f"  ✗ {fname:14} 拒绝理由不含「{needle}」：{e}")
        except Exception as e:  # noqa: BLE001
            bad += 1
            print(f"  ✗ {fname:14} 异常类型不对：{type(e).__name__}")
        else:
            bad += 1
            print(f"  ✗ {fname:14} 应该拒绝却没拒绝")
            rows.append(f"| {fname} | **未拒绝** | — | — | — | — |")

    print("=" * 78)
    print("Unit 17 · 真实样本实测")
    print("=" * 78)

    print("\n[1] 文本类：语法剥离 / 编码 / 归一")
    case("utf8.md", lambda d: _md(d), "Markdown")
    case("bom.md", lambda d: _bom(d), "BOM")
    case("gbk.txt", lambda d: _gbk(d), "GB18030")
    case("fullwidth.txt", lambda d: _fullwidth(d), "全角")
    case("table.csv", lambda d: _csv(d), "CSV")
    case("script.html", lambda d: _html(d), "HTML")

    print("\n[2] PDF：页数 / 页码映射 / 扫描件")
    case("multi.pdf", lambda d: _pdf(d), "两页")
    expect_raises("blank.pdf", "扫描")

    print("\n[3] DOCX：无页概念")
    case("simple.docx", lambda d: _docx(d), "段落+表格")

    print("\n[4] 拒绝路径")
    expect_raises("fake.pdf", "文件头")
    expect_raises("empty.md", "空文件")

    print("\n[5] 边界：超限")
    huge = b"x" * (MAX_BYTES + 1)
    try:
        parse_bytes("huge.txt", huge)
        print("  ✗ 超限未拒绝")
        bad += 1
    except ParseError as e:
        ok += 1
        print(f"  ✓ 超限正确拒绝：{e}")

    print("\n" + "=" * 78)
    print("可直接贴进 notes.md 的表：")
    print("| 样本 | kind | 页数 | 字数 | 编码 | warnings |")
    print("| --- | --- | ---: | ---: | --- | ---: |")
    for r in rows:
        print(r)
    print("=" * 78)
    print(f"通过 {ok} / 失败 {bad}")
    return 1 if bad else 0


def _md(d) -> str:
    assert d.kind == "md", f"kind={d.kind}"
    assert "#" not in d.text.split("\n")[0], "标题的 # 没被剥离"
    assert "https://hr.example.com" not in d.text or "内网" in d.text, "链接处理异常"
    return f"{d.chars} 字，{d.page_count} 页"


def _bom(d) -> str:
    assert "\ufeff" not in d.text, "BOM 残留在正文里"
    assert d.text.lstrip().startswith("带 BOM 的文档"), f"正文开头不对：{d.text[:20]!r}"
    return f"{d.chars} 字，BOM 已剥离（meta.encoding={d.meta.get('encoding')}）"


def _gbk(d) -> str:
    assert d.meta.get("encoding") in ("gb18030", "gbk"), f"编码={d.meta.get('encoding')}"
    assert "退款" in d.text, "中文没解出来"
    return f"{d.chars} 字，encoding={d.meta.get('encoding')}"


def _fullwidth(d) -> str:
    assert "ABC123" in d.text, "全角字母数字没转半角"
    assert "（括号）" in d.text, "全角括号被误改（不该用 NFKC）"
    return "ABC123 ✓，中文标点保留 ✓"


def _csv(d) -> str:
    assert d.kind == "csv", f"kind={d.kind}"
    assert d.meta.get("csv_rows") == 3, f"行数={d.meta.get('csv_rows')}"
    assert "含奖金" in d.text, "带引号的字段被截断"
    return f"{d.meta.get('csv_rows')} 行"


def _html(d) -> str:
    assert d.kind == "html", f"kind={d.kind}"
    assert "alert(" not in d.text, "script 内容没被丢弃"
    assert "停机" in d.text, "正文丢了"
    return "script 已剥离"


def _pdf(d) -> str:
    assert d.kind == "pdf", f"kind={d.kind}"
    assert d.page_count == 2, f"页数={d.page_count}"
    assert "Page one" in d.text and "Page two" in d.text, "正文抽取不完整"
    idx = d.text.index("Page two")
    page = d.page_of(idx)
    assert page == 2, f"page_of 错：偏移 {idx} → 第 {page} 页（应为 2）"
    return f"{d.page_count} 页 / {d.chars} 字，page_of 定位第 2 页 ✓"


def _docx(d) -> str:
    assert d.kind == "docx", f"kind={d.kind}"
    assert d.page_count == 1, f"页数={d.page_count}（DOCX 应整篇 1 页）"
    assert "年假" in d.text, "段落文本丢了"
    assert "每晚 500 元" in d.text, "表格文本丢了"
    assert any("页" in w for w in d.warnings), f"没有页数警告：{d.warnings}"
    return f"1 页 / {d.chars} 字，表格已展开，警告 {len(d.warnings)} 条"


if __name__ == "__main__":
    raise SystemExit(main())
