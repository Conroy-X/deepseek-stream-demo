"""Unit 12 · 数据层：在 Unit 11 的三张表上加 password_hash。

和 Unit 11 的 db.py 只差两处：
    1. users 表多了 password_hash 列；
    2. 多了 ensure_schema()，负责把已经存在的老表补齐。

第 2 点是本单元真正的教学点：**create_all() 不会给已有表加列**。
Unit 11 的库已经建好了 users 表，现在要加字段，只能自己写 ALTER。
生产上这件事交给 Alembic，这里手写一版让你看清迁移到底在做什么。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
# SQLAlchemy 的列类型：SQLModel 只是它的语法糖，遇到唯一约束/默认值/时区这类# 复杂需求还得回到 Column 来写
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy import text as sa_text
from sqlmodel import Field, Session, SQLModel, create_engine

load_dotenv(Path(__file__).with_name(".env"))

# 默认连本机 Unix socket（peer 认证，免密）；换 Docker 时只改 .env 里的 DATABASE_URL
DEFAULT_URL = "postgresql+psycopg://rik@/ai_journey?host=/var/run/postgresql"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_URL).strip()


# class Xxx(SQLModel, table=True)：table=True 表示这是真表，不是普通数据类。
# 一个 class 同时承担三件事：建表 DDL、查询结果类型、以及 Pydantic 校验。
class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    # 登录名 → 必须 unique + index：唯一约束防重复注册，索引让登录查询走索引
    email: str = Field(
        sa_column=Column(String(255), nullable=False, unique=True, index=True),
    )
    display_name: str = Field(
        default="",
        sa_column=Column(String(100), nullable=False, server_default=""),
    )
    # 存的是 Argon2id 哈希串，绝不存明文、不存可逆加密
    password_hash: str = Field(
        default="",
        sa_column=Column(String(255), nullable=False, server_default=""),
    )
    # 账号开关：token 还有效、但人已被禁用时，get_current_user 会拦住（见 main.py）
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=sa_text("true")),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


# 会话表：user_id 外键 + ON DELETE CASCADE —— 删用户，会话跟着没，由数据库保证
class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"
    # 复合索引 (user_id, updated_at)：正好服务「列出我的会话、按最近活跃排序」这个查询。
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)

    id: int | None = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    # server_default 让「给老表 ALTER 加列」时有值可填：已有行不知道该填什么时用这个兜底
    title: str = Field(
        default="新对话",
        sa_column=Column(String(200), nullable=False, server_default="新对话"),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


# 消息表：全库最大的表。conversation_id 外键 + CASCADE，删会话自动清空消息
class Message(SQLModel, table=True):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    conversation_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    role: str = Field(sa_column=Column(String(16), nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))
    # tokens 这里存的是字符数（廉价估算）；真实计费口径看 llm_client 记账里的 usage
    tokens: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


# 连接池：整个进程共用一组连接，不是每个请求都新建（建 TCP + 认证很贵）。
#   pool_pre_ping  借出前先 ping，避开被数据库踢掉的死连接（PG 会主动断空闲连接）
#   pool_size      常驻连接数；max_overflow 峰值时允许临时多开几个
#   pool_recycle   连接最长复用 30 分钟，超时回收，防止用到已被服务端关掉的连接
def make_engine(url: str | None = None, echo: bool = False):
    return create_engine(
        url or DATABASE_URL,
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
    )


# 模块级单例：import 时就建好连接池，全进程共用
engine = make_engine()


# ---------------------------------------------------------------------------
# Session 是什么（本单元几乎每个数据库操作都经过它）
# ---------------------------------------------------------------------------
# 一句话：Session = 一次「工作单元」+ 一级缓存 + 事务边界。
# 它不是连接 —— 连接由 engine 的连接池管着，Session 借一个、用完还回去。
#
# 生命周期：一次请求一个（见下面的 get_session），请求结束就 close。
#   · 不要在模块级共享一个 Session：并发不安全，还会把对象泄漏给别的请求
#   · 不要跨线程/跨协程传递它
#
# 方法速查（下面每个都在 main.py 里真实用到）：
#   session.add(obj)            登记为「新增」，此刻不发 SQL
#   session.delete(obj)         登记为「删除」，commit 时才发 DELETE
#   session.get(Model, pk)      按主键取：先查 identity map，没有才 SELECT
#   session.exec(select(...))   执行查询；.first() / .all() 取结果
#   session.commit()            真正写库 + 结束事务；成功后对象属性会过期
#   session.rollback()          回滚。异常之后必须调用，否则这个 Session 不可用
#   session.refresh(obj)        立刻重读这一行，把过期属性刷成数据库真实值
#   session.close()             释放连接（with 退出时自动执行）
#
# 两个必须知道的默认行为：
#   ① 事务「隐式开始」：第一次查询/写入就开启事务，直到 commit/rollback 结束。
#      所以别在一个 Session 里先查一堆东西、再去调模型 —— 连接会被长时间占住。
#   ② commit 之后 expire_on_commit=True：对象属性全部过期（__dict__ 被清空），
#      下次访问会**再发一条 SELECT** 重新加载。refresh() 只是把这次加载提前、显式化。
# FastAPI 依赖注入用：Depends(store.get_session) 每个请求开一个 Session，
# 请求结束自动 close（with 退出）。别在模块级共享一个 Session —— 它不保证并发安全。
def get_session() -> Iterator[Session]:
    # with 退出时自动 close()：连接还回池子、identity map 丢掉、未提交事务回滚。
    # yield 把 Session 交给 FastAPI：请求处理完，FastAPI 回来继续执行 finally 收尾。
    with Session(engine) as session:
        yield session


# ---------------------------------------------------------------------------
# 迁移
# ---------------------------------------------------------------------------
# 每条迁移都必须是幂等的（IF NOT EXISTS），否则第二次启动就炸。
_MIGRATIONS: list[tuple[str, str]] = [
    (
        "users.password_hash",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash VARCHAR(255) NOT NULL DEFAULT ''",
    ),
    (
        "users.is_active",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT true",
    ),
]


# create_all() 只建「不存在的表」，不会给已有表加列 —— 这就是必须手写 ALTER 的原因
def ensure_schema(target_engine=None) -> list[str]:
    """建表 + 补列。返回实际执行过的迁移名，方便自检和日志。

    步骤顺序很重要：
        1. 先 create_all()  —— 新环境一次到位
        2. 再跑 ALTER 列表  —— 老环境（Unit 11 建的）补齐缺的列

    为什么 NOT NULL 要带 DEFAULT ''：表里已有数据时，
    ALTER TABLE ADD COLUMN ... NOT NULL 不带默认值会直接报错
    （现有行不知道该填什么）。这是加列最经典的坑。
    """
    eng = target_engine or engine
    # 第一步：新环境一次建全（表已存在则跳过，不会重复建）
    SQLModel.metadata.create_all(eng)

    # applied 只用来回答「这次真做了什么」，给日志和自检断言用
    applied: list[str] = []
    # 所有 ALTER 放同一个事务：要么全成功、要么全回滚，不会留下半迁移状态
    with eng.begin() as conn:
        # name 形如 users.password_hash，拆开是为了去 information_schema 里查
        for name, sql in _MIGRATIONS:
            col = name.split(".")[1]
            table = name.split(".")[0]
            # 先查列在不在，只为了知道「这次真的执行了哪些迁移」（日志和自检要用），不影响 SQL
            exists = conn.execute(
                sa_text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c"
                ),
                {"t": table, "c": col},
            ).first()
            # 不管列在不在都执行 —— SQL 里自带 IF NOT EXISTS，天然幂等，可以反复跑
            conn.execute(sa_text(sql))
            # 执行前不存在、执行后才有的，才算「这次补上的列」
            if not exists:
                applied.append(name)
    return applied


# /health 用它：连不上就返回 ok:false，而不是抛异常，保证探活接口永远有响应
def check_connection(target_engine=None) -> dict[str, object]:
    try:
    # 对比一下：这里用 engine.connect() 而不是 Session ——
    # 只是跑一条 SELECT 看看版本，不需要工作单元/事务/对象身份这些语义。
        with (target_engine or engine).connect() as conn:
            version = conn.execute(sa_text("SELECT version()")).scalar_one()
            dbname = conn.execute(sa_text("SELECT current_database()")).scalar_one()
            tables = [
                r[0]
                for r in conn.execute(
                    sa_text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'public' ORDER BY tablename"
                    )
                )
            ]
        return {"ok": True, "version": str(version).split(",")[0], "database": dbname, "tables": tables}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# 新消息写入后刷新会话的 updated_at，列表才会按「最近活跃」排序
# （用冗余字段换查询效率：否则每次列表都要对 messages 做 MAX 子查询）
def touch_conversation(session: Session, conversation_id: int) -> None:
    conv = session.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = datetime.now()
        session.add(conv)
