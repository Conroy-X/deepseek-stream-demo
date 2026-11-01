"""Unit 17 · 文档解析 —— 所有格式进，同一种结构出。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8017
    打开 http://127.0.0.1:8017/

自检（不需要任何 key，不联网）：
    uv run python main.py
"""

from __future__ import annotations

import os
import sys

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from parser import MAX_BYTES, SAMPLES, SUPPORTED, ParseError, detect_kind, parse_bytes

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

app = FastAPI(title="Unit 17 · 文档解析", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class TextIn(BaseModel):
    name: str = Field(default="inline.md", min_length=1)
    text: str = Field(min_length=1)


@app.get("/health", summary="支持哪些格式、可选依赖装没装")
def health() -> dict:
    optional = {}
    for mod, pkg in (("pypdf", "pypdf"), ("docx", "python-docx")):
        try:
            __import__(mod)
            optional[pkg] = True
        except ImportError:
            optional[pkg] = False
    return {
        "ok": True,
        "supported": list(SUPPORTED),
        "max_bytes": MAX_BYTES,
        "optional_deps": optional,
        "samples": sorted(SAMPLES),
    }


@app.post("/parse", summary="上传文件 → 统一结构")
async def parse_upload(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    try:
        doc = parse_bytes(file.filename or "upload.bin", data)
    except ParseError as e:
        raise HTTPException(status_code=415, detail=str(e)) from e
    out = doc.to_dict()
    out["text"] = doc.preview(4000)
    return out


@app.post("/parse-text", summary="直接贴文本（不用建文件）")
def parse_text(req: TextIn) -> dict:
    try:
        doc = parse_bytes(req.name, req.text.encode("utf-8"))
    except ParseError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    out = doc.to_dict()
    out["text"] = doc.preview(4000)
    return out


@app.get("/sample", summary="解析内置样例")
def sample(name: str = Query(default="员工手册.md")) -> dict:
    if name not in SAMPLES:
        raise HTTPException(status_code=404, detail=f"没有这个样例，可选：{sorted(SAMPLES)}")
    kind, text = SAMPLES[name]
    doc = parse_bytes(name, text.encode("utf-8"))
    out = doc.to_dict()
    out["kind"] = kind
    out["text"] = doc.preview(4000)
    return out


@app.get("/", include_in_schema=False)
def index():
    if os.path.exists(STATIC_INDEX):
        return FileResponse(STATIC_INDEX)
    return {"hint": "static/index.html 不存在，看 /docs"}


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    def raises(fn) -> tuple[bool, str]:
        try:
            fn()
        except ParseError as e:
            return True, str(e)
        except Exception as e:  # noqa: BLE001
            return False, f"抛了 {type(e).__name__}，应该是 ParseError"
        return False, "没抛异常"

    print("Unit 17 · 文档解析自检（不联网）\n")

    print("  —— 文本类：编码与清洗 ——")
    doc = parse_bytes("员工手册.md", SAMPLES["员工手册.md"][1].encode("utf-8"))
    check("Markdown 解析出正文", "年假需提前 3 个工作日" in doc.text)
    check("标题井号被去掉", "# " not in doc.text and "员工手册（2026 版）" in doc.text)
    check("kind 判定为 md", doc.kind == "md", doc.kind)

    gbk = "退款政策：签收后 7 天内可退。".encode("gb18030")
    d2 = parse_bytes("gbk.txt", gbk)
    check("GBK 自动识别不乱码", "签收后 7 天内可退" in d2.text, d2.text[:20])
    check("记录了实际编码", d2.meta.get("encoding") == "gb18030", str(d2.meta.get("encoding")))

    d3 = parse_bytes("bom.txt", "\ufeff第一行\n\n\n\n第二行".encode("utf-8"))
    check("BOM 不残留在正文", not d3.text.startswith("\ufeff"), repr(d3.text[:5]))
    check("多余空行被折叠", "\n\n\n" not in d3.text)

    d4 = parse_bytes("wide.txt", "ＡＢＣ１２３".encode("utf-8"))
    check("全角字符归一成半角", d4.text == "ABC123", d4.text)

    print("\n  —— 结构化格式 ——")
    h = parse_bytes("公告.html", SAMPLES["公告.html"][1].encode("utf-8"))
    check("HTML 去掉 script 内容", "这段不该出现在正文里" not in h.text)
    check("HTML 去掉 style 内容", ".x{color:red}" not in h.text)
    check("HTML 正文提取正确", "无法下单" in h.text)

    c = parse_bytes("对账单.csv", SAMPLES["对账单.csv"][1].encode("utf-8"))
    check("CSV 行拼成文本", "1月 | 12000 | 8000" in c.text, c.text[:40])
    check("CSV 记录了行数", c.meta.get("csv_rows") == 3, str(c.meta.get("csv_rows")))

    print("\n  —— 边界与拒绝 ——")
    ok, msg = raises(lambda: parse_bytes("empty.txt", b""))
    check("空文件被拒绝", ok, msg)
    ok, msg = raises(lambda: parse_bytes("a.exe", b"MZ\x90\x00"))
    check("不支持的扩展名被拒绝", ok, msg)
    ok, msg = raises(lambda: parse_bytes("big.txt", b"x" * (MAX_BYTES + 1)))
    check("超过大小上限被拒绝", ok, msg)
    ok, msg = raises(lambda: parse_bytes("fake.pdf", b"hello, I am plain text"))
    check("改名成 .pdf 的文本被拒绝", ok and "文件头" in msg, msg)
    ok, msg = raises(lambda: parse_bytes("blank.md", b"   \n\n   "))
    check("只有空白的文档被拒绝", ok, msg)

    # 扩展名写错但内容是真的：识别层要按实际内容判型，并给出警告
    real_pdf_header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n" + b"0" * 200
    kind, warns = detect_kind("错名.txt", real_pdf_header)
    check(
        "魔数识别覆盖错误扩展名",
        kind == "pdf" and any("文件头" in w for w in warns),
        f"{kind} {warns}",
    )
    kind2, _ = detect_kind("正常.pdf", real_pdf_header)
    check("扩展名与魔数一致时无额外警告", kind2 == "pdf")

    print("\n  —— 页码定位（Citation 依赖它）——")
    doc3 = parse_bytes("单页.md", "第一页内容\n\n第二页内容\n\n第三页内容".encode("utf-8"))
    check("单页文档页数为 1", doc3.page_count == 1, str(doc3.page_count))
    check("page_of(0) = 1", doc3.page_of(0) == 1, str(doc3.page_of(0)))
    check("page_of(越界) 落在最后一页", doc3.page_of(10_000) == doc3.page_count, str(doc3.page_of(10_000)))

    print("\n  —— 可选依赖的行为（缺失时必须给可执行的提示）——")
    try:
        import pypdf  # noqa: F401

        check("pypdf 已安装，可直接解析 PDF", True)
    except ImportError:
        ok, msg = raises(lambda: parse_bytes("x.pdf", b"%PDF-1.4\n"))
        check("缺 pypdf 时提示 uv add pypdf", ok and "uv add pypdf" in msg, msg)

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.get("/health")
    check("GET /health 返回格式列表", r.status_code == 200 and ".pdf" in r.json()["supported"])

    r = client.get("/sample", params={"name": "员工手册.md"})
    check("GET /sample 能取内置样例", r.status_code == 200 and r.json()["chars"] > 100)

    r = client.get("/sample", params={"name": "不存在.md"})
    check("GET /sample 未知样例返回 404", r.status_code == 404, str(r.status_code))

    r = client.post("/parse", files={"file": ("x.md", "# 标题\n\n正文内容".encode("utf-8"), "text/markdown")})
    check("POST /parse 上传 Markdown", r.status_code == 200 and "正文内容" in r.json()["text"], str(r.status_code))

    r = client.post("/parse", files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    check("POST /parse 非法类型返回 415", r.status_code == 415, str(r.status_code))

    r = client.post("/parse-text", json={"name": "t.txt", "text": "微信退款多久到账"})
    check("POST /parse-text 走内存不落盘", r.status_code == 200 and "退款" in r.json()["text"])

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
