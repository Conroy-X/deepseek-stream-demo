"""Unit 62 · Redis：缓存层抽象（Memory / Redis 双后端）。

本单元学三件事，都围绕一个核心认知：**LLM 调用又贵又慢，能缓存就缓存**。

  1. 响应缓存：相同 prompt → 相同 key → 命中缓存直接返回，不调模型、不花钱
  2. 会话存储：session 数据放 Redis（带 TTL），服务重启不丢、过期自动清
  3. 缓存命中统计：hit / miss 是评价缓存价值的唯一指标

关键设计：缓存层是一个抽象，Memory 后端离线可测（自检用），Redis 后端真机可测
（本机 127.0.0.1:6379 已装 Redis 8）。上层代码不关心后端是谁。
"""

from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


def llm_cache_key(model: str, messages: list[dict], temperature: float) -> str:
    """给一次 LLM 调用算一个确定性 key。

    规则：
      - key 必须覆盖影响输出的全部参数（model / messages / temperature）
      - messages 顺序有意义，不能排序
      - 用 sha256 避免把整段 prompt 塞进 key（Redis key 太长会拖慢内存）
    """
    payload = json.dumps(
        {"model": model, "messages": messages, "temperature": temperature},
        ensure_ascii=False,
        sort_keys=False,  # messages 顺序不能动
    )
    return "llm:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class CacheError(RuntimeError):
    """缓存后端不可用或参数非法。"""


# ---------------------------------------------------------------------------
# 抽象 + 两个实现
# ---------------------------------------------------------------------------
class Cache(ABC):
    """统一接口。上层只认 get/set/delete/clear/stats。"""

    @abstractmethod
    def get(self, key: str) -> str | None: ...

    @abstractmethod
    def set(self, key: str, value: str, ttl: int) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def stats(self) -> dict: ...


class MemoryCache(Cache):
    """离线后端：自检用它，永远可用。"""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, float]] = {}
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> str | None:
        if key not in self._data:
            self._misses += 1
            return None
        value, expire_at = self._data[key]
        if expire_at and time.monotonic() < expire_at:
            self._hits += 1
            return value
        # 过期：删掉并算 miss
        del self._data[key]
        self._misses += 1
        return None

    def set(self, key: str, value: str, ttl: int) -> None:
        if ttl <= 0:
            raise CacheError(f"ttl 必须 > 0，收到 {ttl}")
        self._data[key] = (value, time.monotonic() + ttl)

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "backend": "memory",
            "size": len(self._data),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total else 0.0,
        }


class RedisCache(Cache):
    """真机后端：连本机 Redis。连不上抛 CacheError，调用方决定降级还是失败。"""

    def __init__(self, url: str = "redis://127.0.0.1:6379/0") -> None:
        try:
            import redis  # 延迟导入：离线自检不装 redis 也能跑 MemoryCache
        except ImportError as exc:
            raise CacheError("未安装 redis 包：uv pip install redis") from exc
        try:
            self._r = redis.Redis.from_url(url, socket_connect_timeout=2, decode_responses=True)
            self._r.ping()
        except Exception as exc:  # noqa: BLE001
            raise CacheError(f"Redis 不可用（{url}）：{exc}") from exc
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> str | None:
        value = self._r.get(key)
        if value is None:
            self._misses += 1
            return None
        self._hits += 1
        return value

    def set(self, key: str, value: str, ttl: int) -> None:
        if ttl <= 0:
            raise CacheError(f"ttl 必须 > 0，收到 {ttl}")
        self._r.setex(key, ttl, value)

    def delete(self, key: str) -> None:
        self._r.delete(key)

    def clear(self) -> None:
        self._r.flushdb()

    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "backend": "redis",
            "size": self._r.dbsize(),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / total, 4) if total else 0.0,
        }


def get_cache(prefer: str = "auto") -> Cache:
    """工厂：auto 时优先 Redis，不可用退回 Memory。"""
    if prefer == "memory":
        return MemoryCache()
    if prefer == "redis":
        return RedisCache()
    try:
        return RedisCache()
    except CacheError:
        return MemoryCache()


# ---------------------------------------------------------------------------
# 会话存储：一个 dict 序列化成 JSON 塞进缓存（带 TTL）
# ---------------------------------------------------------------------------
@dataclass
class Session:
    user_id: str
    history: list[dict]


def save_session(cache: Cache, sid: str, session: Session, ttl: int = 3600) -> None:
    cache.set(f"session:{sid}", json.dumps(session.__dict__, ensure_ascii=False), ttl)


def load_session(cache: Cache, sid: str) -> Session | None:
    raw = cache.get(f"session:{sid}")
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return Session(user_id=data["user_id"], history=data["history"])
    except (json.JSONDecodeError, KeyError):
        return None


