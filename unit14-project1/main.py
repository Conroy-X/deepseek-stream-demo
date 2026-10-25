"""Unit 14 · 项目 1 收口：AI Chat 完整版（project1-ai-chat）。

把前面 13 个单元的东西拼成一个真的能演示的产品：

    Unit 11 的三张表      → 会话与消息真正持久化
    Unit 12 的 JWT        → 每个用户只能看自己的会话
    Unit 13 的 llm_client → 超时/重试/限流/成本
    Unit 6 的 SSE         → 逐字输出

本单元唯一的新增难点是「**流式 + 落库的时序**」：
    什么时候写用户消息？什么时候写助手消息？
    用户中途关页面，已经生成的那半句要不要存？
    模型报错时，历史里该留什么？

启动：
    uv run uvicorn main:app --reload --port 8014
    打开 http://127.0.0.1:8014/

自检（用假 LLM，不花钱）：
    uv run python main.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

import db as store
import llm_client as llm
import security
from db import Conversation, Message, User, ensure_schema

# 静态页路径按「本文件所在目录」算：uvicorn 的启动目录不一定是这个目录，用相对路径会 404
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_INDEX = os.path.join(BASE_DIR, "static", "index.html")

# 上下文预算：只把最近 N 条消息塞给模型。
# 不做裁剪的话，聊到第 50 轮必然撞上上下文长度上限，而且每轮都在为历史付钱。
MAX_CONTEXT_MESSAGES = int(os.getenv("MAX_CONTEXT_MESSAGES", "20"))
DEFAULT_TITLE = "新对话"

SYSTEM_PROMPT = (
    "你是 rik 的 AI 学习搭子，说话直接、不灌水。"
    "回答尽量短，能用一句说清就不用三句。"
)


# lifespan：服务启动时跑一次（建表 + 配置自检），关闭时执行 yield 之后那半段（这里没有）。
# 它是 @app.on_event("startup") 的替代品，后者已弃用。
@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 建表失败只 warn 不崩：数据库没起时，服务也得能启动，让 /health 把原因报出来
    try:
        applied = ensure_schema()
        if applied:
            print(f"[migrate] 已补列：{', '.join(applied)}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] 建表失败（库没起？）：{type(exc).__name__}: {exc}", file=sys.stderr)
    if llm._is_placeholder(llm.get_client().config.api_key):
        print("[warn] 未配置 DEEPSEEK_API_KEY，对话接口会返回 503", file=sys.stderr)
    yield


app = FastAPI(title="project1-ai-chat", version="1.0.0", lifespan=lifespan)

# ---------------------------------------------------------------------------
# CORS：页面和接口不同源时（例如用 http://127.0.0.1:3000 打开静态页、接口在 8012），
# 浏览器默认会拦掉响应。没有这段，跨域 POST 会表现为
# 「服务端其实返回 200，但浏览器里是红色失败请求」，极难排查。
# ---------------------------------------------------------------------------
ALLOW_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOW_ORIGINS",
        "http://127.0.0.1:3000,http://localhost:3000,"
        "http://127.0.0.1:5500,http://localhost:5500,"
        "http://127.0.0.1:8080,http://localhost:8080,"
        "http://127.0.0.1:8011,http://localhost:8011,"
        "http://127.0.0.1:8012,http://localhost:8012,"
        "http://127.0.0.1:8013,http://localhost:8013,"
        "http://127.0.0.1:8014,http://localhost:8014,"
        "http://127.0.0.1:8015,http://localhost:8015",
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# OAuth2PasswordBearer 只干一件事：从请求头 Authorization: Bearer <token> 里把 token 抠出来。
# auto_error=False 表示「没带 token 不立刻报错」，把 None 交给 get_current_user 自己判断，
# 这样 401 的文案和响应头由我们控制（默认实现会回一句英文 Not authenticated）。
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/login", auto_error=False)


# ---------------------------------------------------------------------------
# 模型
# ---------------------------------------------------------------------------
# 请求模型就是接口合同：字段名、类型、长度限制都写在这，校验不过 FastAPI 自动回 422
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(default="", max_length=100)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ConversationCreate(BaseModel):
    title: str = Field(default=DEFAULT_TITLE, max_length=200)


class ChatTurn(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    # 推理模型的 reasoning 与正文共用这个预算，512 常被思考吃光 → 正文为空
    max_tokens: int = Field(default=4096, ge=1, le=4096)


# 401 必须带 WWW-Authenticate 头（HTTP 规范要求），否则部分客户端不会弹登录框
def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------
# 鉴权闸门：所有要登录的接口都挂这个依赖。四步：
#   ① 从请求头取 token  ② 验签并解出 sub（user id）  ③ 查库取 User  ④ 看是否被禁用
# 第 ③ 步不能省：token 是无状态的，签发之后没法主动作废，
# 只有每次查库，才能实现「禁用账号立刻生效」。
def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    session: Session = Depends(store.get_session),
) -> User:
    # 三种失败在这里汇合：没带 token / 验签失败（伪造或过期）/ 用户不存在或被禁用
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
    # session.get(User, pk) 发的就是一条主键查询：
    #   SELECT ... FROM users WHERE users.id = $1
    # 之所以有时候看起来「没查库」：SQLAlchemy 的 Session 有 identity map
    # （同一个 Session 里按主键取过的对象会直接命中内存，不再发 SQL）。
    # 但 FastAPI 每个请求都给一个新 Session（见 db.get_session），
    # 所以**跨请求永远是一次真实的 SELECT** —— 这正是「禁用账号立刻生效」的代价。
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise _unauthorized("用户不存在或已禁用")
    return user


# 归属校验：会话不存在、会话是别人的，都返回 404。
# 为什么不返回 403：403 等于告诉攻击者「这个 id 真实存在，只是不给你看」，属于信息泄露。
def _owned(session: Session, conversation_id: int, user: User) -> Conversation:
    conv = session.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id:
        raise HTTPException(status_code=404, detail="conversation 不存在")
    return conv


# ---------------------------------------------------------------------------
# 认证接口
# ---------------------------------------------------------------------------
@app.get("/health", summary="探活")
def health() -> dict[str, object]:
    conn = store.check_connection()
    cfg = llm.get_client().config
    return {
        **conn,
        "model": cfg.model,
        "key_configured": not llm._is_placeholder(cfg.api_key),
        "max_context_messages": MAX_CONTEXT_MESSAGES,
    }


# 邮箱统一转小写再入库：否则 A@x.com 和 a@x.com 会注册成两个账号，登录时对不上
@app.post("/register", status_code=201, summary="注册")
def register(payload: RegisterRequest, session: Session = Depends(store.get_session)) -> dict:
    user = User(
        email=str(payload.email).lower(),
        display_name=payload.display_name,
        password_hash=security.hash_password(payload.password),
    )
    # ① add()：只把对象登记进 Session 的「待写入」集合 —— 此刻还没发 SQL
    session.add(user)
    try:
        # ② commit()：这时才真正 INSERT，同时结束事务。成功后对象拿到自增主键
        session.commit()
    # 用数据库的唯一约束兜并发：先查再插存在竞态窗口（两个请求同时查到「不存在」）
    except IntegrityError:
        # rollback()：事务已经失败，必须回滚收尾；不回滚的话这个 Session 之后每个操作都会报错
        session.rollback()
        raise HTTPException(status_code=409, detail="email 已注册") from None
    # ③ refresh()：commit 让属性过期了，这里显式重读一行，确保拿到数据库的真实值
    session.refresh(user)
    return {"id": user.id, "email": user.email}


# 登录用 OAuth2PasswordRequestForm：OAuth2 规范要求表单格式，字段名固定 username / password
# （email 塞进 username）。用 JSON 调会 422 —— 这是新手最常踩的坑。
@app.post("/login", response_model=TokenResponse, summary="登录")
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(store.get_session),
) -> TokenResponse:
    email = (form.username or "").strip().lower()
    # exec(select(...))：Session 是查询入口。注意 select() 只是构造语句，真正执行在这一步
    # .first() 拿第一行或 None（配合下面的「同一句错误提示」防账号枚举）
    user = session.exec(select(User).where(User.email == email)).first()
    # 用户不存在与密码错误返回同一句话：防账号枚举，否则攻击者能逐个探测哪些邮箱注册过
    if user is None or not security.verify_password(form.password, user.password_hash):
        raise _unauthorized("邮箱或密码错误")
    if not user.is_active:
        raise _unauthorized("用户已被禁用")
    return TokenResponse(
        access_token=security.create_access_token(user.id),
        expires_in=security.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@app.get("/me", summary="当前用户")
def me(user: User = Depends(get_current_user)) -> dict:
    return {"id": user.id, "email": user.email, "display_name": user.display_name}


# ---------------------------------------------------------------------------
# 会话
# ---------------------------------------------------------------------------
@app.post("/conversations", summary="新建会话")
def create_conversation(
    payload: ConversationCreate,
    user: User = Depends(get_current_user),
    session: Session = Depends(store.get_session),
) -> dict:
    conv = Conversation(user_id=user.id, title=payload.title)
    # 三步曲：add 登记 → commit 落库 → refresh 回填 id
    session.add(conv)
    session.commit()
    session.refresh(conv)
    return {"id": conv.id, "title": conv.title}


@app.get("/conversations", summary="我的会话列表")
def list_conversations(
    user: User = Depends(get_current_user),
    session: Session = Depends(store.get_session),
) -> list[dict]:
    # select() 只是把 SQL 拼成对象，不发请求；执行发生在下面的 exec()
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    )
    out = []
    # exec(stmt).all()：到这里才发 SQL。查出来的 Conversation 对象会进 identity map，
    # 后面再按同一主键取就不会重复查库
    for c in session.exec(stmt).all():
        # 注意这里是 N+1 查询：列表里每个会话各查一次最后一条消息。
        # 会话多时应该改成一次 group by 或子查询，别让列表接口退化成 N 次 IO。
        last = session.exec(
            select(Message)
            .where(Message.conversation_id == c.id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        ).first()
        out.append(
            {
                "id": c.id,
                "title": c.title,
                "updated_at": c.updated_at.isoformat(),
                "last_message": (last.content[:60] if last else ""),
            }
        )
    return out


# 只删会话这一行，消息由数据库 ON DELETE CASCADE 带走 —— 应用层不写删消息的代码
@app.delete("/conversations/{conversation_id}", summary="删会话")
def delete_conversation(
    conversation_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(store.get_session),
) -> dict:
    conv = _owned(session, conversation_id, user)
    # delete() 只是标记待删除，commit() 时才真正发 DELETE（消息由数据库 CASCADE 带走）
    session.delete(conv)
    session.commit()
    return {"deleted": conversation_id}


@app.get("/conversations/{conversation_id}/messages", summary="拉消息")
def list_messages(
    conversation_id: int,
    user: User = Depends(get_current_user),
    session: Session = Depends(store.get_session),
    # limit 设上限 500：防止有人一次拉十万条把内存和服务打满
    limit: int = Query(default=200, ge=1, le=500),
) -> list[dict]:
    _owned(session, conversation_id, user)
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)
        .limit(limit)
    )
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "tokens": m.tokens,
            "created_at": m.created_at.isoformat(),
        }
        for m in session.exec(stmt).all()
    ]


# ---------------------------------------------------------------------------
# 核心：流式对话 + 落库
# ---------------------------------------------------------------------------
def build_context(session: Session, conversation_id: int) -> list[dict[str, str]]:
    """取最近 N 条消息作为上下文。

    注意 order_by 用 (created_at, id) 双排序：同一秒写入的两条消息，
    created_at 可能完全相同，只按时间排会出现顺序错乱（用户消息跑到助手消息后面）。
    加 id 兜底保证稳定顺序。
    """
    stmt = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(MAX_CONTEXT_MESSAGES)
    )
    rows = list(session.exec(stmt).all())
    rows.reverse()  # 倒序取最近 N 条，再翻回正序
    return [{"role": m.role, "content": m.content} for m in rows]


def _auto_title(session: Session, conv: Conversation, first_text: str) -> None:
    """首条用户消息自动当标题，省得用户面对一排"新对话"。"""
    if conv.title == DEFAULT_TITLE:
        conv.title = first_text.strip().replace("\n", " ")[:20] or DEFAULT_TITLE
        session.add(conv)


# 一次对话的完整时序（本单元的核心难点）：
#   ① 鉴权 + 归属校验   ② 用户消息先落库   ③ 组装上下文（system + 最近 N 条）
#   ④ 流式转发给前端     ⑤ 助手消息落库（正常结束 / 报错 / 客户端断开 都要存）
@app.post("/conversations/{conversation_id}/chat", summary="流式对话（SSE，自动落库）")
async def chat_turn(
    conversation_id: int,
    turn: ChatTurn,
    request: Request,
    user: User = Depends(get_current_user),
    session: Session = Depends(store.get_session),
) -> StreamingResponse:
    # 步骤 0：这个会话必须是当前用户的；不是就 404（越权判断统一在 _owned 里）
    conv = _owned(session, conversation_id, user)

    # ① 用户消息**先入库**。即使马上被限流或模型报错，用户说过的话也要留痕，
    #    否则刷新页面这条消息就凭空消失了，用户会以为是自己没发出去。
    # tokens 记 0：计费大头在输出侧，用户输入的 token 不在这里精确统计
    user_msg = Message(conversation_id=conv.id, role="user", content=turn.content, tokens=0)
    # ① 用户消息先落库：add → commit → refresh，和 register 一样的三步曲
    session.add(user_msg)
    _auto_title(session, conv, turn.content)
    session.commit()
    session.refresh(user_msg)
    # refresh 之后 id 已回填，生成器里才能安全地用 user_msg.id

    # 历史里已经包含刚写入的这条用户消息，所以不要再手动拼一次，否则模型会看到两遍
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + build_context(session, conv.id)

    # 生成器：每次 yield 都会被 StreamingResponse 立刻 flush 给客户端，这就是「流式」的来源
    async def gen():
        # parts 累积正文 —— 它是唯一能保证「断开也存得下半句」的来源，别指望回头从事件里拼
        parts: list[str] = []
        # finished_cleanly：是否收到过 done 事件，用来区分「正常结束」和「被中途打断」
        finished_cleanly = False
        error: str | None = None

        try:
            # 先发 start：带上 user_message_id，前端可以立刻对齐它刚画的那个气泡
            yield sse({"type": "start", "user_message_id": user_msg.id})

            try:
                async for event in llm.get_client().chat_stream(
                    messages,
                    temperature=turn.temperature,
                    max_tokens=turn.max_tokens,
                ):
                    # 只把 delta 收进 parts；first_token / retry / usage 不参与落库
                    if event.get("type") == "delta":
                        parts.append(event["content"])
                    # 每块都检查客户端还在不在：用户关页面后没必要继续调模型烧 token
                    if await request.is_disconnected():
                        break
                    # 原样转发：事件协议由 llm_client 定义，这一层只负责搬运
                    yield sse(event)
                    # done 里带 usage 和 cost，收到它才算这次真的跑完
                    if event.get("type") == "done":
                        finished_cleanly = True
                    if event.get("type") == "error":
                        error = event.get("message", "未知错误")
            # LLMError = 重试也救不回来的那类（配置错 / 鉴权失败 / 参数错）
            except llm.LLMError as exc:
                error = str(exc)
                yield sse({"type": "error", "kind": type(exc).__name__, "message": str(exc)})
        # finally 是这段代码的核心保险：正常结束、报错、客户端断开，三种结局都会走到这里
        finally:
            # ③ 助手消息入库（哪怕只有半句）。
            #    必须放在 finally：客户端点「停止」/关页面时，框架会向这个生成器抛
            #    GeneratorExit，写在循环后面的代码根本不会执行 —— 那样
            #    "已生成部分自动保存" 就是假的（2026-10-25 实测确实丢了半句话）。
            # 只有真产出过内容才写：空白 assistant 消息会把历史弄脏（自检里有一条专门查这个）
            content = "".join(parts)
            if content:
            # 另开一个 Session：请求级 session 可能在流还没结束时就已经关闭
                # 这里必须新开一个 Session：请求级 session 会随请求结束被关掉，
                # 而流式响应还没结束。自己开、自己提交、with 退出自动关。
                # 也顺便绕开了 identity map：拿到的一定是数据库里的最新状态。
                with Session(store.engine) as s2:
                    s2.add(
                        Message(
                            conversation_id=conversation_id,
                            role="assistant",
                            content=content,
                            tokens=len(content),
                        )
                    )
            # 顺手刷新会话 updated_at，列表才会按最近活跃排序
                    c2 = s2.get(Conversation, conversation_id)
                    if c2:
                        c2.updated_at = datetime.now(timezone.utc)
                        s2.add(c2)
                    s2.commit()

        # saved 事件回答前端两个问题：这次存了多少字、是不是被打断的
        yield sse(
            {
                "type": "saved",
                "chars": len(content),
                "interrupted": not finished_cleanly and not error,
                "error": error,
            }
        )
        # [DONE] 是 SSE 的结束标记，前端看到它就可以收手，不用等连接自然关闭
        yield "data: [DONE]\n\n"

    # 三个响应头都是为了「别缓冲」：代理（nginx）默认会攒一批再转发，
# X-Accel-Buffering: no 是明确告诉 nginx 这个响应要即时透传，否则流式会变成一次性吐出。
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# SSE 帧格式固定为 `data: <json>` 加一个空行。少那个空行，前端永远拼不出完整消息
def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


# 成本账本存在进程内存里：多 worker / 多实例各算各的，生产要落库才能汇总
@app.get("/stats", summary="成本账本")
def stats() -> dict[str, object]:
    return llm.LEDGER.as_dict()


# 静态页由后端直接返回 → 页面和接口天然同源，这也是同源时完全不需要 CORS 的原因
@app.get("/", include_in_schema=False)
def index():
    if os.path.exists(STATIC_INDEX):
        return FileResponse(STATIC_INDEX)
    return {"hint": "static/index.html 不存在，看 /docs"}


# ---------------------------------------------------------------------------
# 自检：用假 LLM，不花钱
# ---------------------------------------------------------------------------
# 只有 `python main.py` 才会进这里。`uvicorn main:app` 是「导入模块取 app」，
# 此时 __name__ == "main"，这个 if 不成立 → 自检不跑（所以起服务时不会执行测试）。
if __name__ == "__main__":
    import uuid

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

    # 假 LLM：吐固定文字、不走网络 —— 所以自检不花钱，也不依赖真实 key
    class FakeLLM:
        """替身：记录收到的 messages，按预设脚本吐 delta。"""

        def __init__(self, text: str = "你好世界", fail: bool = False):
            self.text = text
            self.fail = fail
            self.seen_messages: list[list[dict]] = []

        async def chat_stream(self, messages, **kwargs):
            self.seen_messages.append(messages)
            if self.fail:
                yield {"type": "error", "kind": "LLMRateLimitError", "message": "上游限流"}
                return
            for ch in self.text:
                await asyncio.sleep(0)
                yield {"type": "delta", "content": ch}
            yield {
                "type": "done",
                "elapsed": 0.01,
                "attempts": 1,
                "retries": 0,
                "prompt_tokens": 10,
                "completion_tokens": len(self.text),
                "cost": 0.0,
            }

    print("Unit 14 · 项目 1 收口自检（假 LLM，不花钱）\n")

    conn = store.check_connection()
    if not conn.get("ok"):
        print(f"  [SKIP] 连不上数据库：{conn.get('error')}")
        sys.exit(1)

    ensure_schema()
    # TestClient 把请求直接喂给 app（进程内 ASGI），不绑端口
    # → 自检可以和 8014 上跑着的服务同时进行，互不干扰
    client = TestClient(app)
    tag = uuid.uuid4().hex[:8]
    email = f"p1-{tag}@example.com"
    pwd = "CorrectHorse2026"

    # --- 认证 ---
    r = client.post("/register", json={"email": email, "password": pwd, "display_name": "p1"})
    check("注册成功", r.status_code == 201, str(r.status_code))
    r = client.post("/login", data={"username": email, "password": pwd})
    check("登录成功", r.status_code == 200, str(r.status_code))
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}

    r = client.get("/conversations")
    check("不带 token → 401", r.status_code == 401 and not H == {}, str(r.status_code))
    r = client.get("/conversations", headers=H)
    check("带 token 能列会话", r.status_code == 200)

    # --- 建会话 ---
    r = client.post("/conversations", json={}, headers=H)
    conv_id = r.json()["id"]
    check("新建会话默认标题为「新对话」", r.json()["title"] == DEFAULT_TITLE, r.json()["title"])

    # --- 流式对话 ---
    fake = FakeLLM("流式回答的第一句")
    llm._default_client = fake  # type: ignore[assignment]

    with client.stream(
        "POST", f"/conversations/{conv_id}/chat", json={"content": "你好啊"}, headers=H
    ) as resp:
        check("流式接口返回 200", resp.status_code == 200, str(resp.status_code))
        check("content-type 是 event-stream", resp.headers["content-type"].startswith("text/event-stream"))
        body = "".join(resp.iter_text())

    check("收到 start 事件", '"type": "start"' in body or '"type":"start"' in body)
    # 逐字输出：每个 delta 只有一个汉字，所以不能断言"流式"这种连续串
    delta_n = body.count('"type": "delta"') + body.count('"type":"delta"')
    check("逐字收到 8 个 delta 事件", delta_n == len("流式回答的第一句"), f"实际 {delta_n} 个")
    check("delta 内容是单个汉字", '"流"}' in body or '"流" }' in body, body[:160])
    check("收到 saved 落库确认", "saved" in body)
    check("有 [DONE] 结束标记", "[DONE]" in body)

    # --- 落库校验 ---
    r = client.get(f"/conversations/{conv_id}/messages", headers=H)
    msgs = r.json()
    check("落库 2 条消息（user + assistant）", len(msgs) == 2, str(len(msgs)))
    check("顺序正确：user 在前", [m["role"] for m in msgs] == ["user", "assistant"], str([m["role"] for m in msgs]))
    check("assistant 内容完整", msgs[-1]["content"] == "流式回答的第一句", msgs[-1]["content"])

    # --- 自动标题 ---
    r = client.get("/conversations", headers=H)
    title = next(c["title"] for c in r.json() if c["id"] == conv_id)
    check("首条消息自动当标题", title == "你好啊", title)

    # --- 客户端中断：已生成的部分必须落库 ---
    # 回归：以前落库写在循环后面，客户端断开时框架抛 GeneratorExit，那段代码不执行，
    # 结果「停止生成」后 assistant 消息一条都不剩（2026-10-25 实测）。
    r = client.post("/conversations", json={}, headers=H)
    conv_cut = r.json()["id"]
    fake_cut = FakeLLM("这是一段会被中途打断的长回答内容")
    llm._default_client = fake_cut
    got = 0
    with client.stream(
        "POST", f"/conversations/{conv_cut}/chat", json={"content": "讲讲中断"}, headers=H
    ) as resp:
        for chunk in resp.iter_text():
            if '"delta"' in chunk:
                got += 1
            if got >= 3:
                break
    time.sleep(0.3)
    msgs_cut = client.get(f"/conversations/{conv_cut}/messages", headers=H).json()
    asst = [m for m in msgs_cut if m["role"] == "assistant"]
    saved_len = len(asst[0]["content"]) if asst else 0
    check("中断后 assistant 仍落库", len(asst) == 1, str([m["role"] for m in msgs_cut]))
    check(
        "落库的是已生成的部分（非空且少于全文）",
        bool(asst) and 0 < saved_len < len(fake_cut.text),
        f"{saved_len} / {len(fake_cut.text)}",
    )

    # --- 上下文构建 ---
    sent = fake.seen_messages[-1]
    check("上下文第一条是 system prompt", sent[0]["role"] == "system")
    check("上下文里带上了刚才的 user 消息", any(m["content"] == "你好啊" for m in sent), str(len(sent)))

    # --- 上下文裁剪 ---
    for i in range(MAX_CONTEXT_MESSAGES + 5):
        client.post(
            f"/conversations/{conv_id}/chat",
            json={"content": f"填充 {i}", "max_tokens": 16},
            headers=H,
        )
    sent2 = fake.seen_messages[-1]
    check(
        f"上下文裁剪到 ≤ {MAX_CONTEXT_MESSAGES} 条",
        len(sent2) - 1 <= MAX_CONTEXT_MESSAGES,
        f"实际 {len(sent2) - 1} 条",
    )

    # --- LLM 报错时：用户消息已入库，助手消息不写 ---
    r = client.post("/conversations", json={"title": "报错测试"}, headers=H)
    conv_err = r.json()["id"]
    llm._default_client = FakeLLM(fail=True)  # type: ignore[assignment]
    with client.stream(
        "POST", f"/conversations/{conv_err}/chat", json={"content": "这句话要留下"}, headers=H
    ) as resp:
        body_err = "".join(resp.iter_text())
    check("上游报错会推 error 事件", "error" in body_err)
    r = client.get(f"/conversations/{conv_err}/messages", headers=H)
    err_msgs = r.json()
    check("报错后用户消息仍在", len(err_msgs) == 1 and err_msgs[0]["role"] == "user", str(len(err_msgs)))
    check("报错后不写空的 assistant 消息", all(m["role"] != "assistant" for m in err_msgs))

    # --- 越权 ---
    email2 = f"p1b-{tag}@example.com"
    client.post("/register", json={"email": email2, "password": pwd})
    t2 = client.post("/login", data={"username": email2, "password": pwd}).json()["access_token"]
    H2 = {"Authorization": f"Bearer {t2}"}
    llm._default_client = FakeLLM("偷看")  # type: ignore[assignment]
    r = client.post(f"/conversations/{conv_id}/chat", json={"content": "越权"}, headers=H2)
    check("B 对 A 的会话发消息 → 404", r.status_code == 404, str(r.status_code))

    # --- 清理 ---
    with Session(store.engine) as s:
        for em in (email, email2):
            u = s.exec(select(User).where(User.email == em)).first()
            if u:
                s.delete(u)
        s.commit()

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
