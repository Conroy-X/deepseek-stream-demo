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
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy import text as sa_text
from sqlmodel import Field, Session, SQLModel, create_engine

load_dotenv(Path(__file__).with_name(".env"))

DEFAULT_URL = "postgresql+psycopg://rik@/ai_journey?host=/var/run/postgresql"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_URL).strip()


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: int | None = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
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
    is_active: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=sa_text("true")),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"
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
    tokens: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


def make_engine(url: str | None = None, echo: bool = False):
    return create_engine(
        url or DATABASE_URL,
        echo=echo,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=1800,
    )


engine = make_engine()


def get_session() -> Iterator[Session]:
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
    SQLModel.metadata.create_all(eng)

    applied: list[str] = []
    with eng.begin() as conn:
        for name, sql in _MIGRATIONS:
            col = name.split(".")[1]
            table = name.split(".")[0]
            exists = conn.execute(
                sa_text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c"
                ),
                {"t": table, "c": col},
            ).first()
            conn.execute(sa_text(sql))
            if not exists:
                applied.append(name)
    return applied


def check_connection(target_engine=None) -> dict[str, object]:
    try:
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


def touch_conversation(session: Session, conversation_id: int) -> None:
    conv = session.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = datetime.now()
        session.add(conv)
