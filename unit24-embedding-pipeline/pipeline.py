"""Unit 24 · Embedding 流水线：批量、限流重试、断点续跑、成本台账。

这个单元不教"怎么算向量"（Unit 15 算过了），教的是**外部调用最容易失控的那一段**：

    一条一发          → 100 条 100 次请求，RTT 和 429 全乘以 100
    失败就整批重来     → 已经付过钱的那 90 条再付一遍
    跑一半挂了就从头   → 重跑一次等于重付一次

所以四件事必须写实：

1. **批量**     一次请求塞 N 条，不是一条一发。调用次数是限流和计费的**基本单位**。
2. **重试**     429/超时是常态，不是异常。指数退避 + 上限，且要能验证"第几次成功的"。
3. **续跑**     每条 chunk 的 status（pending/done/failed）**落库**，中断后只处理没 done 的。
4. **台账**     估算 token（事前）+ 实际 token（事后）+ 单价 → 成本，重跑不能重复计费。

provider 是本地哈希向量（离线、免费、确定），但 **usage 的记账方式按真实 API 来**：
一次请求返回一批向量 + 一个 token 数 + 一次调用。换成 OpenAI / bge-m3 时，
只要把 `HashingProvider.embed` 换成 `httpx.post("/embeddings")` 并把 tokens 换成
`response.usage.total_tokens`，流水线其余部分一行都不用改 —— 这就是 provider 抽象的意义。
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import sqlite3
import struct
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from embedding import LocalHashingEmbedder, cosine_similarity, l2_normalize

# ---------------------------------------------------------------------------
# 常量：端口、模型、单价
# ---------------------------------------------------------------------------
PORT = 8024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "emb.db")

DEFAULT_MODEL = os.getenv("EMBEDDING_MODEL", "local-hashing-512").strip()
DEFAULT_DIM = int(os.getenv("EMBEDDING_DIM", "512"))

# bge-m3 量级的单价（美元 / 百万 token）。数字不重要，**乘得对**才重要。
PRICE_PER_MTOK = 0.13

# 经验系数：BPE 词表下中文大约 1.4 字 / token，英文大约 0.8 词 / token。
# 事前估算只能用这种系数，所以它天生有 10%~20% 的误差 —— 这正是"估算 vs 实际"这条断言的来源。
EST_CJK_PER_TOKEN = 0.7
EST_ASCII_PER_WORD = 1.3
# provider 实际计数更"细"一点（模拟真实分词器与经验公式的偏差）
ACT_CJK_PER_TOKEN = 1.4
ACT_ASCII_PER_WORD = 1.25

CJK_CHAR_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
ASCII_WORD_RE = re.compile(r"[a-zA-Z0-9]+")


# ---------------------------------------------------------------------------
# 异常：可重试 / 不可重试 / 落库失败，三类必须分开
# ---------------------------------------------------------------------------
class ProviderError(Exception):
    """provider 调用失败。"""


class RateLimitError(ProviderError):
    """429 / 超时 —— **可重试**，等一会儿大概率就好了。"""


class PermanentError(ProviderError):
    """400 / 401 / 维度不符 —— **不可重试**，重试一百次也是错。"""


class StorageError(Exception):
    """向量算出来了但写不进去（锁冲突、磁盘满、约束冲突）。"""


# ---------------------------------------------------------------------------
# Token 估算与计数
# ---------------------------------------------------------------------------
def estimate_tokens(text: str) -> int:
    """事前估算：只靠字符数和经验系数，不发请求。

    为什么需要它：
        组批要在**调用之前**决定一批塞多少条，而限流通常按 token 算。
        所以必须有一个不等 provider 回答就能算出来的数，哪怕它不准。
    """
    cjk = len(CJK_CHAR_RE.findall(text))
    words = len(ASCII_WORD_RE.findall(text))
    return max(1, round(cjk * EST_CJK_PER_TOKEN + words * EST_ASCII_PER_WORD))


def count_tokens(text: str) -> int:
    """事后实际计数（模拟 provider 的 usage）。

    真实项目里这里是 `response.usage.total_tokens`，你不用自己算；
    本单元没有真实分词器，所以按同一套系数推一个"实际值"出来。
    逐条相加 == 整批的 usage，这样才能验证"批次 token = 各条之和"。
    """
    cjk = len(CJK_CHAR_RE.findall(text))
    words = len(ASCII_WORD_RE.findall(text))
    return max(1, math.ceil(cjk / ACT_CJK_PER_TOKEN + words * ACT_ASCII_PER_WORD))


def cost_of(tokens: int, price_per_mtok: float = PRICE_PER_MTOK) -> float:
    return round(tokens / 1_000_000 * price_per_mtok, 8)


# ---------------------------------------------------------------------------
# 演示语料：32 条短条款，seed 时循环取用并带编号，凑出 100 条的规模
# ---------------------------------------------------------------------------
DEMO_CLAUSES = [
    "年假：入职满一年享 5 天，满三年 10 天，满五年 15 天，需提前 3 个工作日在系统提交申请。",
    "年假结转：跨年未休完最多结转 5 天，且须在次年 3 月底前用完，逾期作废。",
    "病假：需提供二级以上医院证明，全年累计不超过 30 天，超过部分按事假处理。",
    "迟到：每月累计迟到超过 60 分钟按半天事假处理，公交故障可凭公告申诉。",
    "加班：工作日 1.5 倍调休，休息日 2 倍，法定节假日 3 倍，调休 3 个月内用完。",
    "差旅报销：出差结束后 10 个工作日内提交，附发票原件与行程单。",
    "住宿标准：一线城市每晚不超过 600 元，其他城市不超过 450 元。",
    "交通：市内交通凭票实报，跨城优先高铁二等座，机票需提前 7 天审批。",
    "招待费：单次不超过 800 元，须提前登记客户名称与事由。",
    "大额报销：超过 5000 元需部门总监二次审批，超过 2 万元需总经理签批。",
    "退款：未发货订单可随时申请全额退款，1 个工作日内处理。",
    "退货退款：已发货订单需先拒收或寄回，仓库签收后 3 个工作日内退款。",
    "虚拟商品：一经激活不支持退款，未激活可在 7 天内申请。",
    "会员服务：按已使用天数折算，剩余部分可退，原路退回。",
    "到账时间：以支付渠道为准，通常 3–5 个工作日，超时请提供支付流水号。",
    "采购审批：单笔低于 5000 元由部门负责人审批，5000 至 5 万元需比价三家。",
    "招标：单笔超过 5 万元须走招标流程，合同同时送法务审核。",
    "合同用印：须在系统发起用印申请，附合同终稿与审批记录。",
    "供应商准入：需提供营业执照、开户许可证与近一年纳税证明。",
    "供应商淘汰：连续两次交付延期超过 15 天，暂停合作并复核。",
    "数据导出：生产数据禁止下载到个人设备，确需导出须走审批并加水印。",
    "导出文件：设置 7 天有效期，到期自动失效，不得转发外部邮箱。",
    "密码策略：12 位以上含大小写与符号，每 90 天更换一次。",
    "登录失败：连续 5 次锁定账号 30 分钟，解锁需联系 IT 服务台。",
    "权限回收：离职当天回收全部账号，转岗按新岗位最小权限重新授权。",
    "发布流程：变更单 → 代码评审 → 预发验证 → 灰度 10% → 全量。",
    "发布熔断：灰度期间核心指标异常超过阈值，自动暂停发布。",
    "回滚：先确认数据库迁移可逆，再确认缓存已清理，回滚脚本须预先填写。",
    "事故复盘：P1 事故 24 小时内出初稿，5 个工作日内完成复盘会。",
    "监控：核心接口错误率超过 1% 持续 5 分钟触发告警，值班 15 分钟内响应。",
    "备份：数据库每日全量备份保留 30 天，异地机房保留 7 天。",
    "审计：敏感操作留存日志 180 天，日志本身不可修改。",
]


def demo_texts(n: int) -> list[str]:
    """造 n 条互不相同的演示 chunk（循环取条款 + 编号，保证 text 唯一）。"""
    return [f"{DEMO_CLAUSES[i % len(DEMO_CLAUSES)]}（第 {i + 1} 条）" for i in range(n)]


# ---------------------------------------------------------------------------
# SQLite：一个连接一件事，commit / rollback / close 都写明白
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,
    seq         INTEGER NOT NULL,
    text        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | done | failed
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT '',
    model       TEXT NOT NULL DEFAULT '',
    dim         INTEGER NOT NULL DEFAULT 0,
    est_tokens  INTEGER NOT NULL DEFAULT 0,
    act_tokens  INTEGER NOT NULL DEFAULT 0,
    vector      BLOB,
    updated_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_chunks_status ON chunks(status, seq);

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    model       TEXT NOT NULL,
    dim         INTEGER NOT NULL,
    batch_size  INTEGER NOT NULL,
    batches     INTEGER NOT NULL,
    calls       INTEGER NOT NULL,
    sent        INTEGER NOT NULL,
    est_tokens  INTEGER NOT NULL,
    act_tokens  INTEGER NOT NULL,
    cost_usd    REAL NOT NULL,
    done        INTEGER NOT NULL,
    failed      INTEGER NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    note        TEXT NOT NULL DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _conn() -> sqlite3.Connection:
    """开连接。**每次调用自己 commit / rollback / close**，不用全局连接。

    为什么不用一个全局连接：
        uvicorn 的同步路由跑在线程池里，多线程共用一个 sqlite3 连接会炸
        （sqlite3 默认 check_same_thread=True）。开连接很便宜，别在这里优化。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    # timeout=10：等锁最多 10 秒。默认 5 秒时，WAL 里积了大量未 checkpoint 的帧、
    # 上一次进程被 kill 掉、或者自检和 uvicorn 同时在跑，都会 5 秒内等不到写锁。
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    # journal_mode 是**写进库文件**的持久属性，设一次就够。
    # 每条连接都跑一次 PRAGMA journal_mode=WAL 会在并发下抢锁，
    # 拿到 "database is locked" 纯属自找 —— 这个坑很值得记。
    conn.execute("PRAGMA journal_mode=WAL")
    # synchronous：默认 FULL 每次 commit 都 fsync。本机实测一次 commit ≈ 97 ms，
    # 于是 batch=1 跑 100 条光落库就要 9.7 秒（见 notes.md）。
    # WAL + NORMAL 是 SQLite 官方推荐的组合：进程崩溃/断电不会损坏库，
    # 最坏只丢最后一次事务 —— 对本单元"重算几条"的代价完全可以接受。
    # 换成 FULL 只需删掉这一行，然后接受慢一个数量级。
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    conn.commit()


