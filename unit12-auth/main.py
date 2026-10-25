"""Unit 12 · 用户系统：注册 / 登录 / JWT 鉴权 / 越权防护。

在 Unit 11 的三张表上加 password_hash，把"谁都能读写所有人的数据"
变成"每个人只能碰自己的数据"。

启动：
    uv run uvicorn main:app --reload --port 8012
    打开 http://127.0.0.1:8012/           注册登录面板
    打开 http://127.0.0.1:8012/docs       点右上角 Authorize 亲手验一次

登录接口是 OAuth2 表单（字段 username/password，Content-Type 为 form），
不是 JSON —— 用 JSON 调会报 422 "username/password Field required"。

自检（不调模型、不花钱，需要库在跑）：
    uv run python main.py

认证链路一句话：
    注册（存 Argon2 哈希）→ 登录（验哈希，签 JWT）→ 每次请求带 Authorization: Bearer <token>
    → 后端验签解出 sub（user id）→ 查库拿到 user → 进入业务逻辑
"""

from __future__ import annotations

import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.responses import FileResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

import db as store
import security
from db import Conversation, Message, User, ensure_schema

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        applied = ensure_schema()
        if applied:
            print(f"[migrate] 已补列：{', '.join(applied)}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 建表/迁移失败（库没起？）：{type(exc).__name__}: {exc}", file=sys.stderr)
    yield


app = FastAPI(title="Unit 12 · 用户系统", version="0.1.0", lifespan=lifespan)

# auto_error=False：token 缺失时不要由 FastAPI 直接抛 403，
# 交给我们自己返回带 WWW-Authenticate 头的 401（规范要求）
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login", auto_error=False)


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class RegisterRequest(BaseModel):
    email: EmailStr
    # 密码强度下限。真实项目还要查弱密码表、查是否泄露过
    password: str = Field(min_length=8, max_length=128, description="至少 8 位")
    display_name: str = Field(default="", max_length=100)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ConversationCreate(BaseModel):
    title: str = Field(default="新对话", max_length=200)


class MessageCreate(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1)
    tokens: int = Field(default=0, ge=0)


