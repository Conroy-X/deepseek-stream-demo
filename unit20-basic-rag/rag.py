"""Unit 20 · RAG 的三段：检索 → 拼上下文 → 生成。

生成有两条路，共用同一个 prompt：

    extractive（默认，离线）  从检索到的句子里挑最贴近问题的两句，句末标 [n]
                              ✅ 零成本、不会编、可断言
                              ❌ 不会归纳，问「综合来看」就废

    api（配了 key）          走 OpenAI 兼容的 /chat/completions
                              ✅ 会归纳、会说人话
                              ❌ 要钱、要网、可能编

**两条路都必须先过阈值再生成**。分数低于 threshold 就不答 —— 否则模型拿到
一堆无关片段，会用它们编出一个看起来很像的答案，这是 RAG 最典型的翻车方式。

拒答话术是常量，不是让模型自由发挥。理由：拒答要可测。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from dotenv import load_dotenv
from pathlib import Path

import embedding as emb
from embedding import cosine_similarity
from retriever import Chunk, Retriever

# 固定拒答话术：可被自检断言，也可以在 UI 上单独着色
REFUSAL = "资料里没有相关内容，无法回答这个问题。"

SYSTEM_PROMPT = (
    "你是企业知识库助手。只能依据下面带编号的资料回答，不要使用资料以外的知识。"
    "资料不足时直接说无法回答，不要猜测。引用资料时在句末标注编号，例如 [1]。"
)

# .env 里可以覆盖这两个默认值；不配就用实测拐点
load_dotenv(Path(__file__).with_name(".env"))

DEFAULT_THRESHOLD = float(os.getenv("RAG_SCORE_THRESHOLD", "0.15"))
DEFAULT_TOP_K = int(os.getenv("RAG_TOP_K", "3"))
MAX_TOP_K = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS traces (
    trace_id   TEXT PRIMARY KEY,
    ts         TEXT NOT NULL,
    question   TEXT NOT NULL,
    mode       TEXT NOT NULL,
    threshold  REAL NOT NULL,
    refused    INTEGER NOT NULL,
    hits       TEXT NOT NULL,
    prompt     TEXT NOT NULL,
    answer     TEXT NOT NULL,
    usage      TEXT NOT NULL DEFAULT '{}',
    elapsed_ms REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_traces_ts ON traces(ts);
"""


class RagError(ValueError):
    """配置缺失、参数非法、上游返回异常。"""


# ---------------------------------------------------------------------------
# 拼上下文 / 拼 prompt
# ---------------------------------------------------------------------------
def split_sentences(text: str) -> list[str]:
    """按中文句末标点切句，标点留在前一句。

    不用正则 split('。') —— 那样每句都会丢掉句号，后面要拼回去，
    而拼接位置一错，Citation 定位就错。
    """
    out: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if ch in "。！？；\n":
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def build_context(hits: list[Chunk]) -> str:
    """编号从 1 开始，顺序 = 检索排名。**编号就是引用契约**，不能事后重排。"""
    return "\n".join(
        f"[{i}] ({h.source} 第 {h.page} 页) {h.text}" for i, h in enumerate(hits, 1)
    )


def build_prompt(question: str, hits: list[Chunk]) -> str:
    return f"{SYSTEM_PROMPT}\n\n资料：\n{build_context(hits)}\n\n问题：{question}\n回答："


