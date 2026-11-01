"""Unit 22 · 知识库管理 —— 上传一个文件，是一件有边界、有归属、可撤销的事。

启动：
    uv sync
    uv run uvicorn main:app --reload --port 8022
    打开 http://127.0.0.1:8022/

自检（离线，临时 SQLite）：
    uv run python main.py

租户一律走请求头 `X-Tenant`（默认 demo），没有任何接口接受「租户由前端传参」。
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import io
import os
import re
import sqlite3
import sys
import uuid
import zipfile
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.environ.get("KB_DB_PATH") or os.path.join(DATA_DIR, "kb.db")

MAX_BYTES = 2 * 1024 * 1024  # 单文件 2 MB，超了直接拒，别等它进流水线
MAX_NAME = 200
CHUNK_CHARS = 180
TENANT_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
KB_ID_RE = re.compile(r"^kb_[0-9a-f]{12}$")
DOC_ID_RE = re.compile(r"^doc_[0-9a-f]{12}$")

# 扩展名 → 内容类型。白名单只管「名字」，真伪还要看魔数（见 sniff）。
KIND_BY_EXT: dict[str, str] = {
    ".pdf": "pdf",
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".txt": "text",
    ".md": "text",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".pptx": "pptx",
}

@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="Unit 22 · 知识库", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


# ---------------------------------------------------------------------------
# 错误：上传被拒要有稳定的 code，不能只给人看一句话
# ---------------------------------------------------------------------------
class UploadRejected(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class NotFound(Exception):
    """「不存在」与「不属于你」共用同一个异常 —— 越权必须长得像不存在。"""


# ---------------------------------------------------------------------------
# 数据库
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS kbs (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kbs_tenant ON kbs(tenant_id);

CREATE TABLE IF NOT EXISTS docs (
    id          TEXT PRIMARY KEY,
    kb_id       TEXT NOT NULL REFERENCES kbs(id) ON DELETE CASCADE,
    tenant_id   TEXT NOT NULL,
    name        TEXT NOT NULL,
    ext         TEXT NOT NULL,
    size        INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    chars       INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
-- 去重的边界是 (租户, 知识库)，不是全库
CREATE UNIQUE INDEX IF NOT EXISTS uq_docs_dedupe ON docs(tenant_id, kb_id, sha256);
CREATE INDEX IF NOT EXISTS idx_docs_kb ON docs(kb_id);

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      TEXT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    kb_id       TEXT NOT NULL,
    tenant_id   TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    doc_id      TEXT NOT NULL REFERENCES docs(id) ON DELETE CASCADE,
    tenant_id   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    note        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_doc ON jobs(doc_id);
"""


@contextlib.contextmanager
def _conn():
    """`with sqlite3.connect(...)` 只 commit 不 close，所以要自己写这三件事。

    还有一件最容易漏：SQLite 默认 foreign_keys = OFF，级联删除根本不生效。
    每个连接都必须显式打开它，否则 ON DELETE CASCADE 只是一行好看的 SQL。
    """
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _conn() as c:
        c.executescript(SCHEMA)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# 第一道门：扩展名白名单 + 魔数 + 大小
