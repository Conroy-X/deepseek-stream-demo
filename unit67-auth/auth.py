"""Unit 67 · Auth：认证（你是谁）与授权（你能干什么）。

核心认知：
  1. 认证 Authentication —— 确认身份（登录 / API Key / JWT）
  2. 授权 Authorization —— 确认权限（RBAC / 最小权限）
  3. 两者是两个独立步骤，HTTP 状态码也分开：
     401 = 没认证（你是谁都不知道）
     403 = 认证了但没权限（知道你是谁，但你不许干这个）
  4. JWT 三段：header.payload.signature，签名用 HMAC 防篡改 ——
     手写一遍才知道 PyJWT 帮你做了什么

本单元手写一个最小 JWT（HMAC-SHA256）+ 一个 RBAC 表，离线全可测。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


class AuthError(Exception):
    """401：认证失败（无 token / 签名错 / 过期）。"""


class ForbiddenError(Exception):
    """403：认证通过但权限不足。"""


# ---------------------------------------------------------------------------
# base64url 编解码（JWT 用的不是标准 base64）
# ---------------------------------------------------------------------------
def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ---------------------------------------------------------------------------
# 最小 JWT：HS256
# ---------------------------------------------------------------------------
def create_token(payload: dict, secret: str, ttl: int = 3600) -> str:
    """签发 token（HS256，HMAC-SHA256 签名）。

    payload 里强制带 exp（过期时间），过期是 JWT 内建的安全属性。
    """
    now = int(time.time())
    body = {**payload, "exp": now + ttl, "iat": now}
    header = {"alg": "HS256", "typ": "JWT"}
    h = b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    p = b64url_encode(json.dumps(body, separators=(",", ":")).encode())
    signing_input = f"{h}.{p}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{b64url_encode(sig)}"


def verify_token(token: str, secret: str) -> dict:
    """验证 token，返回 payload。任何一步不对都抛 AuthError。"""
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthError("token 不是三段结构")
    h, p, sig = parts

    # 1. 验签名：篡改 payload 会让签名对不上
    signing_input = f"{h}.{p}"
    expected = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    try:
        actual = b64url_decode(sig)
    except Exception as exc:  # noqa: BLE001
        raise AuthError("签名无法解码") from exc
    if not hmac.compare_digest(actual, expected):
        raise AuthError("签名不匹配（token 被篡改）")

    # 2. 验过期
    try:
        payload = json.loads(b64url_decode(p))
    except Exception as exc:  # noqa: BLE001
        raise AuthError("payload 无法解码") from exc
    if payload.get("exp", 0) <= int(time.time()):
        raise AuthError("token 已过期")
    return payload


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------
ROLES: dict[str, set[str]] = {
    "admin": {"ask", "list_users", "set_quota", "delete_user"},
    "user": {"ask"},
    "guest": {"ask"},  # guest 也能 ask，但额度低（见 main.py 的配额表）
}


def has_permission(role: str, permission: str) -> bool:
    return permission in ROLES.get(role, set())


# ---------------------------------------------------------------------------
# 认证 + 授权：两个独立步骤
# ---------------------------------------------------------------------------
def authenticate(auth_header: str | None, secret: str) -> dict:
    """第一步：认证。失败抛 AuthError（对应 401）。"""
    if not auth_header:
        raise AuthError("缺少 Authorization 头")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthError("Authorization 头格式应为 'Bearer <token>'")
    return verify_token(token, secret)


def authorize(payload: dict, permission: str) -> str:
    """第二步：授权。失败抛 ForbiddenError（对应 403）。返回 role。"""
    role = payload.get("role", "")
    if not has_permission(role, permission):
        raise ForbiddenError(f"角色 {role or '(无)'} 没有权限 {permission}")
    return role


# ---------------------------------------------------------------------------
# 独立自检
# ---------------------------------------------------------------------------
def run_selfcheck() -> int:
    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 67 · Auth 自检\n")
    # 演示密钥：真实项目从 .env 读，且要定期轮换
    SECRET = "demo-secret-change-me"

    # ---- token 基本结构 ----
    tok = create_token({"sub": "rik", "role": "user"}, SECRET, ttl=3600)
    check("token 是三段", len(tok.split(".")) == 3)
    payload = verify_token(tok, SECRET)
    check("能验回 sub", payload["sub"] == "rik")
    check("能验回 role", payload["role"] == "user")
    check("payload 带 exp", "exp" in payload)
    check("payload 带 iat", "iat" in payload)

    # ---- 篡改检测 ----
    h, p, sig = tok.split(".")
    tampered = json.loads(b64url_decode(p))
    tampered["role"] = "admin"  # 试图提权
    p2 = b64url_encode(json.dumps(tampered, separators=(",", ":")).encode())
    try:
        verify_token(f"{h}.{p2}.{sig}", SECRET)
        check("篡改 payload 被拒", False)
    except AuthError as e:
        check("篡改 payload 被拒（签名不匹配）", "签名" in str(e), str(e))

    # ---- 过期 ----
    tok2 = create_token({"sub": "x"}, SECRET, ttl=1)
    time.sleep(1.1)
    try:
        verify_token(tok2, SECRET)
        check("过期 token 被拒", False)
    except AuthError as e:
        check("过期 token 被拒", "过期" in str(e), str(e))

    # ---- 错误 secret ----
    try:
        verify_token(tok, "wrong-secret")
        check("错误 secret 被拒", False)
    except AuthError:
        check("错误 secret 被拒", True)

    # ---- 非三段 ----
    try:
        verify_token("abc.def", SECRET)
        check("非三段 token 被拒", False)
    except AuthError:
        check("非三段 token 被拒", True)

    # ---- RBAC 矩阵 ----
    check("admin 可 set_quota", has_permission("admin", "set_quota"))
    check("user 不可 set_quota", not has_permission("user", "set_quota"))
    check("user 可 ask", has_permission("user", "ask"))
    check("未知角色无任何权限", not has_permission("hacker", "ask"))

    # ---- 认证：401 场景 ----
    try:
        authenticate(None, SECRET)
        check("无 token 抛 AuthError", False)
    except AuthError:
        check("无 token 抛 AuthError", True)
    try:
        authenticate("Basic xyz", SECRET)
        check("非 Bearer 抛 AuthError", False)
    except AuthError:
        check("非 Bearer 抛 AuthError", True)

    # ---- 授权：403 场景 ----
    user_tok = create_token({"sub": "rik", "role": "user"}, SECRET)
    pl = authenticate(f"Bearer {user_tok}", SECRET)
    check("认证通过（user）", pl["sub"] == "rik")
    try:
        authorize(pl, "delete_user")
        check("user 无 delete_user 抛 ForbiddenError", False)
    except ForbiddenError:
        check("user 无 delete_user 抛 ForbiddenError", True)
    role = authorize(pl, "ask")
    check("user 有 ask 权限", role == "user")

    # ---- admin 完整链路 ----
    admin_tok = create_token({"sub": "root", "role": "admin"}, SECRET)
    apl = authenticate(f"Bearer {admin_tok}", SECRET)
    check("admin 可 delete_user", authorize(apl, "delete_user") == "admin")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
