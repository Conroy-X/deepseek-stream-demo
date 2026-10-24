"""Unit 11 · PostgreSQL：把对话真正存进数据库。

和前几个单元的区别：
    Unit 9 用 SQLite、Unit 10 用内存 —— 进程一重启就没了。
    这个单元换成 PostgreSQL，用三张表 + 外键 + 索引，把"持久化"做成真的。

启动：
    docker compose up -d                      # 起库（第一次会初始化数据库）
    uv run uvicorn main:app --reload --port 8000
    打开 http://127.0.0.1:8000/ 看数据面板

自检（不需要 LLM，不需要 API Key，只需要库在跑）：
    uv run python main.py

为什么把这个单元排在 JWT 之前：
    先有可靠的数据层，再加用户系统。反过来做，你会一边调 FK 一边调 token，
    报错根本分不清是哪层的。
"""

from __future__ import annotations

import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

import db as store
from db import Conversation, Message, User, init_db

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动时建表。连不上库不阻塞启动 —— 让 /health 把原因说清楚，
    比 uvicorn 直接崩掉更好排查。

    用 lifespan 而不是已弃用的 @app.on_event("startup")。
    """
    try:
        init_db()
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 建表失败（库没起？）：{type(exc).__name__}: {exc}", file=sys.stderr)
    yield


app = FastAPI(title="Unit 11 · PostgreSQL", version="0.1.0", lifespan=lifespan)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")


# ---------------------------------------------------------------------------
# 请求 / 响应模型
# ---------------------------------------------------------------------------
class UserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(default="", max_length=100)


class ConversationCreate(BaseModel):
    user_id: int
    title: str = Field(default="新对话", max_length=200)


class MessageCreate(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1)
    tokens: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
@app.get("/health", summary="健康检查：顺带告诉你能不能连库")
def health() -> dict[str, object]:
    return store.check_connection()


@app.post("/users", summary="建用户")
def create_user(payload: UserCreate, session: Session = Depends(store.get_session)) -> dict:
    user = User(email=str(payload.email), display_name=payload.display_name)
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="email 已存在") from None
    session.refresh(user)
    return {"id": user.id, "email": user.email}


@app.get("/users", summary="列用户")
def list_users(session: Session = Depends(store.get_session)) -> list[dict]:
    users = session.exec(select(User).order_by(User.id)).all()
    return [{"id": u.id, "email": u.email, "display_name": u.display_name} for u in users]


@app.post("/conversations", summary="建会话")
def create_conversation(
    payload: ConversationCreate, session: Session = Depends(store.get_session)
) -> dict:
    if session.get(User, payload.user_id) is None:
        raise HTTPException(status_code=404, detail="user 不存在")
    conv = Conversation(user_id=payload.user_id, title=payload.title)
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return {"id": conv.id, "title": conv.title, "user_id": conv.user_id}


@app.get("/conversations", summary="列某用户的会话（按最近活跃排序）")
def list_conversations(
    user_id: int = Query(..., description="用户 id"),
    session: Session = Depends(store.get_session),
) -> list[dict]:
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.updated_at.desc())
    )
    return [
        {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()}
        for c in session.exec(stmt).all()
    ]


@app.delete("/conversations/{conversation_id}", summary="删会话（消息级联删除）")
def delete_conversation(
    conversation_id: int, session: Session = Depends(store.get_session)
) -> dict:
    conv = session.get(Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation 不存在")
    # 只删会话本身，messages 由数据库 ON DELETE CASCADE 带走
    session.delete(conv)
    session.commit()
    return {"deleted": conversation_id}


@app.post("/conversations/{conversation_id}/messages", summary="追加一条消息")
def add_message(
    conversation_id: int,
    payload: MessageCreate,
    session: Session = Depends(store.get_session),
) -> dict:
    if session.get(Conversation, conversation_id) is None:
        raise HTTPException(status_code=404, detail="conversation 不存在")
    msg = Message(
        conversation_id=conversation_id,
        role=payload.role,
        content=payload.content,
        tokens=payload.tokens,
    )
    session.add(msg)
    store.touch_conversation(session, conversation_id)
    session.commit()
    session.refresh(msg)
    return {"id": msg.id, "created_at": msg.created_at.isoformat()}


@app.get("/conversations/{conversation_id}/messages", summary="拉一个会话的全部消息")
def list_messages(
    conversation_id: int,
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(store.get_session),
) -> list[dict]:
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
        .limit(limit)
    )
    return [
        {"id": m.id, "role": m.role, "content": m.content, "tokens": m.tokens}
        for m in session.exec(stmt).all()
    ]


@app.get("/debug/explain", summary="看查询计划，确认索引生效")
def debug_explain(conversation_id: int = 1) -> dict[str, object]:
    plan = store.explain_message_query(conversation_id=conversation_id)
    return {
        "plan": plan,
        "uses_index": "ix_messages_conversation_created" in plan,
        "seq_scan": "Seq Scan" in plan,
    }


@app.get("/", include_in_schema=False)
def index():
    if os.path.exists(STATIC_INDEX):
        return FileResponse(STATIC_INDEX)
    return {"hint": "static/index.html 不存在，直接看 /docs"}


# ---------------------------------------------------------------------------
# 自检：只需要 PostgreSQL 在跑，不花一分钱
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi.testclient import TestClient

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 11 · PostgreSQL 自检（不调模型）\n")

    conn = store.check_connection()
    if not conn.get("ok"):
        print(f"  [SKIP] 连不上数据库：{conn.get('error')}")
        print("\n  先起库：docker compose up -d && sleep 3 && uv run python main.py")
        sys.exit(1)

    print(f"  数据库：{conn['version']}")
    check("能连上 PostgreSQL", True)

    init_db()
    tables = store.check_connection().get("tables", [])
    check("三张表都已创建", all(t in tables for t in ("users", "conversations", "messages")), str(tables))

    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    email = f"selftest-{tag}@example.com"

    # --- HTTP 层：建用户 ---
    r = client.post("/users", json={"email": email, "display_name": "自检"})
    check("POST /users 返回 200", r.status_code == 200, str(r.status_code))
    user_id = r.json()["id"] if r.status_code == 200 else None

    r2 = client.post("/users", json={"email": email, "display_name": "重复"})
    check("重复 email 被拒（409）", r2.status_code == 409, str(r2.status_code))

    # --- 建会话 ---
    r = client.post("/conversations", json={"user_id": user_id, "title": "自检会话"})
    check("POST /conversations 返回 200", r.status_code == 200, str(r.status_code))
    conv_id = r.json()["id"] if r.status_code == 200 else None

    r = client.post("/conversations", json={"user_id": 99999999, "title": "x"})
    check("不存在的 user 被拒（404）", r.status_code == 404, str(r.status_code))

    # --- 写消息 ---
    for i, role in enumerate(("user", "assistant", "user")):
        client.post(
            f"/conversations/{conv_id}/messages",
            json={"role": role, "content": f"第 {i} 条", "tokens": 10 * (i + 1)},
        )
    r = client.get(f"/conversations/{conv_id}/messages")
    msgs = r.json()
    check("写进 3 条消息", len(msgs) == 3, str(len(msgs)))
    check("按时间正序返回", [m["content"] for m in msgs] == ["第 0 条", "第 1 条", "第 2 条"])
    check("tokens 落库正确", sum(m["tokens"] for m in msgs) == 60)

    # --- 索引与查询计划 ---
    check("(conversation_id, created_at) 索引生效", client.get(
        f"/debug/explain?conversation_id={conv_id}"
    ).json()["uses_index"])

    # --- 持久化：换一个全新 Session 还能读到（模拟"换个进程"）---
    engine2 = store.make_engine()
    with Session(engine2) as s2:
        found = s2.exec(select(Message).where(Message.conversation_id == conv_id)).all()
    engine2.dispose()
    check("新连接仍能读到数据（真持久化）", len(found) == 3, str(len(found)))

    # --- 级联删除 ---
    r = client.delete(f"/conversations/{conv_id}")
    check("删会话返回 200", r.status_code == 200, str(r.status_code))
    with Session(store.engine) as s3:
        left = s3.exec(select(Message).where(Message.conversation_id == conv_id)).all()
    check("会话删除后消息被级联清空", len(left) == 0, f"残留 {len(left)} 条")

    # --- 删用户 → 会话级联 ---
    r = client.post("/conversations", json={"user_id": user_id, "title": "待级联"})
    conv2 = r.json()["id"]
    with Session(store.engine) as s4:
        u = s4.get(User, user_id)
        s4.delete(u)
        s4.commit()
    with Session(store.engine) as s5:
        gone = s5.exec(select(Conversation).where(Conversation.id == conv2)).all()
    check("删用户后会话被级联清空", len(gone) == 0, f"残留 {len(gone)} 条")

    # --- 外键约束 ---
    with Session(store.engine) as s6:
        orphan_rejected = False
        try:
            s6.add(Message(conversation_id=98765432, role="user", content="孤儿消息"))
            s6.commit()
        except IntegrityError:
            s6.rollback()
            orphan_rejected = True
    check("外键拦住孤儿消息", orphan_rejected)

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