# ---------------------------------------------------------------------------
def _zip_kind(data: bytes) -> str:
    """OOXML 都是 zip，靠压缩包里的目录名区分，不靠扩展名。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
    except (zipfile.BadZipFile, ValueError):
        return "zip"
    if any(n.startswith("word/") for n in names):
        return "docx"
    if any(n.startswith("xl/") for n in names):
        return "xlsx"
    if any(n.startswith("ppt/") for n in names):
        return "pptx"
    return "zip"


def sniff(data: bytes) -> str | None:
    """看字节猜类型。猜不出返回 None（未知二进制）。"""
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"PK\x03\x04") or data.startswith(b"PK\x05\x06"):
        return _zip_kind(data)
    if b"\x00" in data:
        return None
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return "text"


def inspect_upload(filename: str, data: bytes) -> dict:
    """进流水线之前的三道关：白名单 → 大小 → 魔数。顺序从最便宜的开始。"""
    name = (filename or "").strip().replace("\\", "/").split("/")[-1]
    if not name or len(name) > MAX_NAME:
        raise UploadRejected("bad_filename", f"文件名不能为空且不超过 {MAX_NAME} 字符")
    ext = os.path.splitext(name)[1].lower()
    if ext not in KIND_BY_EXT:
        raise UploadRejected(
            "ext_not_allowed", f"不允许的扩展名 {ext or '(无)'}，白名单：{sorted(KIND_BY_EXT)}"
        )
    if not data:
        raise UploadRejected("empty_file", "空文件")
    if len(data) > MAX_BYTES:
        raise UploadRejected("too_large", f"{len(data)} 字节超过上限 {MAX_BYTES} 字节")
    expected = KIND_BY_EXT[ext]
    got = sniff(data)
    if got != expected:
        raise UploadRejected(
            "magic_mismatch",
            f"扩展名 {ext} 声明 {expected}，实际内容像 {got or '未知二进制'} —— 拒绝",
        )
    return {"name": name, "ext": ext, "kind": got, "size": len(data)}


# ---------------------------------------------------------------------------
# 切块
# ---------------------------------------------------------------------------
def chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """按段落装袋，单段超长就硬切。只对文本类做，二进制文件留空。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= size:
            buf = f"{buf}\n\n{p}" if buf else p
            continue
        if buf:
            out.append(buf)
            buf = ""
        while len(p) > size:
            out.append(p[:size])
            p = p[size:]
        buf = p
    if buf:
        out.append(buf)
    return out


def extract_text(kind: str, data: bytes) -> str:
    """文本类直接解码；二进制类的解析器是 Unit 17 的事，这里只记账。"""
    if kind != "text":
        return ""
    return data.decode("utf-8", errors="replace").strip()


# ---------------------------------------------------------------------------
# 领域操作：所有查询的第一个条件永远是 tenant_id
# ---------------------------------------------------------------------------
def create_kb(tenant: str, name: str) -> dict:
    name = (name or "").strip()
    if not 1 <= len(name) <= 60:
        raise UploadRejected("bad_kb_name", "知识库名称 1–60 字符")
    kb_id = _new_id("kb")
    with _conn() as c:
        c.execute(
            "INSERT INTO kbs(id, tenant_id, name, created_at) VALUES (?,?,?,?)",
            (kb_id, tenant, name, _now()),
        )
    return {"kb_id": kb_id, "name": name, "tenant": tenant}


def require_kb(c: sqlite3.Connection, tenant: str, kb_id: str) -> sqlite3.Row:
    row = c.execute(
        "SELECT * FROM kbs WHERE id = ? AND tenant_id = ?", (kb_id, tenant)
    ).fetchone()
    if row is None:
        raise NotFound("知识库不存在")
    return row


def list_kbs(tenant: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            """SELECT k.id, k.name, k.created_at,
                      (SELECT COUNT(*) FROM docs d WHERE d.kb_id = k.id) AS doc_count,
                      (SELECT COUNT(*) FROM chunks ch WHERE ch.kb_id = k.id) AS chunk_count
               FROM kbs k WHERE k.tenant_id = ? ORDER BY k.created_at DESC""",
            (tenant,),
        ).fetchall()
    return [dict(r) for r in rows]


def upload_doc(tenant: str, kb_id: str, filename: str, data: bytes) -> dict:
    meta = inspect_upload(filename, data)
    sha = hashlib.sha256(data).hexdigest()
    text = extract_text(meta["kind"], data)
    pieces = chunk_text(text)

    with _conn() as c:
        require_kb(c, tenant, kb_id)
        dup = c.execute(
            "SELECT id, chars FROM docs WHERE tenant_id=? AND kb_id=? AND sha256=?",
            (tenant, kb_id, sha),
        ).fetchone()
        if dup is not None:
            return {
                "doc_id": dup["id"],
                "kb_id": kb_id,
                "dedup": True,
                "sha256": sha[:12],
                "chunks": 0,
                "chars": dup["chars"],
                "name": meta["name"],
                "note": "同租户同库同内容，命中已有记录，不产生新行",
            }
        doc_id = _new_id("doc")
        c.execute(
            "INSERT INTO docs(id, kb_id, tenant_id, name, ext, size, sha256, chars, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (doc_id, kb_id, tenant, meta["name"], meta["ext"], meta["size"], sha, len(text), _now()),
        )
        for i, p in enumerate(pieces):
            c.execute(
                "INSERT INTO chunks(doc_id, kb_id, tenant_id, idx, text) VALUES (?,?,?,?,?)",
                (doc_id, kb_id, tenant, i, p),
            )
        c.execute(
            "INSERT INTO jobs(id, doc_id, tenant_id, kind, status, note, created_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                _new_id("job"),
                doc_id,
                tenant,
                "parse",
                "done",
                f"{meta['kind']} · {len(pieces)} 块"
                if pieces
                else f"{meta['kind']} · 二进制未接解析器（Unit 17）",
                _now(),
            ),
        )
    return {
        "doc_id": doc_id,
        "kb_id": kb_id,
        "dedup": False,
        "sha256": sha[:12],
        "chunks": len(pieces),
        "chars": len(text),
        "size": meta["size"],
        "name": meta["name"],
        "ext": meta["ext"],
    }


