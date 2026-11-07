"""Unit 61 · Docker：FastAPI + PostgreSQL 用 compose 一键起。

计划要求：把 FastAPI 后端 + PG 用 compose 一键起。

设计要点：
  1. **可降级**：PG 连不上时自动退回内存存储，服务照常可用（可用性优先）
  2. **健康检查**：/health 明确报告 db 后端（postgres / memory）
  3. **依赖顺序**：compose 里 app 等 PG 的 healthcheck 通过才启动
  4. **自检离线可跑**：静态校验 compose 结构 + 验证降级逻辑，不需要真起容器

启动：docker compose up --build -d
自检：uv run python main.py
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

HERE = Path(__file__).resolve().parent


def build_dsn() -> str:
    """拼出 DSN：优先 DATABASE_URL；否则用拆分的 PG* 变量拼（compose 就是这么传的）。"""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    host = os.getenv("PGHOST", "").strip()
    user = os.getenv("PGUSER", "").strip()
    auth = os.getenv("PGAUTH", "").strip()
    db = os.getenv("PGDB", "").strip()
    port = os.getenv("PGPORT", "5432").strip()
    if not (host and user and auth and db):
        return ""
    return f"postgresql://{user}:{auth}@{host}:{port}/{db}"


DATABASE_URL = build_dsn()


# ---------------------------------------------------------------------------
# 存储层：PG 优先，连不上降级内存
# ---------------------------------------------------------------------------
@dataclass
class MemoryStore:
    backend: str = "memory"
    _data: dict = field(default_factory=dict)

    def put(self, key: str, value: str) -> None:
        self._data[key] = value

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def count(self) -> int:
        return len(self._data)


@dataclass
class PgStore:
    backend: str = "postgres"
    dsn: str = ""

    def _conn(self):
        import psycopg  # 延迟导入：离线环境不装也能用 MemoryStore

        return psycopg.connect(self.dsn, connect_timeout=2)

    def put(self, key: str, value: str) -> None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO notes(key, value) VALUES(%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, value),
            )
            conn.commit()

    def get(self, key: str) -> str | None:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM notes WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else None

    def count(self) -> int:
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM notes")
            return int(cur.fetchone()[0])


def make_store(dsn: str | None = None) -> MemoryStore | PgStore:
    """优先 PG；连不上（或没配 DSN）退回内存。"""
    dsn = (dsn if dsn is not None else DATABASE_URL).strip()
    if not dsn:
        return MemoryStore()
    store = PgStore(dsn=dsn)
    try:
        # 探测：建表 + 一次查询
        with store._conn() as conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS notes(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.commit()
        store.count()
        return store
    except Exception:  # noqa: BLE001
        return MemoryStore()


store = make_store()

app = FastAPI(title="Unit 61 · Docker + PostgreSQL", version="0.1.0")


@app.get("/health", summary="健康检查：报告当前 db 后端")
async def health() -> dict:
    return {"ok": True, "unit": 61, "db_backend": store.backend}


@app.put("/notes/{key}", summary="写入一条笔记")
async def put_note(key: str, value: str) -> dict:
    store.put(key, value)
    return {"ok": True, "key": key, "db_backend": store.backend}


@app.get("/notes/{key}", summary="读取一条笔记")
async def get_note(key: str) -> dict:
    v = store.get(key)
    if v is None:
        raise HTTPException(status_code=404, detail=f"笔记 {key} 不存在")
    return {"key": key, "value": v, "db_backend": store.backend}


@app.get("/notes", summary="笔记总数")
async def count_notes() -> dict:
    return {"count": store.count(), "db_backend": store.backend}


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "/health 看 db 后端；PUT/GET /notes/{key} 读写"})


# ---------------------------------------------------------------------------
# 独立自检：compose 结构校验 + 降级逻辑验证
# ---------------------------------------------------------------------------
def run_selfcheck() -> int:
    import yaml

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 61 · Docker + PostgreSQL 自检\n")

    # ---- 降级逻辑 ----
    ms = make_store("")  # 空 DSN → 内存
    check("空 DSN 退回内存", ms.backend == "memory")
    ms.put("a", "1")
    check("内存后端可写读", ms.get("a") == "1")
    check("内存后端计数", ms.count() == 1)
    check("内存后端读不存在返回 None", ms.get("nope") is None)

    bad = make_store("postgresql://nobody:nobody@127.0.0.1:1/none")
    check("PG 连不上也退回内存（服务不挂）", bad.backend == "memory", bad.backend)

    # ---- DSN 拼接 ----
    import os as _os

    saved = {k: _os.environ.get(k) for k in ("DATABASE_URL", "PGHOST", "PGUSER", "PGAUTH", "PGDB", "PGPORT")}
    for k in saved:
        _os.environ.pop(k, None)
    check("无配置时 DSN 为空", build_dsn() == "")

    _os.environ.update({"PGHOST": "db", "PGUSER": "app", "PGAUTH": "pw", "PGDB": "notes", "PGPORT": "5432"})
    dsn = build_dsn()
    check("拆分的 PG* 能拼出 DSN", dsn.startswith("postgresql://app:") and "@db:5432/notes" in dsn, dsn)

    _os.environ["DATABASE_URL"] = "postgresql://x:y@h:1/d"
    check("DATABASE_URL 优先级最高", build_dsn() == "postgresql://x:y@h:1/d")

    # 还原环境
    for k, v in saved.items():
        if v is None:
            _os.environ.pop(k, None)
        else:
            _os.environ[k] = v

    # ---- 应用层 ----
    from fastapi.testclient import TestClient

    c = TestClient(app)
    r = c.get("/health")
    check("health 200", r.status_code == 200)
    check("health 报告 db_backend", r.json()["db_backend"] in ("postgres", "memory"))
    r = c.put("/notes/x", params={"value": "hello"})
    check("写入笔记 200", r.status_code == 200)
    r = c.get("/notes/x")
    check("读到刚写的笔记", r.json()["value"] == "hello")
    r = c.get("/notes/not-exist")
    check("读不存在 → 404", r.status_code == 404)

    # ---- compose 结构 ----
    dc = HERE / "docker-compose.yml"
    check("docker-compose.yml 存在", dc.exists())
    if dc.exists():
        compose = yaml.safe_load(dc.read_text(encoding="utf-8"))
        svc = compose.get("services", {})
        check("有 app 服务", "app" in svc, str(list(svc)))
        check("有 db 服务", "db" in svc, str(list(svc)))
        if "db" in svc:
            db = svc["db"]
            check("db 用 postgres 镜像", "postgres" in str(db.get("image", "")))
            check("db 有 healthcheck", "healthcheck" in db)
            check("db 有数据卷", bool(db.get("volumes")))
            check("db 有环境变量（POSTGRES_PASSWORD）",
                  "POSTGRES_PASSWORD" in str(db.get("environment")))
        if "app" in svc:
            app_svc = svc["app"]
            check("app 有 build", "build" in app_svc)
            check("app 依赖 db", "db" in str(app_svc.get("depends_on")))
            check("app 等 db healthy",
                  "service_healthy" in str(app_svc.get("depends_on")),
                  str(app_svc.get("depends_on")))
            check("app 传 PG* 连接变量", all(
                k in str(app_svc.get("environment")) for k in ("PGUSER", "PGAUTH", "PGHOST", "PGDB")
            ), str(app_svc.get("environment")))
            check("app 不把凭据写进 URL", "postgresql://" not in str(app_svc.get("environment")))
            check("app 映射端口", bool(app_svc.get("ports")))

    # ---- Dockerfile ----
    df = HERE / "Dockerfile"
    check("Dockerfile 存在", df.exists())
    if df.exists():
        txt = df.read_text(encoding="utf-8")
        lines = [ln for ln in txt.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        check("首条指令是 FROM", lines[0].upper().startswith("FROM"))
        check("非 root 运行", any(ln.upper().startswith("USER ") and "root" not in ln.lower() for ln in lines))
        check("pip 走 USTC 镜像（否则国内构建卡死）", "mirrors.ustc.edu.cn" in txt)
        check("有 HEALTHCHECK", "HEALTHCHECK" in txt)

    # ---- .dockerignore ----
    di = HERE / ".dockerignore"
    check(".dockerignore 存在", di.exists())
    if di.exists():
        d = di.read_text(encoding="utf-8")
        for must in (".env", ".venv", "__pycache__"):
            check(f"排除 {must}", must in d)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())