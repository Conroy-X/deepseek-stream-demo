"""Unit 17 环境自检 —— 坐下第一件事先跑这个。

    uv run python check_env.py

只检查，不改任何文件。它回答一个问题：**这台机器现在能不能解析 PDF/DOCX**，
而不是等写完代码、跑到一半才发现 pypdf 没装。
"""

from __future__ import annotations

import platform
import sys

from parser import MAX_BYTES, ParseError, parse_bytes

ok = bad = 0


def check(name: str, fn) -> bool:
    global ok, bad
    try:
        detail = fn()
        ok += 1
        print(f"  ✓ {name:34} {detail}")
        return True
    except Exception as e:  # noqa: BLE001
        bad += 1
        print(f"  ✗ {name:34} {type(e).__name__}: {e}")
        return False


def dep(mod: str):
    def _inner():
        m = __import__(mod)
        return getattr(m, "__version__", "已安装")

    return _inner


def main() -> int:
    print("=" * 74)
    print("Unit 17 · 文档解析 环境自检")
    print("=" * 74)

    print("\n[1] Python 与解释器")
    check("Python 版本 ≥ 3.11", lambda: platform.python_version() if sys.version_info >= (3, 11) else (_ for _ in ()).throw(RuntimeError(sys.version)))
    check("执行的是 uv 的虚拟环境", lambda: sys.prefix if ".venv" in sys.prefix else (_ for _ in ()).throw(RuntimeError("不在 .venv 里，用 uv run 跑")))

    print("\n[2] 解析依赖（PDF/DOCX 是可选的，缺了只能吃 TXT/MD/HTML/CSV）")
    check("pypdf", dep("pypdf"))
    check("python-docx", dep("docx"))

    print("\n[3] 四种原生格式能出文本")
    check(
        "Markdown",
        lambda: f"{parse_bytes('a.md', '# 标题\n\n正文一句。'.encode()).chars} 字",
    )
    check(
        "纯文本 + GBK",
        lambda: f"{parse_bytes('a.txt', '退款政策'.encode('gb18030')).meta['encoding']}",
    )
    check(
        "HTML 去脚本",
        lambda: "script 已剥离"
        if "alert" not in parse_bytes("a.html", b"<p>ok</p><script>alert(1)</script>").text
        else (_ for _ in ()).throw(RuntimeError("script 没被剥离")),
    )
    check(
        "CSV 行拼接",
        lambda: f"{parse_bytes('a.csv', '月份,收入\n1月,12000'.encode()).meta['csv_rows']} 行",
    )

    print("\n[4] PDF / DOCX（依赖缺失时这两项会红，其余仍可用）")
    check("PDF 能读页数（需 pypdf）", lambda: _pdf_probe())
    check("DOCX 整篇 1 页 + 警告（需 python-docx）", lambda: _docx_probe())

    print("\n[5] 拒绝路径")
    check(
        "空文件被拒绝",
        lambda: "正确"
        if _raises(b"", "a.txt")
        else (_ for _ in ()).throw(RuntimeError("空文件没被拒绝")),
    )
    check(
        "伪装 PDF 被拒绝",
        lambda: "正确"
        if _raises(b"not a pdf", "a.pdf")
        else (_ for _ in ()).throw(RuntimeError("改名文件没被拒绝")),
    )
    print(f"      上限 {MAX_BYTES // 1024 // 1024} MiB")

    print("\n[6] 测试样本（端到端实测用）")
    from pathlib import Path

    fx = Path(__file__).with_name("fixtures")
    n = len(list(fx.iterdir())) if fx.exists() else 0
    print(f"  {'fixtures/ 已有样本' if n else '⚠ fixtures/ 为空，先跑 make_fixtures.py':34} {n} 个")

    print("\n" + "=" * 74)
    print(f"通过 {ok} / 失败 {bad}")
    if bad == 0:
        print("环境就绪。下一步：uv run python main.py（30 项）→ verify_samples.py（12 项）")
    print("=" * 74)
    return 1 if bad else 0


def _pdf_probe() -> str:
    from pathlib import Path

    p = Path(__file__).with_name("fixtures") / "multi.pdf"
    if not p.exists():
        raise RuntimeError("没有 fixtures/multi.pdf，先跑 make_fixtures.py")
    d = parse_bytes("multi.pdf", p.read_bytes())
    return f"{d.page_count} 页"


def _docx_probe() -> str:
    from pathlib import Path

    p = Path(__file__).with_name("fixtures") / "simple.docx"
    if not p.exists():
        raise RuntimeError("没有 fixtures/simple.docx，先跑 make_fixtures.py")
    d = parse_bytes("simple.docx", p.read_bytes())
    if d.page_count != 1:
        raise RuntimeError(f"DOCX 页数 {d.page_count}，应为 1")
    return f"1 页 / {len(d.warnings)} 条警告"


def _raises(data: bytes, name: str) -> bool:
    try:
        parse_bytes(name, data)
    except ParseError:
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