def list_docs(tenant: str, kb_id: str) -> list[dict]:
    with _conn() as c:
        require_kb(c, tenant, kb_id)
        rows = c.execute(
            """SELECT d.id, d.name, d.ext, d.size, d.sha256, d.chars, d.created_at,
                      (SELECT COUNT(*) FROM chunks ch WHERE ch.doc_id = d.id) AS chunk_count
               FROM docs d WHERE d.kb_id = ? AND d.tenant_id = ?
               ORDER BY d.created_at DESC""",
            (kb_id, tenant),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["sha256"] = d["sha256"][:12]
        out.append(d)
    return out


def list_chunks(tenant: str, kb_id: str, doc_id: str | None = None) -> list[dict]:
    with _conn() as c:
        require_kb(c, tenant, kb_id)
        sql = "SELECT id, doc_id, idx, text FROM chunks WHERE kb_id = ? AND tenant_id = ?"
        args: list = [kb_id, tenant]
        if doc_id:
            sql += " AND doc_id = ?"
            args.append(doc_id)
        rows = c.execute(sql + " ORDER BY doc_id, idx", tuple(args)).fetchall()
    return [dict(r) for r in rows]


def delete_doc(tenant: str, kb_id: str, doc_id: str) -> dict:
    with _conn() as c:
        require_kb(c, tenant, kb_id)
        doc = c.execute(
            "SELECT id FROM docs WHERE id = ? AND kb_id = ? AND tenant_id = ?",
            (doc_id, kb_id, tenant),
        ).fetchone()
        if doc is None:
            raise NotFound("文档不存在")
        # 外键已经会级联，这里仍显式删一遍：拿到 rowcount 才能如实回报删了多少
        chunks = c.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,)).rowcount
        jobs = c.execute("DELETE FROM jobs WHERE doc_id = ?", (doc_id,)).rowcount
        c.execute("DELETE FROM docs WHERE id = ?", (doc_id,))
    return {"deleted": True, "doc_id": doc_id, "chunks_removed": chunks, "jobs_removed": jobs}


