"""Unit 11 · 数据层：SQLModel 三张表 + PostgreSQL。

设计要点：
    1. 三张表 users / conversations / messages，用外键串起来；
    2. 删除会话时消息要跟着走 —— 用数据库的 ON DELETE CASCADE，
       不要靠 Python 层手写删除逻辑（漏一处就留脏数据）；
    3. 时间戳全部交给数据库生成（server_default=now()），
       应用服务器时区不一致也不会写进错的时间；
    4. 索引 (conversation_id, created_at) 是"拉一个会话的消息"这类查询的命脉。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy import text as sa_text
from sqlmodel import Field, Session, SQLModel, create_engine

load_dotenv(Path(__file__).with_name(".env"))

# postgresql+psycopg:// 是三段式：dialect+driver://user:pass@host:port/dbname
DEFAULT_URL = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/ai_journey"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_URL).strip()


# ---------------------------------------------------------------------------
# 表定义
# ---------------------------------------------------------------------------
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
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"
    # 拉"某用户的会话列表，按最近活跃排序"用这个索引
    __table_args__ = (Index("ix_conversations_user_updated", "user_id", "updated_at"),)

    id: int | None = Field(
        default=None,
        sa_column=Column(Integer, primary_key=True, autoincrement=True),
    )
    user_id: int = Field(
        sa_column=Column(
            Integer,
            # ondelete="CASCADE"：删用户，会话跟着删。这是数据库层保证，不依赖应用代码
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
    # 本单元的重点索引：WHERE conversation_id = ? ORDER BY created_at
    # 有了它，PG 可以直接走索引有序扫描，不需要额外 Sort。
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
    # role 只有三种，用 String + CHECK 比 ENUM 好迁移
    role: str = Field(sa_column=Column(String(16), nullable=False))
    content: str = Field(sa_column=Column(Text, nullable=False))
    # 记 token 数是为了 Unit 13 的成本统计
    tokens: int = Field(
        default=0,
        sa_column=Column(Integer, nullable=False, server_default="0"),
    )
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=func.now()),
    )


# ---------------------------------------------------------------------------
# 引擎与会话
# ---------------------------------------------------------------------------
def make_engine(url: str | None = None, echo: bool = False):
    """建引擎。

    pool_pre_ping=True 很关键：数据库重启或连接被中间设备掐断时，
    连接池里的死连接会被自动剔除，否则第一个请求必然报
    "server closed the connection unexpectedly"。
    """
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
    """FastAPI 依赖：每个请求一个 Session，请求结束自动归还连接。"""
    with Session(engine) as session:
        yield session


def init_db(target_engine=None) -> None:
    """建表。幂等，已存在不会报错。

    注意：create_all() 只"建缺的表"，**不会改已有表的结构**。
    字段改动/重命名必须走 Alembic 迁移 —— 详见 notes.md。
    """
    SQLModel.metadata.create_all(target_engine or engine)


def check_connection(target_engine=None) -> dict[str, object]:
    """探活：返回数据库版本与当前库名，给 /health 和自检用。"""
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


def explain_message_query(target_engine=None, conversation_id: int = 1) -> str:
    """看查询计划：判断 (conversation_id, created_at) 索引有没有被用上。

    关键看法：
      · 出现 "Index Scan using ix_messages_conversation_created" → 走索引，且结果天然有序
      · 出现 "Seq Scan" + "Sort"                          → 全表扫 + 额外排序，索引没生效
    """
    sql = (
        "EXPLAIN (ANALYZE, BUFFERS) "
        "SELECT * FROM messages WHERE conversation_id = :cid ORDER BY created_at LIMIT 50"
    )
    with (target_engine or engine).connect() as conn:
        rows = conn.execute(sa_text(sql), {"cid": conversation_id}).fetchall()
    return "\n".join(r[0] for r in rows)


def touch_conversation(session: Session, conversation_id: int) -> None:
    """新消息写入后刷新会话的 updated_at，让列表按活跃度排序。"""
    conv = session.get(Conversation, conversation_id)
    if conv:
        conv.updated_at = datetime.now()
        session.add(conv)