# ---------------------------------------------------------------------------
# 独立自检入口（离线）
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

    print("Unit 62 · Redis 缓存 自检\n")

    # ---- llm_cache_key ----
    k1 = llm_cache_key("deepseek-flash", [{"role": "user", "content": "你好"}], 0.0)
    k2 = llm_cache_key("deepseek-flash", [{"role": "user", "content": "你好"}], 0.0)
    k3 = llm_cache_key("deepseek-flash", [{"role": "user", "content": "你好"}], 0.7)
    k4 = llm_cache_key("deepseek-flash", [{"role": "user", "content": "你好啊"}], 0.0)
    k5 = llm_cache_key("deepseek-v4-pro", [{"role": "user", "content": "你好"}], 0.0)
    check("相同输入 key 相同", k1 == k2)
    check("temperature 不同 key 不同", k1 != k3)
    check("内容不同 key 不同", k1 != k4)
    check("模型不同 key 不同", k1 != k5)
    check("key 带前缀 llm:", k1.startswith("llm:"))
    check("key 长度固定 36", len(k1) == 36, str(len(k1)))

    # 顺序敏感
    k_order1 = llm_cache_key("m", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}], 0.0)
    k_order2 = llm_cache_key("m", [{"role": "user", "content": "b"}, {"role": "assistant", "content": "a"}], 0.0)
    check("messages 顺序敏感", k_order1 != k_order2)

    # ---- MemoryCache 基本读写 ----
    c = MemoryCache()
    check("miss 返回 None", c.get("x") is None)
    c.set("x", "v", ttl=60)
    check("set 后能 get", c.get("x") == "v")
    c.delete("x")
    check("delete 后 miss", c.get("x") is None)

    # ---- TTL 过期 ----
    c.set("short", "v", ttl=1)
    check("未过期能读到", c.get("short") == "v")
    time.sleep(1.1)
    check("过期后 miss", c.get("short") is None)

    # ---- ttl 校验 ----
    try:
        c.set("bad", "v", ttl=0)
        check("ttl=0 抛错", False)
    except CacheError:
        check("ttl=0 抛错", True)

    # ---- 命中统计 ----
    c2 = MemoryCache()
    c2.get("nope")           # miss
    c2.set("hit", "1", 60)
    c2.get("hit")            # hit
    c2.get("hit")            # hit
    st = c2.stats()
    check("misses=1", st["misses"] == 1, str(st))
    check("hits=2", st["hits"] == 2, str(st))
    check("hit_rate=2/3", abs(st["hit_rate"] - 2 / 3) < 1e-3, str(st["hit_rate"]))
    check("backend=memory", st["backend"] == "memory")

    # ---- 模拟一次「缓存省钱」闭环 ----
    class FakeLLM:
        def __init__(self) -> None:
            self.calls = 0

        def generate(self, q: str) -> str:
            self.calls += 1
            return f"回答:{q}"

    llm = FakeLLM()
    cache = MemoryCache()

    def ask(q: str) -> str:
        key = llm_cache_key("fake", [{"role": "user", "content": q}], 0.0)
        hit = cache.get(key)
        if hit is not None:
            return hit
        answer = llm.generate(q)
        cache.set(key, answer, ttl=300)
        return answer

    a1 = ask("什么是缓存")
    a2 = ask("什么是缓存")
    a3 = ask("什么是缓存")
    check("三次同问答案一致", a1 == a2 == a3)
    check("模型只被调了一次（省两次）", llm.calls == 1, f"实际 {llm.calls}")

    # ---- 会话存取 ----
    s = Session(user_id="u1", history=[{"role": "user", "content": "hi"}])
    save_session(cache, "sid-1", s)
    loaded = load_session(cache, "sid-1")
    check("会话能存能取", loaded is not None and loaded.user_id == "u1" and loaded.history[0]["content"] == "hi")
    check("不存在的会话返回 None", load_session(cache, "sid-404") is None)

    # ---- 工厂：auto 降级 ----
    cc = get_cache("auto")
    check("get_cache(auto) 返回可用后端", isinstance(cc, Cache))
    check("auto 后端可读写", (cc.set("t", "1", 30), cc.get("t"))[1] == "1")
    cc.delete("t")

    # ---- Redis 真机（可选，不装 redis 包或 Redis 未起则跳过不算失败）----
    try:
        rc = RedisCache("redis://127.0.0.1:6379/0")
        rc.set("selfcheck", "ok", 30)
        check("Redis set/get", rc.get("selfcheck") == "ok")
        rc.delete("selfcheck")
        check("Redis delete", rc.get("selfcheck") is None)
        check("Redis stats", rc.stats()["backend"] == "redis")
        rc.close() if hasattr(rc, "close") else None
    except CacheError as e:
        print(f"  [SKIP] Redis 真机不可用：{e}")

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    return 1 if failed else 0


if __name__ == "__main__":
    import sys

    sys.exit(run_selfcheck())