def delete_kb(tenant: str, kb_id: str) -> dict:
    with _conn() as c:
        require_kb(c, tenant, kb_id)
        docs = c.execute("SELECT id FROM docs WHERE kb_id = ?", (kb_id,)).fetchall()
        chunks = c.execute("DELETE FROM chunks WHERE kb_id = ?", (kb_id,)).rowcount
        jobs = c.execute(
            "DELETE FROM jobs WHERE doc_id IN (SELECT id FROM docs WHERE kb_id = ?)", (kb_id,)
        ).rowcount
        c.execute("DELETE FROM docs WHERE kb_id = ?", (kb_id,))
        c.execute("DELETE FROM kbs WHERE id = ? AND tenant_id = ?", (kb_id, tenant))
    return {
        "deleted": True,
        "kb_id": kb_id,
        "docs_removed": len(docs),
        "chunks_removed": chunks,
        "jobs_removed": jobs,
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def _tenant(x_tenant: str) -> str:
    t = (x_tenant or "demo").strip()
    if not TENANT_RE.match(t):
        raise HTTPException(status_code=400, detail="X-Tenant 只允许字母数字 _-，最长 32")
    return t


def _kb(kb_id: str) -> str:
    if not KB_ID_RE.match(kb_id):
        raise HTTPException(status_code=400, detail="kb_id 格式非法（应形如 kb_0123456789ab）")
    return kb_id


def _doc(doc_id: str) -> str:
    if not DOC_ID_RE.match(doc_id):
        raise HTTPException(status_code=400, detail="doc_id 格式非法（应形如 doc_0123456789ab）")
    return doc_id


class KbIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class UploadIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_b64: str = Field(default="")


@app.get("/health", summary="库数、文档数、块数、白名单与上限")
def health(x_tenant: str = Header(default="demo")) -> dict:
    t = _tenant(x_tenant)
    with _conn() as c:
        kbs = c.execute("SELECT COUNT(*) FROM kbs WHERE tenant_id=?", (t,)).fetchone()[0]
        docs = c.execute("SELECT COUNT(*) FROM docs WHERE tenant_id=?", (t,)).fetchone()[0]
        chunks = c.execute("SELECT COUNT(*) FROM chunks WHERE tenant_id=?", (t,)).fetchone()[0]
        fk = c.execute("PRAGMA foreign_keys").fetchone()[0]
    return {
        "ok": True,
        "tenant": t,
        "db": os.path.relpath(DB_PATH, BASE_DIR),
        "kbs": kbs,
        "docs": docs,
        "chunks": chunks,
        "allowed_ext": sorted(KIND_BY_EXT),
        "max_bytes": MAX_BYTES,
        "chunk_chars": CHUNK_CHARS,
        "foreign_keys": bool(fk),
        "dedupe_scope": "tenant + kb + sha256",
    }


@app.post("/kb", summary="建知识库", status_code=201)
def post_kb(req: KbIn, x_tenant: str = Header(default="demo")) -> dict:
    t = _tenant(x_tenant)
    try:
        return create_kb(t, req.name)
    except UploadRejected as e:
        raise HTTPException(status_code=400, detail=e.detail) from e


@app.get("/kb", summary="我的知识库列表（只含自己的）")
def get_kbs(x_tenant: str = Header(default="demo")) -> dict:
    t = _tenant(x_tenant)
    items = list_kbs(t)
    return {"tenant": t, "count": len(items), "items": items}


@app.get("/kb/{kb_id}/docs", summary="某库的文档列表")
def get_docs(kb_id: str, x_tenant: str = Header(default="demo")) -> dict:
    t = _tenant(x_tenant)
    try:
        items = list_docs(t, _kb(kb_id))
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"kb_id": kb_id, "count": len(items), "items": items}


@app.post("/kb/{kb_id}/docs", summary="上传（base64），同库同内容去重", status_code=201)
def post_doc(req: UploadIn, kb_id: str, x_tenant: str = Header(default="demo")) -> dict:
    t = _tenant(x_tenant)
    kb_id = _kb(kb_id)
    raw = req.content_b64.strip()
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as e:
        raise HTTPException(status_code=400, detail="content_b64 不是合法 base64") from e
    try:
        return upload_doc(t, kb_id, req.filename, data)
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except UploadRejected as e:
        raise HTTPException(status_code=400, detail=f"{e.code}: {e.detail}") from e


@app.get("/kb/{kb_id}/chunks", summary="看块（可选 doc_id）")
def get_chunks(
    kb_id: str,
    doc_id: str | None = Query(default=None),
    x_tenant: str = Header(default="demo"),
) -> dict:
    t = _tenant(x_tenant)
    try:
        items = list_chunks(t, _kb(kb_id), _doc(doc_id) if doc_id else None)
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"kb_id": kb_id, "count": len(items), "items": items}


@app.delete("/kb/{kb_id}/docs/{doc_id}", summary="删文档：块与任务一起走")
def del_doc(kb_id: str, doc_id: str, x_tenant: str = Header(default="demo")) -> dict:
    t = _tenant(x_tenant)
    try:
        return delete_doc(t, _kb(kb_id), _doc(doc_id))
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.delete("/kb/{kb_id}", summary="删知识库：文档、块、任务一起走")
def del_kb(kb_id: str, x_tenant: str = Header(default="demo")) -> dict:
    t = _tenant(x_tenant)
    try:
        return delete_kb(t, _kb(kb_id))
    except NotFound as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/", include_in_schema=False)
def index():
    if os.path.exists(STATIC_INDEX):
        return FileResponse(STATIC_INDEX)
    return {"hint": "static/index.html 不存在，看 /docs"}


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PDF_MIN = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
LONG_MD = ("# 员工手册\n\n年假需提前 3 个工作日在系统提交申请，由直属主管审批。\n\n" * 12).encode()
TINY_TXT = "退款金额原路退回，通常 3–5 个工作日到账。".encode()