# ---------------------------------------------------------------------------
# 生成 A：离线抽取式
# ---------------------------------------------------------------------------
def extractive_answer(
    question: str, hits: list[Chunk], max_sentences: int = 2, embedder=None
) -> tuple[str, list[dict]]:
    """从**最高分那一段**里挑 1–2 句最贴近问题的原句，句末标 [1]。

    为什么只取第一名那一段：
        试过「每个命中段各挑一句」，结果「住宿标准」的答案后面跟了一句退款条款 ——
        `住宿标准：……600 元。[1]超时未到账请提供订单号……[2]`。
        两句各自都对，拼在一起是胡说。跨段拼接需要模型做归纳，规则拼不出来。
        所以规则版只做一件事：把最相关那段的原话摘出来。

    为什么必须是原句：可验证。Unit 21 要拿答案去和来源逐字比对，
    改写过半句就再也校验不了。
    """
    if not hits:
        return REFUSAL, []
    top = hits[0]
    sents = split_sentences(top.text)
    if not sents:
        return REFUSAL, []

    e = embedder or emb.get_embedder()
    qv = asyncio.run(e.embed([question]))[0]
    vecs = asyncio.run(e.embed(sents))
    scores = [cosine_similarity(qv, v) for v in vecs]

    order = sorted(range(len(sents)), key=lambda j: (-scores[j], j))
    keep = order[:1]
    for j in order[1:]:
        if len(keep) >= max_sentences:
            break
        # 第二句要够贴近问题，否则宁可不给 —— 「多给一句」经常是多给一句噪声
        if scores[j] >= 0.6 * scores[order[0]]:
            keep.append(j)
    keep.sort()

    picked = [
        {
            "idx": 1,
            "sentence": sents[j],
            "sent_score": round(scores[j], 4),
            "hit_score": round(top.score, 4),
        }
        for j in keep
    ]
    answer = "".join(f"{sents[j]}[1]" for j in keep)
    return answer, picked


