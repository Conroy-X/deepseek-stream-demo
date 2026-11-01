"""Unit 23 · Parse→Chunk 流水线：任务表 + 状态机 + 带租约的 worker。

为什么不能用 FastAPI 的 BackgroundTasks：
    BackgroundTasks 的任务活在**进程内存**里。进程被 kill、被重启、被 OOM，
    任务就一起没了 —— 用户看到的是"上传成功"，状态永远停在 processing。
    而且它没有去重：同一个文件点两次，两个任务并行跑，chunk 写两遍。

所以本单元把"要做的事"落到一张表里，谁来做、做到哪一步、做失败了没，
全在数据库里，进程只是执行者：

    pending ──claim──> running ──成功──> done
       ▲                  │
       └──失败且还有配额──┘
                          └──失败且 attempts 用尽──> failed

三个约束撑住整个可靠性（也是本单元真正的知识点）：

1. **租约（lease）**
   claim 时写 `leased_until = now + lease`。worker 崩了不会有人帮你擦屁股，
   所以"我还在干"必须靠**续期**证明；过期了别人就能接管，不需要协调、不需要 redis。
   代价：任务可能被执行两次 —— 于是必须有第 2 条。

2. **幂等（idempotent）**
   重试/接管会重复处理同一个文档，所以写 chunk 前先按 document_id 删干净再插，
   并且 `UNIQUE(document_id, idx)` 兜底。重复执行的结果必须和只执行一次一样。

3. **重试预算（attempts）**
   attempts 在 **claim 时** +1，不是失败时。否则 worker 一崩溃就没人记账，
   坏任务能被无限次接管，把整个队列堵死。

解析失败也不能让任务卡在 running：process() 的失败分支一定把任务写回
pending / failed，同时把 error 落到表和文档上，让人查得出来。
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Iterator

from chunker import ChunkError, chunk_document
from parser import ParseError, parse_bytes

PIPELINE_VERSION = "parse-chunk-v1"

STATUSES = ("pending", "running", "done", "failed")

MAX_ATTEMPTS = 3
LEASE_SECONDS = 30.0

# 切块参数固定下来，别让它随调用方漂移 —— 否则重试会切出不同的块，幂等就名存实亡
CHUNK_STRATEGY = "recursive"
CHUNK_SIZE = 200
CHUNK_OVERLAP = 30

_ERROR_MAX = 200  # 错误信息太长会把 /tasks 撑爆，也会漏出内部路径


class PipelineError(ValueError):
    """业务错误（文档不存在、状态不允许）。路由层统一转 400/409。"""


# ---------------------------------------------------------------------------
# 连接与事务
# ---------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    raw         BLOB NOT NULL,              -- 原始字节，重试时重新解析，不依赖内存
    fail_inject INTEGER NOT NULL DEFAULT 0, -- 故障注入开关，自检和演示用
    status      TEXT NOT NULL DEFAULT 'pending',
    kind        TEXT NOT NULL DEFAULT '',
    chars       INTEGER NOT NULL DEFAULT 0,
    pages       INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    error       TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  TEXT NOT NULL REFERENCES documents(id),
    status       TEXT NOT NULL DEFAULT 'pending',
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    worker       TEXT,
    leased_until REAL,
    error        TEXT,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL
);

-- 同一个文档同时只能有一条活任务。重复上传靠它兜底，
-- 而不是靠"先 SELECT 再 INSERT"—— 那中间有窗口，两个请求会同时通过。
CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_active
    ON tasks(document_id) WHERE status IN ('pending', 'running');

CREATE TABLE IF NOT EXISTS chunks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id TEXT NOT NULL REFERENCES documents(id),
    idx         INTEGER NOT NULL,
    text        TEXT NOT NULL,
    chars       INTEGER NOT NULL,
    page        INTEGER NOT NULL,
    start       INTEGER NOT NULL DEFAULT 0,
    end         INTEGER NOT NULL DEFAULT 0,
    UNIQUE(document_id, idx)
);

CREATE INDEX IF NOT EXISTS ix_chunks_doc ON chunks(document_id, idx);
"""


def db_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "pipeline.db")


@contextmanager
def connect(path: str | None = None) -> Iterator[sqlite3.Connection]:
    """每次操作一个连接：sqlite 连接不能跨线程共享，而 worker 天生多线程。

    isolation_level=None 关掉 sqlite3 的隐式事务 —— 我们要显式 BEGIN IMMEDIATE，
    否则两个 worker 会同时 SELECT 到同一条 pending 任务（默认 DEFERRED 事务在
    第一次写之前只拿读锁，读到的不一定是自己即将锁住的那行）。
    """
    p = path or db_path()
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # worker 写的时候，页面还能读
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        conn.executescript(SCHEMA)
        yield conn
    finally:
        conn.close()