if __name__ == "__main__":
    import shutil
    import tempfile

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    def rejected(fn) -> tuple[bool, str]:
        try:
            fn()
        except UploadRejected as e:
            return True, e.code
        except Exception as e:  # noqa: BLE001
            return False, f"抛了 {type(e).__name__}: {e}"
        return False, "没抛异常"

    def q1(sql: str, args: tuple = ()) -> int:
        with _conn() as c:
            return c.execute(sql, args).fetchone()[0]

    def not_found(fn) -> str:
        try:
            fn()
        except NotFound:
            return "NotFound"
        except Exception as e:  # noqa: BLE001
            return f"抛了 {type(e).__name__}"
        return "没报错"

    # 自检库放临时目录，绝不碰 data/kb.db
    tmpdir = tempfile.mkdtemp(prefix="unit22-selftest-")
    DB_PATH = os.path.join(tmpdir, "kb.db")
    print("Unit 22 · 知识库自检（离线，临时 SQLite）\n")
    print(f"       库文件：{DB_PATH}")
    init_db()

    print("\n  —— 第一道门：扩展名白名单 ——")
    ok, code = rejected(lambda: inspect_upload("setup.exe", b"MZ\x90\x00"))
    check(".exe 被拒（白名单之外）", ok and code == "ext_not_allowed", code)
    ok, code = rejected(lambda: inspect_upload("evil.svg", b"<svg onload=alert(1)>"))
    check(".svg 被拒（能带脚本的不放进来）", ok and code == "ext_not_allowed", code)
    ok, code = rejected(lambda: inspect_upload("noext", b"hello"))
    check("无扩展名被拒", ok and code == "ext_not_allowed", code)
    ok, code = rejected(lambda: inspect_upload("a.txt", b""))
    check("空文件被拒", ok and code == "empty_file", code)

    print("\n  —— 魔数：改名改不掉内容 ——")
    ok, code = rejected(lambda: inspect_upload("photo.pdf", PNG_1PX))
    check("PNG 改名 .pdf 被拒（本单元最关键的一条）", ok and code == "magic_mismatch", code)
    ok, code = rejected(lambda: inspect_upload("doc.txt", PDF_MIN))
    check("PDF 改名 .txt 被拒", ok and code == "magic_mismatch", code)
    ok, code = rejected(lambda: inspect_upload("fake.docx", PDF_MIN))
    check("PDF 改名 .docx 被拒", ok and code == "magic_mismatch", code)
    ok, code = rejected(lambda: inspect_upload("bin.md", b"abc\x00def"))
    check("含 NUL 的「文本」被拒", ok and code == "magic_mismatch", code)
    check("真 PDF 通过", inspect_upload("a.pdf", PDF_MIN)["kind"] == "pdf")
    check("真 PNG 通过", inspect_upload("a.png", PNG_1PX)["kind"] == "png")
    check("真 md 通过", inspect_upload("a.md", LONG_MD)["kind"] == "text")
    check("JPEG 魔数认得", inspect_upload("a.jpg", b"\xff\xd8\xff\xe0rest")["kind"] == "jpg")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<w:document/>")
        z.writestr("[Content_Types].xml", "<Types/>")
    docx_bytes = buf.getvalue()
    check("真 docx 通过（zip 里看 word/）", inspect_upload("a.docx", docx_bytes)["kind"] == "docx")
    ok, code = rejected(lambda: inspect_upload("a.pdf", docx_bytes))
    check("docx 改名 .pdf 被拒", ok and code == "magic_mismatch", code)
    plain_zip = io.BytesIO()
    with zipfile.ZipFile(plain_zip, "w") as z:
        z.writestr("readme.txt", "hi")
    ok, code = rejected(lambda: inspect_upload("a.docx", plain_zip.getvalue()))
    check("普通 zip 改名 .docx 被拒", ok and code == "magic_mismatch", code)

    print("\n  —— 大小上限 ——")
    ok, code = rejected(lambda: inspect_upload("big.txt", b"a" * (MAX_BYTES + 1)))
    check(f"{MAX_BYTES} 字节 + 1 被拒", ok and code == "too_large", code)
    check("刚好等于上限则通过", inspect_upload("eq.txt", b"a" * MAX_BYTES)["size"] == MAX_BYTES)
    check("上限就是 2MB（README 里那个数的来源）", MAX_BYTES == 2 * 1024 * 1024, str(MAX_BYTES))

    print("\n  —— 建库与去重 ——")
    a = create_kb("acme", "制度库")["kb_id"]
    b = create_kb("acme", "第二个库")["kb_id"]
    check("建库返回 kb_ 前缀 id", a.startswith("kb_") and len(a) == 15, a)
    r1 = upload_doc("acme", a, "policy.md", LONG_MD)
    check("首次上传产生新文档", r1["dedup"] is False, str(r1))
    check("文本被切块（长文 > 1 块）", r1["chunks"] > 1, str(r1["chunks"]))
    r2 = upload_doc("acme", a, "policy-again.md", LONG_MD)
    check("同租户同库同内容 → 命中去重", r2["dedup"] is True, str(r2))
    check("去重返回同一个 doc_id（不新建行）", r2["doc_id"] == r1["doc_id"], r2["doc_id"])
    check("文档数仍是 1", q1("SELECT COUNT(*) FROM docs WHERE kb_id=?", (a,)) == 1)
    r3 = upload_doc("acme", b, "policy.md", LONG_MD)
    check("同租户不同库同内容 → 不去重", r3["dedup"] is False and r3["doc_id"] != r1["doc_id"])
    got = not_found(lambda: upload_doc("globex", a, "policy.md", LONG_MD))
    check("往别人的库里上传 → NotFound（连去重都轮不到）", got == "NotFound", got)
    g = create_kb("globex", "globex 自己的库")["kb_id"]
    r4 = upload_doc("globex", g, "policy.md", LONG_MD)
    check("不同租户同内容 → 不去重（去重不等于共享）", r4["dedup"] is False, str(r4))
    check("三份同内容各自独立 doc_id", len({r1["doc_id"], r3["doc_id"], r4["doc_id"]}) == 3)
    check("三份的 sha256 完全相同（内容确实一样）", q1("SELECT COUNT(DISTINCT sha256) FROM docs") == 1)

    print("\n  —— 租户隔离：越权要 404，不是 403 ——")
    check("globex 只看得见自己 1 个库", len(list_kbs("globex")) == 1, str(len(list_kbs("globex"))))
    check("acme 看得见自己 2 个库", len(list_kbs("acme")) == 2)
    leaked = not_found(lambda: list_docs("globex", a))
    check("globex 读 acme 的库 → NotFound", leaked == "NotFound", leaked)
    leaked = not_found(lambda: delete_doc("globex", a, r1["doc_id"]))
    check("globex 删 acme 的文档 → NotFound", leaked == "NotFound", leaked)
    check("越权删除后 acme 的文档还在", q1("SELECT COUNT(*) FROM docs WHERE id=?", (r1["doc_id"],)) == 1)
    check(
        "docs 行自带 tenant_id（查询能直接过滤）",
        q1("SELECT tenant_id FROM docs WHERE id=?", (r1["doc_id"],)) == "acme",
    )

    print("\n  —— 级联删除 ——")
    d_before = q1("SELECT COUNT(*) FROM chunks WHERE doc_id=?", (r1["doc_id"],))
    j_before = q1("SELECT COUNT(*) FROM jobs WHERE doc_id=?", (r1["doc_id"],))
    check("删除前有块", d_before > 0, str(d_before))
    check("删除前有任务记录", j_before == 1, str(j_before))
    out = delete_doc("acme", a, r1["doc_id"])
    check("删文档如实回报块数", out["chunks_removed"] == d_before, str(out))
    check("删文档后 chunk 一个不剩", q1("SELECT COUNT(*) FROM chunks WHERE doc_id=?", (r1["doc_id"],)) == 0)
    check("删文档后 job 也清了（任务残留）", q1("SELECT COUNT(*) FROM jobs WHERE doc_id=?", (r1["doc_id"],)) == 0)
    gone = not_found(lambda: delete_doc("acme", a, r1["doc_id"]))
    check("重复删除 → NotFound", gone == "NotFound", gone)
    check("外键 PRAGMA 真的是 ON", q1("PRAGMA foreign_keys") == 1)

    # 外键开着时，即使绕过业务函数直接删父行，子行也必须消失
    check("第二个库还有 1 份文档", q1("SELECT COUNT(*) FROM docs WHERE kb_id=?", (b,)) == 1)
    with _conn() as c:
        c.execute("DELETE FROM docs WHERE kb_id=?", (b,))
    check("直删父行也级联清 chunk（靠 FK 不靠代码）", q1("SELECT COUNT(*) FROM chunks WHERE kb_id=?", (b,)) == 0)

    k3 = create_kb("acme", "待整库删除")["kb_id"]
    for i in range(3):
        upload_doc("acme", k3, f"f{i}.md", f"第 {i} 份文档的内容。\n\n第二段。\n\npayload-{i}".encode())
    res = delete_kb("acme", k3)
    check("删库回报文档数", res["docs_removed"] == 3, str(res))
    check("删库后文档全没", q1("SELECT COUNT(*) FROM docs WHERE kb_id=?", (k3,)) == 0)
    check("删库后块全没", q1("SELECT COUNT(*) FROM chunks WHERE kb_id=?", (k3,)) == 0)
    check(
        "删库后没有留下孤儿任务",
        q1("SELECT COUNT(*) FROM jobs WHERE doc_id NOT IN (SELECT id FROM docs)") == 0,
    )
    check("删库后库本身没", q1("SELECT COUNT(*) FROM kbs WHERE id=?", (k3,)) == 0)

    print("\n  —— 切块 ——")
    doc_txt = "第一段。" * 50 + "\n\n" + "第二段。" * 50
    pieces = chunk_text(doc_txt)
    expected_chars = len(doc_txt) - 2  # 段落间那个分隔符不属于任何一块
    check("长文本切成多块", len(pieces) > 1, str(len(pieces)))
    check("每块不超 CHUNK_CHARS", all(len(p) <= CHUNK_CHARS for p in pieces), str(max(map(len, pieces))))
    check(
        "不丢字（拼回去长度等于原文去掉分隔符）",
        sum(len(p) for p in pieces) == expected_chars,
        f"{sum(len(p) for p in pieces)} vs {expected_chars}",
    )
    check("短文本只有 1 块", len(chunk_text("就一句话。")) == 1)
    check("空文本 0 块", chunk_text("   \n\n  ") == [])
    check("超大单段会被硬切", len(chunk_text("啊" * 500)) == 3, str(len(chunk_text("啊" * 500))))
    idxs = [r["idx"] for r in list_chunks("acme", b)]
    check("块序号从 0 连续", idxs == list(range(len(idxs))), str(idxs))
    big = upload_doc("acme", b, "binary.pdf", PDF_MIN)
    check("二进制文件不产块（解析器是 Unit 17）", big["chunks"] == 0, str(big["chunks"]))
    check("二进制文件仍留下任务记录", q1("SELECT COUNT(*) FROM jobs WHERE doc_id=?", (big["doc_id"],)) == 1)

    print("\n  —— HTTP 层 ——")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    h = {"X-Tenant": "http-co"}
    r = client.get("/health", headers=h)
    check("GET /health 自报租户与上限", r.status_code == 200 and r.json()["max_bytes"] == MAX_BYTES)
    check("/health 显示外键已开", r.json()["foreign_keys"] is True)

    r = client.post("/kb", json={"name": "接口库"}, headers=h)
    check("POST /kb 返回 201 与 kb_id", r.status_code == 201 and r.json()["kb_id"].startswith("kb_"), str(r.status_code))
    kid = r.json()["kb_id"]
    r = client.get("/kb", headers=h)
    check("GET /kb 只列自己的", r.status_code == 200 and r.json()["count"] == 1, str(r.json()))
    r = client.get("/kb", headers={"X-Tenant": "nobody"})
    check("别的租户看到 0 个库", r.json()["count"] == 0, str(r.json()))

    b64 = base64.b64encode(TINY_TXT).decode()
    r = client.post(f"/kb/{kid}/docs", json={"filename": "p.txt", "content_b64": b64}, headers=h)
    check("POST 上传返回 201", r.status_code == 201, str(r.status_code))
    did = r.json()["doc_id"]
    check("上传后能查到 1 份文档", len(client.get(f"/kb/{kid}/docs", headers=h).json()["items"]) == 1)
    r = client.post(f"/kb/{kid}/docs", json={"filename": "p2.txt", "content_b64": b64}, headers=h)
    check("重复上传 dedup=true", r.status_code == 201 and r.json()["dedup"] is True, str(r.json()))
    r = client.post(
        f"/kb/{kid}/docs",
        json={"filename": "p.png", "content_b64": b64},
        headers=h,
    )
    check("改名攻击经 HTTP 也是 400", r.status_code == 400 and "magic_mismatch" in r.json()["detail"], str(r.json()))
    r = client.post(
        f"/kb/{kid}/docs",
        json={"filename": "big.txt", "content_b64": base64.b64encode(b"a" * (MAX_BYTES + 1)).decode()},
        headers=h,
    )
    check("超大文件经 HTTP 被 400", r.status_code == 400 and "too_large" in r.json()["detail"])
    r = client.post(f"/kb/{kid}/docs", json={"filename": "x.txt", "content_b64": "不是base64"}, headers=h)
    check("非法 base64 被 400", r.status_code == 400, str(r.status_code))
    r = client.post(f"/kb/{kid}/docs", json={"filename": "x.txt", "content_b64": b64}, headers={"X-Tenant": "nobody"})
    check("别人往我的库上传 → 404（不泄露存在）", r.status_code == 404, str(r.status_code))
    r = client.get(f"/kb/{kid}/docs", headers={"X-Tenant": "nobody"})
    check("跨租户读文档列表 404", r.status_code == 404, str(r.status_code))
    r = client.delete(f"/kb/{kid}/docs/{did}", headers={"X-Tenant": "nobody"})
    check("跨租户删文档 404", r.status_code == 404, str(r.status_code))

    r = client.get(f"/kb/{kid}/chunks", headers=h)
    check("GET chunks 有块", r.status_code == 200 and r.json()["count"] >= 1, str(r.json()["count"]))
    r = client.delete(f"/kb/{kid}/docs/{did}", headers=h)
    check("删文档 200 且回报块数", r.status_code == 200 and r.json()["chunks_removed"] >= 1, str(r.json()))
    check("删完再读 chunks 为 0", client.get(f"/kb/{kid}/chunks", headers=h).json()["count"] == 0)
    r = client.delete(f"/kb/{kid}/docs/{did}", headers=h)
    check("再删一次 404", r.status_code == 404, str(r.status_code))

    r = client.get("/kb/kb_zzzzzzzzzzzz/docs", headers=h)
    check("非法 kb_id 格式 → 400", r.status_code == 400, str(r.status_code))
    r = client.get("/kb/kb_000000000000/docs", headers=h)
    check("合法但不存在的 kb_id → 404", r.status_code == 404, str(r.status_code))
    r = client.get("/kb", headers={"X-Tenant": "bad tenant!"})
    check("非法 X-Tenant → 400", r.status_code == 400, str(r.status_code))
    r = client.post(
        f"/kb/{kid}/docs",
        json={"filename": "again.txt", "content_b64": base64.b64encode(b"second doc here").decode()},
        headers=h,
    )
    check("删库前再传一份（内容不同，不命中 dedup）", r.status_code == 201 and r.json()["dedup"] is False, str(r.json()))
    r = client.delete(f"/kb/{kid}", headers=h)
    check(
        "DELETE /kb 回报删掉的文档数",
        r.status_code == 200 and r.json()["docs_removed"] == 1,
        str(r.json()),
    )
    check("DELETE /kb 连块一起回收", r.json()["chunks_removed"] >= 1, str(r.json()))
    check("删库后 GET /kb 为空", client.get("/kb", headers=h).json()["count"] == 0)
    r = client.delete(f"/kb/{kid}", headers=h)
    check("删两次库 404", r.status_code == 404, str(r.status_code))

    print("\n  —— 收尾：不留孤儿行 ——")
    total_docs = q1("SELECT COUNT(*) FROM docs")
    orphan_chunks = q1("SELECT COUNT(*) FROM chunks WHERE doc_id NOT IN (SELECT id FROM docs)")
    orphan_jobs = q1("SELECT COUNT(*) FROM jobs WHERE doc_id NOT IN (SELECT id FROM docs)")
    print(f"       残留：docs={total_docs} 无主 chunks={orphan_chunks} 无主 jobs={orphan_jobs}")
    check("全程没有产生无主 chunk", orphan_chunks == 0, str(orphan_chunks))
    check("全程没有产生无主 job", orphan_jobs == 0, str(orphan_jobs))
    shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