def _unauthorized(detail: str) -> HTTPException:
    """401 统一带上 WWW-Authenticate 头，否则不符合 OAuth2 规范，
    前端某些客户端库会认不出来。
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


# ---------------------------------------------------------------------------
# 依赖：从 token 到当前用户
# ---------------------------------------------------------------------------
def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    session: Session = Depends(store.get_session),
) -> User:
    """鉴权核心。任何需要登录的接口挂上这个依赖即可。

    故意把三种情况区分开（都对外的 401，但 detail 不同，方便前端处理）：
        · 没带 token        → "未提供 token"
        · token 过期        → "token 已过期"（前端该去刷新）
        · token 无效/被改   → "token 无效"（直接踢回登录）
    """
    if not token:
        raise _unauthorized("未提供 token")
    try:
        payload = security.decode_token(token)
    except security.TokenError as exc:
        raise _unauthorized(str(exc)) from exc

    try:
        user_id = int(payload.get("sub", ""))
    except (TypeError, ValueError):
        raise _unauthorized("token 无效") from None

    user = session.get(User, user_id)
    if user is None:
        raise _unauthorized("用户不存在")
    if not user.is_active:
        raise _unauthorized("用户已被禁用")
    return user


# ---------------------------------------------------------------------------
# 注册 / 登录 / 我是谁
# ---------------------------------------------------------------------------
@app.get("/health", summary="探活")
def health() -> dict[str, object]:
    return store.check_connection()


@app.post("/register", summary="注册", status_code=201)
def register(payload: RegisterRequest, session: Session = Depends(store.get_session)) -> dict:
    user = User(
        email=str(payload.email).lower(),
        display_name=payload.display_name,
        password_hash=security.hash_password(payload.password),
    )
    session.add(user)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        # 注意：这里能返回 409 说明"该邮箱已存在"，等于泄露了注册状态。
        # 高安全场景会统一返回 200 并异步发邮件，这里为了教学清晰保留 409。
        raise HTTPException(status_code=409, detail="email 已注册") from None
    session.refresh(user)
    return {"id": user.id, "email": user.email}


@app.post("/login", summary="登录，返回 JWT", response_model=TokenResponse)
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(store.get_session),
) -> TokenResponse:
    """注意：OAuth2 规范里这个字段叫 username，本接口把 email 塞进 username。"""
    email = (form.username or "").strip().lower()
    user = session.exec(select(User).where(User.email == email)).first()

    # 用户不存在和密码错误必须返回**同样的**信息，否则攻击者可以靠
    # 响应差异枚举出哪些邮箱注册过（账号枚举漏洞）。
    if user is None or not security.verify_password(form.password, user.password_hash):
        raise _unauthorized("邮箱或密码错误")
    if not user.is_active:
        raise _unauthorized("用户已被禁用")

    token = security.create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        expires_in=security.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@app.get("/me", summary="我是谁（需要登录）")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


@app.get("/debug/token", summary="教学用：不验签直接看 payload，证明 JWT 不是加密")
def debug_token(token: str = Query(...)) -> dict:
    try:
        return {"payload": security.describe_token(token), "note": "以上内容任何人 base64 都能解出来"}
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# 业务接口：全部要求登录，且强制归属校验
# ---------------------------------------------------------------------------
@app.post("/conversations", summary="建会话（自动归属当前用户）")
def create_conversation(
    payload: ConversationCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(store.get_session),
) -> dict:
    conv = Conversation(user_id=user.id, title=payload.title)
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return {"id": conv.id, "title": conv.title}


@app.get("/conversations", summary="我的会话列表")
def list_conversations(
    user: User = Depends(get_current_user),
    session: Session = Depends(store.get_session),
) -> list[dict]:
    # 关键：user_id 来自 token，**不接受前端传参**。
    # 一旦允许 "?user_id=" 由前端指定，越权就只是改个数字的事。
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    )
    return [
        {"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()}
        for c in session.exec(stmt).all()
    ]


def _owned_conversation(session: Session, conversation_id: int, user: User) -> Conversation:
    """取会话并校验归属。

    别人的会话返回 **404 而不是 403** —— 403 等于告诉对方"这个 id 存在，
    只是你没权限"，攻击者可以据此遍历出全站会话 id。404 什么都不泄露。
    """
    conv = session.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation 不存在")
    return conv


@app.delete("/conversations/{conversation_id}", summary="删自己的会话")
def delete_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(store.get_session),
) -> dict:
    conv = _owned_conversation(session, conversation_id, user)
    session.delete(conv)
    session.commit()
    return {"deleted": conversation_id}


@app.get("/conversations/{conversation_id}/messages", summary="拉消息（只能看自己的）")
def list_messages(
    conversation_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(store.get_session),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    _owned_conversation(session, conversation_id, user)
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


@app.post("/conversations/{conversation_id}/messages", summary="追加消息")
def add_message(
    conversation_id: int,
    payload: MessageCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(store.get_session),
) -> dict:
    _owned_conversation(session, conversation_id, user)
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


@app.get("/", include_in_schema=False)
def index():
    if os.path.exists(STATIC_INDEX):
        return FileResponse(STATIC_INDEX)
    return {"hint": "static/index.html 不存在，直接看 /docs"}


# ---------------------------------------------------------------------------
# 自检
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

    print("Unit 12 · 用户系统自检（不调模型）\n")

    conn = store.check_connection()
    if not conn.get("ok"):
        print(f"  [SKIP] 连不上数据库：{conn.get('error')}")
        sys.exit(1)

    ensure_schema()
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    email_a = f"a-{tag}@example.com"
    email_b = f"b-{tag}@example.com"
    password = "CorrectHorse2026"

    # --- 注册 ---
    r = client.post("/register", json={"email": email_a, "password": password, "display_name": "A"})
    check("注册返回 201", r.status_code == 201, str(r.status_code))

    r = client.post("/register", json={"email": email_a, "password": password})
    check("重复邮箱被拒（409）", r.status_code == 409, str(r.status_code))

    r = client.post("/register", json={"email": f"weak-{tag}@example.com", "password": "123"})
    check("弱密码被拒（422）", r.status_code == 422, str(r.status_code))

    # --- 密码存储 ---
    with Session(store.engine) as s:
        ua = s.exec(select(User).where(User.email == email_a)).first()
        stored = ua.password_hash if ua else ""
        uid_a = ua.id if ua else None
    check("库里存的是 Argon2 哈希不是明文", stored.startswith("$argon2") and password not in stored, stored[:20])
    check("同一密码两次哈希不同（随机盐）", security.hash_password(password) != stored)

    # --- 登录 ---
    r = client.post("/login", data={"username": email_a, "password": password})
    check("登录成功", r.status_code == 200, str(r.status_code))
    token_a = r.json().get("access_token", "") if r.status_code == 200 else ""

    r = client.post("/login", data={"username": email_a, "password": "WrongPassword123"})
    check("密码错误返回 401", r.status_code == 401, str(r.status_code))

    r = client.post("/login", data={"username": f"nobody-{tag}@example.com", "password": password})
    check("不存在的用户返回 401（不泄露账号是否存在）", r.status_code == 401, str(r.status_code))

    # --- token 结构与校验 ---
    payload = security.describe_token(token_a)
    check("payload 含 sub / exp / iat / jti", all(k in payload for k in ("sub", "exp", "iat", "jti")))
    check("sub 是字符串形式的 user id", payload.get("sub") == str(uid_a))
    check("JWT 不加密（不看密码字段）", "password" not in payload)

    r = client.get("/me")
    check("不带 token 访问 /me → 401", r.status_code == 401, str(r.status_code))
    check("401 带 WWW-Authenticate 头", r.headers.get("www-authenticate") == "Bearer")

    r = client.get("/me", headers={"Authorization": f"Bearer {token_a}"})
    check("带 token 访问 /me → 200", r.status_code == 200, str(r.status_code))
    check("/me 返回正确用户", r.json().get("email") == email_a)

    expired = security.create_access_token(uid_a, expires_minutes=-1)
    r = client.get("/me", headers={"Authorization": f"Bearer {expired}"})
    check("过期 token → 401", r.status_code == 401, str(r.status_code))
    check("过期原因可区分", "过期" in r.json().get("detail", ""), r.json().get("detail", ""))

    tampered = token_a[:-4] + ("aaaa" if not token_a.endswith("aaaa") else "bbbb")
    r = client.get("/me", headers={"Authorization": f"Bearer {tampered}"})
    check("篡改签名 → 401", r.status_code == 401, str(r.status_code))

    # --- 越权防护 ---
    ha = {"Authorization": f"Bearer {token_a}"}
    r = client.post("/conversations", json={"title": "A 的私密会话"}, headers=ha)
    check("A 建会话成功", r.status_code == 200, str(r.status_code))
    conv_a = r.json().get("id") if r.status_code == 200 else None

    client.post("/register", json={"email": email_b, "password": password, "display_name": "B"})
    token_b = client.post("/login", data={"username": email_b, "password": password}).json()["access_token"]
    hb = {"Authorization": f"Bearer {token_b}"}

    r = client.get(f"/conversations/{conv_a}/messages", headers=hb)
    check("B 读 A 的会话 → 404（不是 403，避免泄露存在性）", r.status_code == 404, str(r.status_code))

    r = client.post(f"/conversations/{conv_a}/messages", json={"role": "user", "content": "入侵"}, headers=hb)
    check("B 往 A 的会话写消息 → 404", r.status_code == 404, str(r.status_code))

    r = client.delete(f"/conversations/{conv_a}", headers=hb)
    check("B 删 A 的会话 → 404", r.status_code == 404, str(r.status_code))

    r = client.get("/conversations", headers=hb)
    check("B 的会话列表看不到 A 的会话", all(c["id"] != conv_a for c in r.json()))

    r = client.get("/conversations", headers=ha)
    check("A 能看到自己的会话", any(c["id"] == conv_a for c in r.json()))

    # --- 禁用用户 ---
    with Session(store.engine) as s:
        ub = s.exec(select(User).where(User.email == email_b)).first()
        ub.is_active = False
        s.add(ub)
        s.commit()
    r = client.get("/me", headers=hb)
    check("被禁用的用户 token 立即失效", r.status_code == 401, str(r.status_code))

    # --- 清理 ---
    with Session(store.engine) as s:
        for em in (email_a, email_b, f"weak-{tag}@example.com"):
            u = s.exec(select(User).where(User.email == em)).first()
            if u:
                s.delete(u)
        s.commit()

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
