"""Unit 12 · 认证三件套：密码哈希 / JWT 签发 / JWT 校验。

三条铁律，写错了会出安全事故：
    1. 密码永远不落明文、也不落"可逆加密"，只能存**单向哈希**（Argon2id / bcrypt）；
    2. 哈希要带盐，盐由算法自己随机生成并存进哈希串 —— 不要自己拼接；
    3. JWT 只是"签过名的声明"，不是加密。任何人 base64 解开都能读 payload，
       所以里面绝对不能放密码、手机号这类敏感信息。

本模块刻意只做纯函数，不碰数据库 —— 好测、可复用。
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from dotenv import load_dotenv
from pwdlib import PasswordHash
from pwdlib.hashers.argon2 import Argon2Hasher

load_dotenv(Path(__file__).with_name(".env"))

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
# HS256 是对称签名：同一个密钥既签又验。密钥泄露 = 谁都能伪造任何用户的 token，
# 所以生产环境必须用足够长的随机串，且不能进 git。
JWT_SECRET = os.getenv("JWT_SECRET", "").strip()
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

_DEV_SECRET_WARNING = (
    "⚠️ 没有配置 JWT_SECRET，正在使用进程级随机密钥（重启后所有 token 失效）。"
    "生产环境必须在 .env 里设置一个足够长的随机串："
    "python -c \"import secrets; print(secrets.token_urlsafe(48))\""
)


def get_secret() -> str:
    """拿签名密钥。

    开发环境没配就临时随机一个（重启全失效，正好提醒你去配）；
    生产环境必须显式配置，否则多实例部署时 A 签的 token 到 B 验不过。
    """
    global _RUNTIME_SECRET
    if JWT_SECRET:
        return JWT_SECRET
    if _RUNTIME_SECRET is None:
        _RUNTIME_SECRET = secrets.token_urlsafe(48)
    return _RUNTIME_SECRET


_RUNTIME_SECRET: str | None = None


# ---------------------------------------------------------------------------
# 密码哈希
# ---------------------------------------------------------------------------
# 为什么用 Argon2 而不是 bcrypt：
#   bcrypt 有 72 字节密码截断问题，且不支持内存硬化，对 GPU 爆破的抵抗力弱于 Argon2id。
#   OWASP 目前的首选就是 Argon2id。
# 为什么不用 passlib：
#   passlib 已停止维护，和 bcrypt 4.x 有著名的 __about__ 兼容报错。
#   FastAPI 官方文档现在也用 pwdlib 了。
password_hash = PasswordHash((Argon2Hasher(),))


def hash_password(plain: str) -> str:
    """生成带盐的哈希串（形如 $argon2id$v=19$m=65536,t=3,p=4$...）。"""
    return password_hash.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """校验密码。哈希串格式非法时返回 False 而不是抛异常，
    否则攻击者能靠"500 还是 401"判断出邮箱是否注册过。
    """
    try:
        return password_hash.verify(plain, hashed)
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def create_access_token(
    subject: str | int,
    expires_minutes: int | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """签一个 access token。

    payload 里的标准字段：
        sub  主体（这里放 user id，规范要求是字符串）
        exp  过期时间（**UTC**，PG/JWT 全用 UTC，本地时区写进去必错）
        iat  签发时间
        jti  唯一 id，将来做"主动吊销"时按它进黑名单
    """
    # 时间一律用 UTC：JWT 的 exp 语义就是 UTC 秒数。写本地时间会整体偏 8 小时，
    # 表现是「刚签的 token 立刻过期」（东八区）或「早就该过期了还能用」（西半球）。
    now = datetime.now(timezone.utc)
    minutes = expires_minutes if expires_minutes is not None else ACCESS_TOKEN_EXPIRE_MINUTES
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=minutes)).timestamp()),
        "jti": secrets.token_urlsafe(12),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, get_secret(), algorithm=JWT_ALGORITHM)


class TokenError(Exception):
    """token 校验失败的统一异常，携带对外的错误原因。"""


def decode_token(token: str) -> dict[str, Any]:
    """校验并解出 payload。

    这里的异常必须**分开处理**，因为过期和伪造的应对完全不同：
        ExpiredSignatureError → 让前端去刷新 token
        InvalidTokenError     → 直接踢回登录页
    """
    try:
        return jwt.decode(
            token,
            get_secret(),
            algorithms=[JWT_ALGORITHM],
            # 必须显式指定算法白名单。历史上最经典的 JWT 漏洞就是
            # 不校验算法，攻击者把 alg 改成 none 或换成对称/非对称混用，直接绕过签名。
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("token 已过期") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenError("token 无效") from exc


def describe_token(token: str) -> dict[str, Any]:
    """**仅供教学查看**：不验签，直接把 payload 解出来看。

    这就是"JWT 不是加密"的证据 —— 任何人都能读到里面的内容。
    生产代码里绝对不要用这种东西做鉴权。
    """
    return jwt.decode(token, options={"verify_signature": False})