def reset_db() -> None:
    """清库重建。演示用 —— 真项目里这么干会丢历史台账。

    `DROP TABLE` 要的是**排他锁**，比普通写更挑时机：只要还有一个连接没释放读快照
    （比如上一轮被 Ctrl-C 掐断的进程、或者同时跑着的自检），就会直接
    `sqlite3.OperationalError: database is locked`，而且 `timeout` 对它不一定管用。
    所以这里做两件事：先 commit 收掉残留事务，再加一次有界重试。
    跑两次挂一次的单元不能交付 —— 这个重试就是为「连跑两次都过」写的。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    last: Exception | None = None
    for attempt in range(3):
        conn = _conn()
        try:
            conn.commit()  # 收掉任何残留事务，别让 DROP 撞上半开的事务
            conn.executescript("DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS runs;")
            conn.commit()
            init_db(conn)
            return
        except sqlite3.OperationalError as e:
            if "locked" not in str(e).lower():
                raise
            last = e
            time.sleep(0.05 * (attempt + 1))  # 50ms / 100ms，退避一下就够
        finally:
            conn.close()
    raise RuntimeError(f"reset_db 连续 3 次拿不到写锁：{last}")


def seed_chunks(conn: sqlite3.Connection, texts: list[str]) -> int:
    """灌数据。status 一律 pending，attempts 归零 —— 这就是"待办队列"。"""
    rows = [
        (f"c{i:03d}", i, t, "pending", estimate_tokens(t), _now())
        for i, t in enumerate(texts)
    ]
    try:
        conn.executemany(
            "INSERT INTO chunks (id, seq, text, status, est_tokens, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "text=excluded.text, status='pending', attempts=0, error='', "
            "est_tokens=excluded.est_tokens, vector=NULL, updated_at=excluded.updated_at",
            rows,
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)


def pending_rows(conn: sqlite3.Connection) -> list[dict]:
    """待办 = `status != 'done'`。**不是**"第 N 条之后的所有条"。

    这个区别就是断点续跑的全部秘密：续跑的锚点是**每条的完成事实**，
    不是源列表的 offset。offset 一旦和实际写入错位，就会漏条或重算。
    """
    rows = conn.execute(
        "SELECT id, seq, text, status, attempts FROM chunks WHERE status != 'done' ORDER BY seq"
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 组批：条数 + token 预算，双约束
# ---------------------------------------------------------------------------
def plan_batches(rows: list[dict], batch_size: int = 16, token_budget: int = 2000) -> list[list[dict]]:
    """把待办切成一堆批次。

    两个约束缺一不可：
        条数  ——  服务端一般限制单请求最多 N 条（OpenAI 是 2048，很多国产接口是 16~32）
        token ——  请求体大小和限流都按 token 算，光看条数会撞 413
    单条超过预算时**不和别人混**，独占一批 —— 拆一条文本会破坏向量语义。
    """
    if batch_size < 1:
        raise ValueError("batch_size 必须 >= 1")
    batches: list[list[dict]] = []
    cur: list[dict] = []
    cur_tokens = 0
    for r in rows:
        est = r.get("est_tokens") or estimate_tokens(r["text"])
        if cur and (len(cur) >= batch_size or cur_tokens + est > token_budget):
            batches.append(cur)
            cur, cur_tokens = [], 0
        cur.append(r)
        cur_tokens += est
    if cur:
        batches.append(cur)
    return batches


# ---------------------------------------------------------------------------
# Provider：批量接口 + 可控失败
# ---------------------------------------------------------------------------
@dataclass
class Attempts:
    count: int = 0
    delays: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class HashingProvider:
    """本地哈希 provider。

    接口形状和真实 embedding API 一致：`embed(texts) -> (vectors, tokens)`。
    多出来的 `inject_failures` 是**测试探针**：让前 N 次调用抛 429，
    这样"重试逻辑"才有东西可重试 —— 靠等真实限流来测重试是不可接受的。
    """

    name = "local-hashing"

    def __init__(self, dim: int = DEFAULT_DIM, inject_failures: int = 0, latency_ms: float = 0.0):
        self.dim = dim
        self.inject_failures = inject_failures
        self.latency_ms = latency_ms
        self._inner = LocalHashingEmbedder(dim)
        self.calls = 0        # 含失败的总尝试次数（限流按这个算）
        self.ok_calls = 0     # 成功次数
        self.sent = 0         # 成功送出的文本条数
        self.tokens = 0       # 成功调用消耗的 token

    def embed(self, texts: list[str]) -> tuple[list[list[float]], int]:
        if not texts:
            # 空批次不发请求也不算调用 —— 真实的 HTTP 客户端也不会为 [] 发请求
            return [], 0
        self.calls += 1
        if self.calls <= self.inject_failures:
            raise RateLimitError(f"429 Too Many Requests（第 {self.calls} 次调用，注入失败）")
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000.0)
        vecs = asyncio.run(self._inner.embed(texts))  # 用公开接口，不碰 _embed_one
        tokens = sum(count_tokens(t) for t in texts)
        self.ok_calls += 1
        self.sent += len(texts)
        self.tokens += tokens
        return vecs, tokens


def embed_with_retry(
    provider: HashingProvider,
    texts: list[str],
    *,
    max_retries: int = 2,
    retry_delay: float = 0.05,
) -> tuple[list[list[float]], int, Attempts]:
    """指数退避重试。总尝试次数 = 1 + max_retries。

    退避是 `delay * 2^(n-1)`，封顶 0.5s。
    本单元**故意不加抖动**，因为要断言 delays 的确切值；
    生产必须加 ±25% 随机抖动，否则一次故障恢复时所有 worker 同时重试，
    会把刚缓过来的服务再打一次（惊群 / thundering herd）。
    """
    att = Attempts()
    delay = retry_delay
    while True:
        att.count += 1
        try:
            vecs, tokens = provider.embed(texts)
            return vecs, tokens, att
        except RateLimitError as e:
            att.errors.append(str(e))
            if att.count > max_retries:
                raise
            att.delays.append(round(delay, 4))
            if delay:
                time.sleep(delay)
            delay = min(delay * 2, 0.5)
        # PermanentError 不接：401/400 重试一百次也是错，直接往上抛


# ---------------------------------------------------------------------------
# 落库：一批一个事务
# ---------------------------------------------------------------------------
def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    return list(struct.unpack(f"<{len(blob) // 4}f", blob))


def commit_batch(
    conn: sqlite3.Connection,
    rows: list[dict],
    *,
    model: str = DEFAULT_MODEL,
    fail_on: set[str] | None = None,
) -> int:
    """把这一批的向量 + status='done' **在同一个事务里**写下去。

    为什么必须同事务：
        向量表和状态表分开提交，中间崩一次，就会留下"向量写了但状态还是 pending"的孤儿，
        下次续跑会重算一遍 —— 钱白花。反过来"状态 done 但向量没写"更糟：永远查不到。

    `fail_on` 是测试探针，模拟"算完了但写库失败"（锁冲突 / 磁盘满）。
    抛出去之前 rollback，所以**同批的其他条也不能是 done** —— 这就是原子性。
    """
    if not rows:
        return 0
    now = _now()
    try:
        for r in rows:
            if fail_on and r["id"] in fail_on:
                raise StorageError(f"写入 {r['id']} 失败：database is locked（模拟）")
            conn.execute(
                "UPDATE chunks SET status='done', model=?, dim=?, est_tokens=?, act_tokens=?, "
                "vector=?, attempts=attempts+?, error='', updated_at=? WHERE id=?",
                (
                    model,
                    len(r["vec"]),
                    r["est_tokens"],
                    r["act_tokens"],
                    pack_vector(r["vec"]),
                    r.get("attempts", 1),
                    now,
                    r["id"],
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return len(rows)


def mark_failed(conn: sqlite3.Connection, ids: list[str], error: str) -> None:
    """标 failed，不是 done。下次续跑会重新捡起来（failed 也是"未完成"）。"""
    if not ids:
        return
    try:
        conn.executemany(
            "UPDATE chunks SET status='failed', attempts=attempts+1, error=?, updated_at=? WHERE id=?",
            [(error[:300], _now(), i) for i in ids],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_pipeline(
    conn: sqlite3.Connection,
    provider: HashingProvider,
    *,
    batch_size: int = 16,
    max_retries: int = 2,
    max_chunks: int = 0,
    token_budget: int = 2000,
    retry_delay: float = 0.05,
    fail_on: set[str] | None = None,
    model: str = DEFAULT_MODEL,
) -> dict:
    """跑一轮：取待办 → 组批 → 重试调用 → 逐批落库 → 记台账。

    `max_chunks > 0` 用来**模拟中途崩溃**（只跑前 N 条就退出），
    这是验证续跑最省事的办法：不用真的 kill 进程。
    """
    if max_retries < 0:
        raise ValueError("max_retries 不能为负")

    started_at = _now()
    run_id = f"run-{uuid.uuid4().hex[:8]}"
    calls_before = provider.calls

    todo = pending_rows(conn)
    if max_chunks > 0:
        todo = todo[:max_chunks]
    for r in todo:
        r["est_tokens"] = estimate_tokens(r["text"])

    batches = plan_batches(todo, batch_size, token_budget)
    report: dict = {
        "run_id": run_id,
        "model": model,
        "dim": provider.dim,
        "batch_size": batch_size,
        "batches": [],
        "calls": 0,
        "texts": sum(len(b) for b in batches),
        "sent_ok": 0,
        "est_tokens": 0,
        "act_tokens": 0,
        "cost_usd": 0.0,
        "done": 0,
        "failed": 0,
        "pending_after": 0,
        "note": "",
    }

    for i, batch in enumerate(batches):
        texts = [r["text"] for r in batch]
        item = {"index": i, "size": len(texts), "attempts": 0, "delays": [], "ok": False,
                "error": "", "tokens": 0}
        try:
            vecs, tokens, att = embed_with_retry(
                provider, texts, max_retries=max_retries, retry_delay=retry_delay
            )
            item["attempts"], item["delays"] = att.count, att.delays
            if len(vecs) != len(texts):
                # 返回条数对不上就直接当失败：**绝不能猜**哪条对应哪条
                raise PermanentError(f"返回 {len(vecs)} 条向量，应为 {len(texts)} 条")
            rows = [
                {
                    "id": r["id"],
                    "vec": v,
                    "est_tokens": r["est_tokens"],
                    "act_tokens": count_tokens(r["text"]),
                    "attempts": att.count,
                }
                for r, v in zip(batch, vecs)
            ]
            commit_batch(conn, rows, model=model, fail_on=fail_on)
            item["ok"] = True
            item["tokens"] = tokens
            report["done"] += len(texts)
            report["sent_ok"] += len(texts)
            report["act_tokens"] += tokens
            report["est_tokens"] += sum(r["est_tokens"] for r in batch)
        except (ProviderError, StorageError) as e:
            item["error"] = f"{type(e).__name__}: {e}"
            mark_failed(conn, [r["id"] for r in batch], item["error"])
            report["failed"] += len(texts)
        report["batches"].append(item)

    report["calls"] = provider.calls - calls_before
    report["cost_usd"] = cost_of(report["act_tokens"])
    left = conn.execute("SELECT COUNT(*) n FROM chunks WHERE status != 'done'").fetchone()["n"]
    report["pending_after"] = left
    if not batches:
        report["note"] = "没有待处理（全部 done）—— 续跑在这里的价值就是：0 次调用、0 元"

    finished_at = _now()
    try:
        conn.execute(
            "INSERT INTO runs (id, model, dim, batch_size, batches, calls, sent, est_tokens, "
            "act_tokens, cost_usd, done, failed, started_at, finished_at, note) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                run_id, model, provider.dim, batch_size, len(batches), report["calls"],
                report["sent_ok"], report["est_tokens"], report["act_tokens"],
                report["cost_usd"], report["done"], report["failed"], started_at, finished_at,
                report["note"],
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return report


# ---------------------------------------------------------------------------
# 读状态
# ---------------------------------------------------------------------------
def read_status(conn: sqlite3.Connection) -> dict:
    """一次给全：完成度 + 累计台账。UI 和自检都读这个。"""
    counts = {"pending": 0, "done": 0, "failed": 0}
    for row in conn.execute("SELECT status, COUNT(*) n FROM chunks GROUP BY status"):
        counts[row["status"]] = row["n"]
    total = sum(counts.values())

    agg = conn.execute(
        "SELECT COUNT(*) runs, COALESCE(SUM(calls),0) calls, COALESCE(SUM(sent),0) sent, "
        "COALESCE(SUM(act_tokens),0) act_tokens, COALESCE(SUM(est_tokens),0) est_tokens, "
        "COALESCE(SUM(cost_usd),0.0) cost FROM runs"
    ).fetchone()

    last = conn.execute("SELECT * FROM runs ORDER BY started_at DESC, rowid DESC LIMIT 1").fetchone()
    one = conn.execute(
        "SELECT model, dim FROM chunks WHERE status='done' ORDER BY seq LIMIT 1"
    ).fetchone()

    return {
        "total": total,
        "done": counts["done"],
        "pending": counts["pending"],
        "failed": counts["failed"],
        "progress": round(counts["done"] / total, 4) if total else 0.0,
        "runs": agg["runs"],
        "calls": agg["calls"],
        "sent": agg["sent"],
        "est_tokens": agg["est_tokens"],
        "act_tokens": agg["act_tokens"],
        "cost_usd": round(agg["cost"], 8),
        "model": one["model"] if one else DEFAULT_MODEL,
        "dim": one["dim"] if one else DEFAULT_DIM,
        "price_per_mtok": PRICE_PER_MTOK,
        "last_run": dict(last) if last else None,
    }


def look_vector(conn: sqlite3.Connection, chunk_id: str) -> dict | None:
    """查一条向量，顺手把范数和维度算出来 —— 归一化没做对，这里立刻露馅。"""
    row = conn.execute("SELECT * FROM chunks WHERE id=?", (chunk_id,)).fetchone()
    if row is None:
        return None
    out = {
        "id": row["id"],
        "status": row["status"],
        "model": row["model"],
        "dim": row["dim"],
        "est_tokens": row["est_tokens"],
        "act_tokens": row["act_tokens"],
        "attempts": row["attempts"],
        "error": row["error"],
        "text": row["text"][:120],
    }
    if row["vector"]:
        vec = unpack_vector(row["vector"])
        norm = math.sqrt(sum(x * x for x in vec))
        out.update(
            {
                "vec_len": len(vec),
                "norm": round(norm, 6),
                "cosine_self": round(cosine_similarity(vec, vec), 6),
                "head": [round(x, 5) for x in l2_normalize(vec)[:6]],
                "nonzero": sum(1 for x in vec if x != 0.0),
            }
        )
    return out