# ---------------------------------------------------------------------------
# 生成 B：OpenAI 兼容接口
# ---------------------------------------------------------------------------
def api_config() -> dict:
    """环境变量读一遍就够；缺 key 时给出可执行的提示，而不是 KeyError。"""
    key = (os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    return {
        "api_key": key,
        "base_url": (os.getenv("OPENAI_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/"),
        "model": (os.getenv("OPENAI_MODEL") or "deepseek-chat").strip(),
    }


def resolve_mode(prefer: str | None = None) -> str:
    """有 key 走 api，没 key 走 extractive —— 不让配置缺失把单元跑挂。"""
    if prefer in ("extractive", "api"):
        if prefer == "api" and not api_config()["api_key"]:
            raise RagError("要求 api 模式，但没配 DEEPSEEK_API_KEY / OPENAI_API_KEY")
        return prefer
    return "api" if api_config()["api_key"] else "extractive"


def parse_chat_response(data: dict) -> tuple[str, dict]:
    """从 OpenAI 兼容响应里取正文和 usage。

    单独抽成纯函数是为了能测：真实响应里 `choices[0].message.content`
    和 `usage.prompt_tokens` 是两条不同的取数路径，任何一个写错都要等真调一次才发现。
    """
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RagError(f"上游返回格式不对：{str(data)[:200]}") from e
    if not isinstance(text, str) or not text.strip():
        raise RagError("上游返回了空内容（可能被内容过滤或 max_tokens 太小）")
    return text.strip(), dict(data.get("usage") or {})


def chat_completion(prompt: str, timeout: float = 60.0) -> tuple[str, dict]:
    cfg = api_config()
    if not cfg["api_key"]:
        raise RagError("缺少 DEEPSEEK_API_KEY / OPENAI_API_KEY，只能用离线 extractive 后端")
    payload = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 400,
    }
    try:
        resp = httpx.post(
            f"{cfg['base_url']}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {cfg['api_key']}"},
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise RagError(f"调用模型失败：{type(e).__name__}: {e}") from e
    if resp.status_code >= 400:
        raise RagError(f"模型返回 HTTP {resp.status_code}：{resp.text[:200]}")
    return parse_chat_response(resp.json())


# ---------------------------------------------------------------------------
# trace
# ---------------------------------------------------------------------------
class TraceStore:
    """每次问答落一条。没有它你永远分不清「没检索到」和「模型答歪了」。"""

    def __init__(self, path: str = "data/traces.db"):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def save(self, rec: dict) -> str:
        trace_id = rec.get("trace_id") or uuid.uuid4().hex[:12]
        self.conn.execute(
            """INSERT INTO traces (trace_id, ts, question, mode, threshold, refused,
                                   hits, prompt, answer, usage, elapsed_ms)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trace_id,
                rec.get("ts") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                rec["question"],
                rec["mode"],
                float(rec.get("threshold", 0.0)),
                1 if rec.get("refused") else 0,
                json.dumps(rec.get("hits") or [], ensure_ascii=False),
                rec.get("prompt", ""),
                rec.get("answer", ""),
                json.dumps(rec.get("usage") or {}, ensure_ascii=False),
                float(rec.get("elapsed_ms", 0.0)),
            ),
        )
        self.conn.commit()
        return trace_id

    def get(self, trace_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM traces WHERE trace_id = ?", (trace_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM traces ORDER BY ts DESC, rowid DESC LIMIT ?", (max(1, limit),)
        ).fetchall()
        return [self._row(r) for r in rows]

    def count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM traces").fetchone()[0])

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["hits"] = json.loads(d["hits"])
        d["usage"] = json.loads(d["usage"])
        d["refused"] = bool(d["refused"])
        return d

    def close(self) -> None:
        self.conn.close()


# ---------------------------------------------------------------------------
# 编排：一次问答
# ---------------------------------------------------------------------------
@dataclass
class RagResult:
    question: str
    answer: str
    refused: bool
    mode: str
    hits: list[Chunk] = field(default_factory=list)
    prompt: str = ""
    usage: dict = field(default_factory=dict)
    trace_id: str = ""
    elapsed_ms: float = 0.0
    picked: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "answer": self.answer,
            "refused": self.refused,
            "mode": self.mode,
            "trace_id": self.trace_id,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "usage": self.usage,
            "hits": [h.to_dict() for h in self.hits],
            "prompt": self.prompt,
        }


def answer_question(
    question: str,
    *,
    retriever: Retriever,
    traces: TraceStore,
    top_k: int = DEFAULT_TOP_K,
    threshold: float = DEFAULT_THRESHOLD,
    mode: str | None = None,
) -> RagResult:
    """检索 → 判阈值 → 生成 → 落 trace。

    阈值判定放在生成之前：这是「无依据拒答」唯一可靠的实现位置。
    放到 prompt 里让模型自己判断，等于把可测的东西变成不可测。
    """
    q = (question or "").strip()
    if not q:
        raise RagError("问题不能为空")
    if not 1 <= top_k <= MAX_TOP_K:
        raise RagError(f"top_k 必须在 1..{MAX_TOP_K} 之间，收到 {top_k}")

    t0 = time.perf_counter()
    use_mode = resolve_mode(mode)
    hits = retriever.search(q, top_k=top_k)
    prompt = build_prompt(q, hits) if hits else ""
    top_score = hits[0].score if hits else 0.0

    if not hits or top_score < threshold:
        elapsed = (time.perf_counter() - t0) * 1000
        # trace 里保留 hits：拒答也要能复盘「当时检索到了什么、分数多低」
        res = RagResult(q, REFUSAL, True, use_mode, hits, prompt, {}, "", elapsed)
        res.trace_id = traces.save(res.to_dict() | {"threshold": threshold})
        return res

    if use_mode == "api":
        answer, usage = chat_completion(prompt)
        picked: list[dict] = []
    else:
        answer, picked = extractive_answer(q, hits)
        # 离线也记一个 usage：字符数当 token 的粗估，比留空有用
        usage = {"backend": "extractive", "prompt_chars": len(prompt), "answer_chars": len(answer)}

    elapsed = (time.perf_counter() - t0) * 1000
    res = RagResult(q, answer, False, use_mode, hits, prompt, usage, "", elapsed, picked)
    res.trace_id = traces.save(res.to_dict() | {"threshold": threshold})
    return res
