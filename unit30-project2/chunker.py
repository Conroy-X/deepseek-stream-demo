"""Unit 18 · 切块：固定长度 / 递归 / 语义，三种策略 + 同一套评测。

为什么不能"随手切"：
    切块决定了检索的**上限**。切太大，一块里混着五个主题，向量被平均成一团糊；
    切太小，一句话被砍成两半，"退款 7 天"和"无理由"分居两块，谁也召不回。

三种策略的差别不在实现难度，而在"断开的位置"：
    fixed      每 size 字切一刀，最简单，也最容易把句子砍断
    recursive  优先在段落 → 换行 → 句号处断，实在不行才硬切
    semantic   看相邻句子的相似度，话题变了才断

本模块只负责"切"，评测在 main.py 里用固定问题集跑 —— 不许凭肉眼看。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field

from embedding import LocalHashingEmbedder, cosine_similarity

STRATEGIES = ("fixed", "recursive", "semantic")

# 递归切分的分隔符优先级：段落 > 换行 > 句末 > 分号 > 空格 > 硬切
SEPARATORS = ("\n\n", "\n", "。", "！", "？", "；", ". ", " ", "")


class ChunkError(ValueError):
    """参数不合法。路由层统一转 400。"""


@dataclass
class Chunk:
    id: str
    index: int
    text: str
    start: int
    end: int
    page: int
    strategy: str
    meta: dict = field(default_factory=dict)

    @property
    def chars(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "index": self.index,
            "text": self.text,
            "chars": self.chars,
            "start": self.start,
            "end": self.end,
            "page": self.page,
            "strategy": self.strategy,
            "meta": self.meta,
        }


def split_sentences(text: str) -> list[str]:
    """按中英文句末标点切句，标点留在前一句里。"""
    return [s for s, _, _ in sentence_spans(text)]


def sentence_spans(text: str) -> list[tuple[str, int, int]]:
    """返回 (句子, 起始偏移, 结束偏移)。

    偏移必须来自原文扫描，不能靠 `text.find` 反查 ——
    一旦句子被 strip 过，find 就可能定位到别的地方，页码和引用会跟着错。
    """
    spans: list[tuple[str, int, int]] = []
    for m in re.finditer(r"[^。！？；!?;\n]+[。！？；!?;]?", text):
        s = m.group(0)
        if not s.strip():
            continue
        lead = len(s) - len(s.lstrip())
        body = s.strip()
        start = m.start() + lead
        spans.append((body, start, start + len(body)))
    return spans


def page_at(offset: int, pages: list[str]) -> int:
    """字符偏移 → 页号。和 Unit 17 的 ParsedDoc.page_of 同一套算法。"""
    if offset <= 0 or not pages:
        return 1
    acc = 0
    for i, p in enumerate(pages):
        acc += len(p) + 1  # +1：拼接时补的换行
        if offset < acc:
            return i + 1
    return max(1, len(pages))


def _validate(size: int, overlap: int) -> None:
    if size < 20:
        raise ChunkError("chunk_size 至少 20 字，再小就没有上下文了")
    if overlap < 0:
        raise ChunkError("overlap 不能为负")
    if overlap >= size:
        # 这不是"保守"，是死循环：每步前进 0 个字
        raise ChunkError("overlap 必须小于 chunk_size，否则切分无法前进")


# ---------------------------------------------------------------------------
# 1. 固定长度
# ---------------------------------------------------------------------------
def fixed_chunks(text: str, size: int = 300, overlap: int = 50) -> list[tuple[str, int, int]]:
    _validate(size, overlap)
    step = size - overlap
    out: list[tuple[str, int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        piece = text[i : i + size]
        if piece.strip():
            out.append((piece, i, i + len(piece)))
        if i + size >= n:
            break
        i += step
    return out


# ---------------------------------------------------------------------------
# 2. 递归
# ---------------------------------------------------------------------------
def _is_heading(piece: str) -> bool:
    """短、无句末标点、不含逗号 —— 当小标题处理。"""
    t = piece.strip()
    return 0 < len(t) <= 30 and not any(ch in t for ch in "。！？；，")


def _glue_headings(parts: list[str]) -> list[str]:
    """把"小节标题"粘到它后面的正文上。

    不粘的话，标题会被打包进上一块的末尾（凑长度刚好塞得下），
    于是"费用报销与差旅"孤零零挂在一堆年假条款后面 —— 检索时它谁也不代表。
    """
    out: list[str] = []
    i = 0
    while i < len(parts):
        if _is_heading(parts[i]) and i + 1 < len(parts):
            out.append(parts[i] + parts[i + 1])
            i += 2
        else:
            out.append(parts[i])
            i += 1
    return out


def _atoms(text: str, size: int, seps: tuple[str, ...] = SEPARATORS) -> list[str]:
    """把文本拆成不超过 size 的原子片段，尽量在语义边界断开。"""
    if len(text) <= size:
        return [text]
    if not seps:
        return [text[i : i + size] for i in range(0, len(text), size)]

    sep, rest = seps[0], seps[1:]
    if sep == "":  # 走到最后一级：硬切
        return [text[i : i + size] for i in range(0, len(text), size)]
    if sep not in text:
        return _atoms(text, size, rest)

    # split 会丢掉分隔符，再补回去，保证切完还能拼出原文
    parts = [p + sep for p in text.split(sep)]
    if parts[-1].endswith(sep):
        parts[-1] = parts[-1][: -len(sep)]
    if sep == "\n\n":
        parts = _glue_headings(parts)

    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if len(p) <= size:
            out.append(p)
            continue

        # 超长原子：如果是"标题 + 正文"，只按剩余额度拆正文，别让标题又被甩出去
        head, sep2, body = p.partition("\n\n")
        if sep2 and body and _is_heading(head):
            budget = max(20, size - len(head) - len(sep2))
            sub = _atoms(body, budget, rest)
            out.append(head + sep2 + sub[0])
            out.extend(sub[1:])
        else:
            out.extend(_atoms(p, size, rest))
    return out


def recursive_chunks(text: str, size: int = 300, overlap: int = 50) -> list[tuple[str, int, int]]:
    _validate(size, overlap)
    pieces = _atoms(text, size)
    if not pieces:
        return []

    grouped: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for p in pieces:
        if buf and buf_len + len(p) > size:
            grouped.append("".join(buf))
            keep: list[str] = []
            klen = 0
            for x in reversed(buf):
                if klen + len(x) > overlap:
                    break
                keep.insert(0, x)
                klen += len(x)
            buf, buf_len = keep, klen
        buf.append(p)
        buf_len += len(p)
    if buf:
        grouped.append("".join(buf))

    out: list[tuple[str, int, int]] = []
    cursor = 0
    for g in grouped:
        if not g.strip():
            continue
        # 不 strip：保留原文首尾空白，覆盖率才能是 100%
        pos = text.find(g, cursor)
        if pos < 0:
            pos = cursor
        out.append((g, pos, pos + len(g)))
        cursor = max(pos + 1, pos + len(g) - overlap)
    return out


# ---------------------------------------------------------------------------
# 3. 语义（相似度驱动）
# ---------------------------------------------------------------------------
def semantic_chunks(
    text: str,
    size: int = 300,
    overlap: int = 0,
    threshold: float = 0.05,
    embedder: LocalHashingEmbedder | None = None,
) -> list[tuple[str, int, int]]:
    """相邻句相似度低于 threshold 就断开。

    注意：这里用的是 Unit 15 的本地哈希向量，它只反映**字面重叠**。
    本单元语料实测：相邻句相似度中位数只有 0.096（自检会打印这个数），
    所以默认阈值取 0.05；换成真语义模型（中位数通常 0.4 以上）必须重新标定。
    阈值永远是跟模型绑定的，抄别人的 0.8 只会把每句切成一块。
    """
    if size < 20:
        raise ChunkError("chunk_size 至少 20 字")
    if not 0 <= threshold <= 1:
        raise ChunkError("threshold 必须在 [0, 1]")

    spans = sentence_spans(text)
    if not spans:
        return []

    e = embedder or LocalHashingEmbedder(dim=512)
    vecs = [e._embed_one(s) for s, _, _ in spans]

    groups: list[list[int]] = [[0]]
    for i in range(1, len(spans)):
        cur_len = spans[i][2] - spans[groups[-1][0]][1]
        sim = cosine_similarity(vecs[i - 1], vecs[i])
        if sim < threshold or cur_len > size:
            groups.append([i])
        else:
            groups[-1].append(i)

    out: list[tuple[str, int, int]] = []
    for gi, g in enumerate(groups):
        # overlap > 0 时，把上一组的最后一句带进来（句子级重叠，不切断句子）
        start_idx = g[0] - 1 if (overlap > 0 and gi > 0) else g[0]
        s, end = spans[start_idx][1], spans[g[-1]][2]
        out.append((text[s:end], s, end))
    return out


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
def chunk_document(
    pages: list[str],
    strategy: str = "recursive",
    size: int = 300,
    overlap: int = 50,
    threshold: float = 0.05,
) -> list[Chunk]:
    if strategy not in STRATEGIES:
        raise ChunkError(f"未知策略 {strategy}，可选：{list(STRATEGIES)}")

    text = "\n".join(pages)
    if not text.strip():
        return []

    if strategy == "fixed":
        raw = fixed_chunks(text, size, overlap)
    elif strategy == "recursive":
        raw = recursive_chunks(text, size, overlap)
    else:
        raw = semantic_chunks(text, size, overlap, threshold)

    chunks: list[Chunk] = []
    for i, (t, s, en) in enumerate(raw):
        chunks.append(
            Chunk(
                id=f"c{i:03d}",
                index=i,
                text=t,
                start=s,
                end=en,
                page=page_at(s, pages),
                strategy=strategy,
                meta={"chars": len(t)},
            )
        )
    return chunks


def coverage(chunks: list[Chunk], total: int) -> float:
    """**去重**覆盖率：被至少一个 chunk 覆盖到的字符占比。

    overlap 会让"字符总数"超过原文，所以不能简单用 sum(len)。
    """
    if total <= 0:
        return 0.0
    mask = bytearray(total)
    for c in chunks:
        for i in range(max(0, c.start), min(total, c.end)):
            mask[i] = 1
    return sum(mask) / total