@contextmanager
def tx(conn: sqlite3.Connection, immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """显式事务。异常回滚，正常提交。

    IMMEDIATE = 进事务就拿写锁。claim 必须用它，否则"选中 → 更新"之间
    别的 worker 也能选中同一行，两个人一起跑同一份文档。
    """
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()


def _brief(e: BaseException) -> str:
    """错误信息压成一行：换行会毁掉表格，堆栈会漏出本机路径。"""
    msg = " ".join(str(e).split())
    if not msg:
        msg = type(e).__name__
    prefix = type(e).__name__
    text = msg if prefix in msg else f"{prefix}: {msg}"
    return text[:_ERROR_MAX]


# ---------------------------------------------------------------------------
# 文档
# ---------------------------------------------------------------------------
def create_document(
    conn: sqlite3.Connection,
    name: str,
    raw: bytes,
    fail_inject: bool = False,
    now: float | None = None,
) -> dict:
    if not name.strip():
        raise PipelineError("文件名不能为空")
    if not raw:
        raise PipelineError("文件是空的（0 字节），没什么可解析的")
    now = now or time.time()
    doc_id = "doc_" + uuid.uuid4().hex[:10]
    with tx(conn):
        conn.execute(
            "INSERT INTO documents(id, name, raw, fail_inject, status, created_at, updated_at)"
            " VALUES(?, ?, ?, ?, 'pending', ?, ?)",
            (doc_id, name.strip(), raw, int(bool(fail_inject)), now, now),
        )
    return get_document(conn, doc_id)


def get_document(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, name, fail_inject, status, kind, chars, pages, chunk_count,"
        " error, created_at, updated_at FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()
    return dict(row) if row else None


def list_documents(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    rows = conn.execute(
        """
        SELECT d.id, d.name, d.status, d.kind, d.chars, d.pages, d.chunk_count, d.error,
               d.created_at, d.updated_at,
               t.id AS task_id, t.status AS task_status, t.attempts, t.max_attempts,
               t.leased_until, t.worker, t.error AS task_error
        FROM documents d
        LEFT JOIN tasks t ON t.id = (SELECT MAX(id) FROM tasks WHERE document_id = d.id)
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def document_chunks(conn: sqlite3.Connection, doc_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT idx, text, chars, page, start, end FROM chunks"
        " WHERE document_id = ? ORDER BY idx",
        (doc_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def count_chunks(conn: sqlite3.Connection, doc_id: str) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM chunks WHERE document_id = ?", (doc_id,)
    ).fetchone()["n"]


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
def get_task(conn: sqlite3.Connection, task_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return dict(row) if row else None


def list_tasks(conn: sqlite3.Connection, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        """
        SELECT t.*, d.name AS document_name
        FROM tasks t LEFT JOIN documents d ON d.id = t.document_id
        ORDER BY t.id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def enqueue(
    conn: sqlite3.Connection,
    document_id: str,
    max_attempts: int = MAX_ATTEMPTS,
    now: float | None = None,
) -> dict:
    """把文档排进队列。同一个文档重复调用只会有一条活任务（返回 created=False）。"""
    now = now or time.time()
    row = conn.execute(
        "SELECT * FROM tasks WHERE document_id = ? AND status IN ('pending','running')",
        (document_id,),
    ).fetchone()
    if row:
        return {**dict(row), "created": False}
    try:
        with tx(conn):
            cur = conn.execute(
                "INSERT INTO tasks(document_id, status, attempts, max_attempts, created_at, updated_at)"
                " VALUES(?, 'pending', 0, ?, ?, ?)",
                (document_id, max_attempts, now, now),
            )
            task_id = int(cur.lastrowid or 0)
    except sqlite3.IntegrityError:
        # 唯一索引挡下了并发重复入队，把已有的那条取回来即可
        row = conn.execute(
            "SELECT * FROM tasks WHERE document_id = ? AND status IN ('pending','running')",
            (document_id,),
        ).fetchone()
        return {**dict(row), "created": False}
    return {**get_task(conn, task_id), "created": True}  # type: ignore[operator]


def claim(
    conn: sqlite3.Connection,
    worker: str = "worker-1",
    lease_seconds: float = LEASE_SECONDS,
    now: float | None = None,
) -> dict | None:
    """领一条能干的活：pending，或者 running 但租约已过期（原 worker 大概率已死）。

    注意 attempts 在这里 +1。崩在 claim 之后、完成之前的那次尝试也要算数，
    否则"崩溃→接管→崩溃→接管"能无限循环。
    """
    now = now or time.time()
    deadline = now + lease_seconds
    with tx(conn):
        row = conn.execute(
            """
            SELECT * FROM tasks
            WHERE (status = 'pending'
                   OR (status = 'running' AND leased_until IS NOT NULL AND leased_until < ?))
              AND attempts < max_attempts
            ORDER BY id
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        if row is None:
            return None
        cur = conn.execute(
            """
            UPDATE tasks
               SET status = 'running', worker = ?, leased_until = ?,
                   attempts = attempts + 1, error = NULL, updated_at = ?
             WHERE id = ?
               AND (status = 'pending'
                    OR (status = 'running' AND leased_until IS NOT NULL AND leased_until < ?))
            """,
            (worker, deadline, now, row["id"], now),
        )
        if cur.rowcount == 0:  # 被别的 worker 抢先了
            return None
        conn.execute(
            "UPDATE documents SET status = 'running', updated_at = ? WHERE id = ?",
            (now, row["document_id"]),
        )
        task = get_task(conn, int(row["id"]))
    return task


def _replace_chunks(conn: sqlite3.Connection, document_id: str, chunks) -> None:
    """先删后插 —— 这是幂等的关键。

    为什么不用 `INSERT ... ON CONFLICT DO UPDATE`：重切片可能让块**变少**
    （比如换小了 chunk_size），ON CONFLICT 只覆盖同 idx 的行，多余的旧块会留在库里。
    删干净再插，块数就永远等于本次真实切出来的数量。
    调用方必须已经在事务里，否则删除和插入之间出现空窗，页面会看到 0 块。
    """
    conn.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
    conn.executemany(
        "INSERT INTO chunks(document_id, idx, text, chars, page, start, end)"
        " VALUES(?, ?, ?, ?, ?, ?, ?)",
        [
            (document_id, c.index, c.text, c.chars, c.page, c.start, c.end)
            for c in chunks
        ],
    )


def process(
    conn: sqlite3.Connection,
    task: dict,
    strategy: str = CHUNK_STRATEGY,
    size: int = CHUNK_SIZE,
    overlap: int = CHUNK_OVERLAP,
    now: float | None = None,
) -> dict:
    """跑一条已领取的任务：Parse → Chunk → 落库。

    chunks 写入、文档状态、任务状态在**同一个事务**里提交。
    否则会出现"块已经能搜到、任务还显示 running"的中间态 —— 用户重试一次就重复了。
    """
    now = now or time.time()
    t0 = time.perf_counter()
    doc = conn.execute("SELECT * FROM documents WHERE id = ?", (task["document_id"],)).fetchone()
    doc_id = task["document_id"]
    try:
        if doc is None:
            raise PipelineError(f"文档 {doc_id} 不存在（任务指向了已删除的行）")
        if doc["fail_inject"]:
            raise ParseError("注入的故障：文件头不是 %PDF-，拒绝解析")
        parsed = parse_bytes(doc["name"], bytes(doc["raw"]))
        chunks = chunk_document(parsed.pages, strategy, size, overlap)
        if not chunks:
            raise ChunkError("切块结果为空：文档里没有可切分的句子")
        ms = (time.perf_counter() - t0) * 1000
        with tx(conn):
            _replace_chunks(conn, doc_id, chunks)
            conn.execute(
                "UPDATE documents SET status='done', kind=?, chars=?, pages=?,"
                " chunk_count=?, error=NULL, updated_at=? WHERE id=?",
                (parsed.kind, parsed.chars, parsed.page_count, len(chunks), now, doc_id),
            )
            conn.execute(
                "UPDATE tasks SET status='done', worker=NULL, leased_until=NULL,"
                " error=NULL, updated_at=? WHERE id=?",
                (now, task["id"]),
            )
        return {
            "task_id": task["id"],
            "document_id": doc_id,
            "status": "done",
            "attempts": task["attempts"],
            "final": True,
            "chunks": len(chunks),
            "chars": parsed.chars,
            "pages": parsed.page_count,
            "kind": parsed.kind,
            "avg_chars": round(sum(c.chars for c in chunks) / len(chunks), 1),
            "ms": round(ms, 2),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001 - 任何异常都必须落到任务状态上
        msg = _brief(e)
        ms = (time.perf_counter() - t0) * 1000
        final = int(task["attempts"]) >= int(task["max_attempts"])
        with tx(conn):
            conn.execute(
                "UPDATE tasks SET status=?, error=?, worker=NULL, leased_until=NULL,"
                " updated_at=? WHERE id=?",
                ("failed" if final else "pending", msg, now, task["id"]),
            )
            conn.execute(
                "UPDATE documents SET status=?, error=?, updated_at=? WHERE id=?",
                ("failed" if final else "pending", msg, now, doc_id),
            )
            if final:
                # 终态就别留着上一次的半截结果，否则"文档 failed 但 chunks 还在"
                conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
        return {
            "task_id": task["id"],
            "document_id": doc_id,
            "status": "failed" if final else "pending",
            "attempts": task["attempts"],
            "final": final,
            "chunks": 0,
            "ms": round(ms, 2),
            "error": msg,
        }


def tick(
    conn: sqlite3.Connection,
    worker: str = "worker-1",
    lease_seconds: float = LEASE_SECONDS,
    now: float | None = None,
    **chunk_opts,
) -> dict:
    """一次调度循环：领一条 → 跑完 → 回报。没有活就 claimed=False 立刻返回。"""
    t0 = time.perf_counter()
    task = claim(conn, worker, lease_seconds, now)
    if task is None:
        return {
            "claimed": False,
            "reason": "没有可领取的任务（全是 done / failed，或租约还没到期）",
            "ms": round((time.perf_counter() - t0) * 1000, 2),
        }
    res = process(conn, task, now=now, **chunk_opts)
    return {"claimed": True, **res, "worker": worker, "ms": round((time.perf_counter() - t0) * 1000, 2)}


def drain(
    conn: sqlite3.Connection,
    worker: str = "worker-1",
    limit: int = 20,
    lease_seconds: float = LEASE_SECONDS,
    now: float | None = None,
    **chunk_opts,
) -> list[dict]:
    out: list[dict] = []
    for _ in range(limit):
        res = tick(conn, worker, lease_seconds, now, **chunk_opts)
        if not res["claimed"]:
            break
        out.append(res)
    return out


def retry_document(
    conn: sqlite3.Connection,
    document_id: str,
    now: float | None = None,
) -> dict:
    """把一条失败的任务重置回 pending，配额重新给满。

    重试是"重置同一条任务"而不是"再插一条"：唯一索引只允许一条活任务，
    硬插会撞 IntegrityError；而历史任务堆成山也没人看得懂。
    """
    now = now or time.time()
    doc = get_document(conn, document_id)
    if doc is None:
        raise PipelineError(f"没有文档 {document_id}")
    if doc["status"] == "done":
        raise PipelineError("文档已经处理完，重试没有意义（要换结果请重新上传）")

    task = conn.execute(
        "SELECT * FROM tasks WHERE document_id = ? ORDER BY id DESC LIMIT 1",
        (document_id,),
    ).fetchone()
    if task is not None and task["status"] == "running":
        alive = task["leased_until"] is not None and float(task["leased_until"]) >= now
        if alive:
            raise PipelineError("任务正在执行（租约未过期），别手动插一脚；等它超时或失败")

    with tx(conn):
        if task is None:
            conn.execute(
                "INSERT INTO tasks(document_id, status, attempts, max_attempts, created_at, updated_at)"
                " VALUES(?, 'pending', 0, ?, ?, ?)",
                (document_id, MAX_ATTEMPTS, now, now),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status='pending', attempts=0, max_attempts=?, error=NULL,"
                " worker=NULL, leased_until=NULL, updated_at=? WHERE id=?",
                (MAX_ATTEMPTS, now, task["id"]),
            )
        conn.execute(
            "UPDATE documents SET status='pending', error=NULL, updated_at=? WHERE id=?",
            (now, document_id),
        )
        latest = conn.execute(
            "SELECT * FROM tasks WHERE document_id = ? ORDER BY id DESC LIMIT 1",
            (document_id,),
        ).fetchone()
    return {"document_id": document_id, "task": dict(latest), "reset": True}  # type: ignore[arg-type]


def stats(conn: sqlite3.Connection) -> dict:
    by_status = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM tasks GROUP BY status"
    )}
    docs = {r["status"]: r["n"] for r in conn.execute(
        "SELECT status, COUNT(*) AS n FROM documents GROUP BY status"
    )}
    return {
        "documents": sum(docs.values()),
        "chunks": conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"],
        "by_document_status": {s: docs.get(s, 0) for s in STATUSES},
        "by_task_status": {s: by_status.get(s, 0) for s in STATUSES},
    }
