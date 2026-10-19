"""Unit 9 · 多轮对话：Conversation / Message 数据模型 + SQLite 持久化。

本单元在 Unit 7/8 基础上加一层「会话管理」：

    Conversation 1 ──── N Message

关键设计（面试常问）：
    1. 为什么不能只存最后一条消息？
       —— LLM 本身无状态，每次请求都要把**完整历史**重发一遍。
          只存最后一条，模型就不知道上下文了。

    2. 消息必须**按时间有序**，且区分 role（user / assistant / system）
       —— 顺序错了模型会理解反。

    3. 用 SQLite 而不是内存 → 服务重启、刷新页面都不丢。

数据库 schema：
    conversations(id, title, created_at, updated_at)
    messages(id, conversation_id, role, content, created_at)

启动：
    .venv/bin/uvicorn main:app --reload --port 8000

自检（不需要 API Key、不花钱）：
    .venv/bin/python main.py
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

load_dotenv(Path(__file__).with_name(".env"))

API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
DB_PATH = Path(os.getenv("DB_PATH", Path(__file__).with_name("chat.db")))

ALLOW_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOW_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000,http://127.0.0.1:5500,"
        "http://localhost:5500,http://127.0.0.1:8080,http://localhost:8080,null",
    ).split(",")
    if o.strip()
]

SYSTEM_PROMPT = os.getenv(
    "SYSTEM_PROMPT",
    "你是一个乐于助人的 AI 助手。回答准确、简洁，涉及代码时给出可运行的示例。",
)

def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id          TEXT PRIMARY KEY,
                title       TEXT NOT NULL,
                created_at  REAL NOT NULL,
                updated_at  REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role            TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
                content         TEXT NOT NULL,
                created_at      REAL NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_msg_conv
                ON messages(conversation_id, created_at);
            """
        )



@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN001, ARG001
    """服务启动时建表。

    这一步不能省：uvicorn 启动后如果没建表，第一个请求就会报
    `no such table: conversations`。自检里手动调了 init_db()，
    所以自检通过 ≠ 真实启动没问题 —— 必须单独验证。
    """
    init_db()
    yield


app = FastAPI(title="Unit 9 · 多轮对话", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# 数据库层
# ---------------------------------------------------------------------------
@contextmanager
def db():
    """每次操作开一个连接，用完自动提交/关闭。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now() -> float:
    return time.time()


def create_conversation(title: str = "新对话") -> str:
    cid = uuid.uuid4().hex[:12]
    ts = now()
    with db() as conn:
        conn.execute(
            "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (cid, title, ts, ts),
        )
    return cid


def list_conversations() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS msg_count
            FROM conversations c
            ORDER BY c.updated_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(cid: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM conversations WHERE id = ?", (cid,)).fetchone()
    return dict(row) if row else None


def get_messages(cid: str) -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY id",
            (cid,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_message(cid: str, role: str, content: str) -> None:
    ts = now()
    with db() as conn:
        conn.execute(
            "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?,?,?,?)",
            (cid, role, content, ts),
        )
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (ts, cid))


def rename_conversation(cid: str, title: str) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, now(), cid),
        )


def delete_conversation(cid: str) -> None:
    with db() as conn:
        conn.execute("DELETE FROM conversations WHERE id = ?", (cid,))


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1, le=4096)


class NewConversation(BaseModel):
    title: str = Field(default="新对话", max_length=100)


class RenameRequest(BaseModel):
    title: str = Field(min_length=1, max_length=100)


class SendMessage(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1, le=4096)


_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if not API_KEY or API_KEY.startswith("sk-你的"):
        raise HTTPException(status_code=500, detail="缺少 DEEPSEEK_API_KEY，请在 .env 配置")
    if _client is None:
        _client = AsyncOpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=60.0)
    return _client


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# 会话管理接口
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    return {
        "ok": True,
        "model": MODEL,
        "key_configured": bool(API_KEY) and not API_KEY.startswith("sk-你的"),
        "db": str(DB_PATH),
    }


@app.get("/conversations", summary="会话列表（按最近更新排序）")
async def api_list_conversations() -> dict:
    return {"conversations": list_conversations()}


@app.post("/conversations", summary="新建会话")
async def api_create_conversation(req: NewConversation) -> dict:
    cid = create_conversation(req.title)
    return {"id": cid, "title": req.title}


@app.get("/conversations/{cid}", summary="会话详情（含全部消息）")
async def api_get_conversation(cid: str) -> dict:
    conv = get_conversation(cid)
    if not conv:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"conversation": conv, "messages": get_messages(cid)}


@app.patch("/conversations/{cid}", summary="重命名会话")
async def api_rename(cid: str, req: RenameRequest) -> dict:
    if not get_conversation(cid):
        raise HTTPException(status_code=404, detail="会话不存在")
    rename_conversation(cid, req.title)
    return {"ok": True, "title": req.title}


@app.delete("/conversations/{cid}", summary="删除会话（级联删除消息）")
async def api_delete(cid: str) -> dict:
    if not get_conversation(cid):
        raise HTTPException(status_code=404, detail="会话不存在")
    delete_conversation(cid)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 对话（带持久化）
# ---------------------------------------------------------------------------
@app.post("/conversations/{cid}/messages", summary="在多轮会话里发一条消息（SSE 流式）")
async def api_send(cid: str, req: SendMessage, request: Request) -> StreamingResponse:
    if not get_conversation(cid):
        raise HTTPException(status_code=404, detail="会话不存在")

    async def gen() -> AsyncIterator[str]:
        started = time.perf_counter()

        # 1. 先把用户消息落库（即使模型调用失败也不丢）
        add_message(cid, "user", req.content)

        # 2. 取完整历史作为上下文
        history = get_messages(cid)
        payload = [{"role": "system", "content": SYSTEM_PROMPT}] + [
            {"role": m["role"], "content": m["content"]} for m in history
        ]
        yield sse({"type": "history", "count": len(history)})

        collected: list[str] = []
        try:
            client = get_client()
            stream = await client.chat.completions.create(
                model=MODEL,
                messages=payload,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            first = True
            async for chunk in stream:
                if await request.is_disconnected():
                    break
                if chunk.usage:
                    yield sse(
                        {
                            "type": "usage",
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                        }
                    )
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if not delta:
                    continue
                if first:
                    first = False
                    yield sse({"type": "first_token", "ttft": round(time.perf_counter() - started, 3)})
                collected.append(delta)
                yield sse({"type": "delta", "content": delta})

            answer = "".join(collected)
            if answer:
                add_message(cid, "assistant", answer)
                # 首次对话自动用第一句话当标题
                conv = get_conversation(cid)
                if conv and conv["title"] == "新对话":
                    title = req.content.strip().replace("\n", " ")[:24]
                    rename_conversation(cid, title)

            yield sse(
                {
                    "type": "done",
                    "elapsed": round(time.perf_counter() - started, 3),
                    "saved": bool(answer),
                }
            )
        except HTTPException as exc:
            yield sse({"type": "error", "message": exc.detail})
        except Exception as exc:  # noqa: BLE001
            yield sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/chat/stream", summary="无状态的流式问答（兼容 Unit 7 前端）")
async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
    async def gen() -> AsyncIterator[str]:
        started = time.perf_counter()
        try:
            client = get_client()
            stream = await client.chat.completions.create(
                model=MODEL,
                messages=[m.model_dump() for m in req.messages],
                temperature=req.temperature,
                max_tokens=req.max_tokens,
                stream=True,
                stream_options={"include_usage": True},
            )
            first = True
            async for chunk in stream:
                if await request.is_disconnected():
                    break
                if chunk.usage:
                    yield sse(
                        {
                            "type": "usage",
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                        }
                    )
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta.content
                if not delta:
                    continue
                if first:
                    first = False
                    yield sse({"type": "first_token", "ttft": round(time.perf_counter() - started, 3)})
                yield sse({"type": "delta", "content": delta})
            yield sse({"type": "done", "elapsed": round(time.perf_counter() - started, 3)})
        except HTTPException as exc:
            yield sse({"type": "error", "message": exc.detail})
        except Exception as exc:  # noqa: BLE001
            yield sse({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# 自检：不调模型、不需要 Key
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import tempfile

    from fastapi.testclient import TestClient

    # 用临时库，避免污染真实数据
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    DB_PATH = tmp
    init_db()

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 9 · 多轮对话 自检（不调模型）\n")
    client = TestClient(app)

    # --- 建库 ---
    check("数据库文件已创建", tmp.exists())

    # --- 会话 CRUD ---
    r = client.post("/conversations", json={"title": "测试会话"})
    check("新建会话 200", r.status_code == 200)
    cid = r.json()["id"]
    check("返回 id", bool(cid))

    r = client.get("/conversations")
    check("列表含该会话", any(c["id"] == cid for c in r.json()["conversations"]))

    r = client.get(f"/conversations/{cid}")
    check("详情 200", r.status_code == 200)
    check("初始消息为空", r.json()["messages"] == [])

    r = client.patch(f"/conversations/{cid}", json={"title": "改过的标题"})
    check("重命名 200", r.status_code == 200)
    check(
        "标题已更新",
        client.get(f"/conversations/{cid}").json()["conversation"]["title"] == "改过的标题",
    )

    # --- 消息持久化 ---
    add_message(cid, "user", "第一句")
    add_message(cid, "assistant", "第一答")
    add_message(cid, "user", "第二句")
    msgs = get_messages(cid)
    check("消息数量正确", len(msgs) == 3, str(len(msgs)))
    check("消息顺序正确", [m["role"] for m in msgs] == ["user", "assistant", "user"])
    check("内容正确", [m["content"] for m in msgs] == ["第一句", "第一答", "第二句"])

    # --- 级联删除 ---
    r = client.delete(f"/conversations/{cid}")
    check("删除 200", r.status_code == 200)
    check("会话已不存在", client.get(f"/conversations/{cid}").status_code == 404)
    with db() as conn:
        left = conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE conversation_id = ?", (cid,)
        ).fetchone()["c"]
    check("消息被级联删除", left == 0, f"剩 {left} 条")

    # --- role 约束 ---
    cid2 = create_conversation("约束测试")
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?,?,?,?)",
                (cid2, "robot", "x", now()),
            )
        check("非法 role 被数据库拒绝", False)
    except sqlite3.IntegrityError:
        check("非法 role 被数据库拒绝", True)

    # --- 404 处理 ---
    check("不存在的会话 404", client.get("/conversations/nope").status_code == 404)
    check(
        "给不存在的会话发消息 404",
        client.post("/conversations/nope/messages", json={"content": "hi"}).status_code == 404,
    )

    # --- 空消息被拒 ---
    cid3 = create_conversation("边界")
    r = client.post(f"/conversations/{cid3}/messages", json={"content": ""})
    check("空消息被拒（422）", r.status_code == 422, str(r.status_code))

    # --- 缺 Key 时用户消息仍落库 ---
    import sys as _sys

    self_mod = _sys.modules["__main__"]
    saved = self_mod.API_KEY
    try:
        self_mod.API_KEY = ""
        with client.stream(
            "POST", f"/conversations/{cid3}/messages", json={"content": "会失败的请求"}
        ) as resp:
            body = "".join(resp.iter_text())
        check("缺 Key 错误走 SSE", '"error"' in body)
        msgs3 = get_messages(cid3)
        check("用户消息已落库（不丢）", any(m["content"] == "会失败的请求" for m in msgs3))
    finally:
        self_mod.API_KEY = saved

    # --- 清理临时库 ---
    try:
        tmp.unlink()
    except OSError:
        pass

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
