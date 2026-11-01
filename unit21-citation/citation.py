"""Unit 21 · 引用：把答案里的 [n] 变成可校验、可点击、不可注入的东西。

三件事，只有一件是渲染：

    1. 解析   [1] / [1][2] / [1] 重复 → 有序去重的编号列表
              但 [2026] 不是引用，[123] 也不是 —— 只认 1–2 位数字
    2. 校验   越界（[9] 只有 3 条来源）、覆盖率（多少句带引用）、
              伪造（引了 [2]，但那半句在来源 2 里根本找不到）
    3. 渲染   [n] → <sup><a>…</a></sup>，**先转义再替换**，顺序反了就是 XSS

为什么"伪造引用"要单独查：
    模型最常见的编法不是瞎编编号，而是**编号对、内容错** ——
    它会引用一条真实存在的来源，然后写一句那条来源从来没说过的话。
    只校验「编号是否越界」的检查器，对这种情况一无所知。
"""

from __future__ import annotations

import difflib
import html
import re
from dataclasses import dataclass

# 只认 1–2 位数字。这条正则是防年份的：`[2026]` 在中文技术文档里是年份，
# 拿它当引用会得到一条永远越界的告警，然后所有人开始忽略告警。
CITE_RE = re.compile(r"\[(\d{1,2})\]")

# 判定「这半句在来源里找得到」用的滑窗长度。
# 太短（3~4 字）任何中文都能凑上；太长（20 字）改一个词就判伪造。6 是个折中。
NGRAM = 6
FABRICATED_BELOW = 0.5  # 支撑度低于这个就算伪造


def has_content(text: str) -> bool:
    """句子里有没有真内容 —— 只有标点的「。」不算一句，否则覆盖率分母被灌水。"""
    return bool(re.search(r"[0-9A-Za-z\u4e00-\u9fff]", text or ""))


@dataclass
class Source:
    idx: int
    text: str
    source: str = "unknown.md"
    page: int = 1

    def to_dict(self) -> dict:
        return {"idx": self.idx, "text": self.text, "source": self.source, "page": self.page}


def extract_refs(text: str) -> list[int]:
    """按出现顺序去重取编号。`[1][1]` → `[1]`，`[2][1]` → `[2, 1]`。

    保留顺序很重要：它决定了「首条引用是哪一条」，UI 上默认展开的就是它。
    """
    seen: list[int] = []
    for m in CITE_RE.finditer(text or ""):
        n = int(m.group(1))
        if n not in seen:
            seen.append(n)
    return seen


def strip_refs(text: str) -> str:
    return CITE_RE.sub("", text or "").strip()


def split_sentences(text: str) -> list[str]:
    """切句，并且**把紧跟句号的 [n] 收回上一句**。

    这是本单元最容易错的一处：中文习惯把引用标在句号**后面**
    （`……审批。[1]`），按标点切的话 `[1]` 会跑到下一句开头，
    于是「上一句没引用、下一句只有个编号」—— 覆盖率直接掉一半，伪造检测也全失效。
    """
    out: list[str] = []
    buf = ""
    i = 0
    n = len(text or "")
    while i < n:
        ch = text[i]
        buf += ch
        i += 1
        if ch in "。！？；\n":
            while True:
                m = CITE_RE.match(text, i)
                if not m:
                    break
                buf += m.group(0)
                i = m.end()
            if buf.strip():
                out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def locate(snippet: str, source_text: str, min_len: int = NGRAM) -> tuple[int, int] | None:
    """在来源里定位这段文字，返回 [start, end)。

    用 difflib 的最长公共块而不是 `source_text.find(snippet)`：
    答案常常在句尾多一个标点、或者引用了半句，精确 find 会直接失败，
    而最长公共块还能告诉你「它最接近哪一段」，这对排查比一个 None 有用得多。
    """
    s = strip_refs(snippet)
    if not s or not source_text:
        return None
    m = difflib.SequenceMatcher(None, source_text, s, autojunk=False).find_longest_match(
        0, len(source_text), 0, len(s)
    )
    if m.size < min_len:
        return None
    return (m.b, m.b + m.size)


def support_score(claim: str, source_text: str) -> float:
    """claim 的 6 字滑窗有多大比例能在来源里找到 —— 粗暴但能抓住"编号对内容错"。"""
    s = strip_refs(claim)
    if len(s) < NGRAM or not source_text:
        return 0.0
    windows = [s[i : i + NGRAM] for i in range(len(s) - NGRAM + 1)]
    hit = sum(1 for w in windows if w in source_text)
    return hit / len(windows)


def coverage(sentences: list[dict]) -> float:
    """带引用的句子 / 有内容的句子。

    分母不算空句，否则答案末尾一个换行就能把覆盖率稀释掉。
    """
    body = [s for s in sentences if has_content(s["text"])]
    if not body:
        return 0.0
    return round(sum(1 for s in body if s["refs"]) / len(body), 4)


def validate(answer: str, sources: list[Source]) -> dict:
    """一份答案的完整体检报告。UI 上每个数字都来自这里。"""
    total = len(sources)
    refs = extract_refs(answer)
    out_of_range = [n for n in refs if n < 1 or n > total]

    sentences = []
    for raw in split_sentences(answer):
        s_refs = extract_refs(raw)
        claim = strip_refs(raw)
        if not claim:
            continue
        in_range = [n for n in s_refs if 1 <= n <= total]
        support = 0.0
        if in_range:
            support = max(support_score(claim, sources[n - 1].text) for n in in_range)
        sentences.append(
            {
                "text": claim,
                "refs": s_refs,
                "support": round(support, 3),
                "cited": bool(s_refs),
                # 全部编号都越界时不判「伪造」—— 那是越界问题，别混成一个告警
                "fabricated": bool(in_range) and support < FABRICATED_BELOW,
            }
        )

    used = {n for n in refs if 1 <= n <= total}
    return {
        "answer": answer,
        "sources": total,
        "refs": refs,
        "out_of_range": out_of_range,
        "coverage": coverage(sentences),
        "sentences": sentences,
        "no_citation": [s["text"] for s in sentences if not s["cited"]],
        "fabricated": [
            {"text": s["text"], "refs": s["refs"], "support": s["support"]}
            for s in sentences
            if s["fabricated"]
        ],
        "unused_sources": [s.idx for s in sources if s.idx not in used],
        "ok": not out_of_range and not any(s["fabricated"] for s in sentences),
    }


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def render(answer: str, sources: list[Source], title: str = "参考来源") -> str:
    """把 [n] 变成可点击的 <sup>，并附来源列表。

    **顺序是 security-critical**：先 html.escape 整段答案，**再**替换 `[n]`。
    反过来的话，答案里塞一个 `<img src=x onerror=...>` 会先被浏览器解析。
    转义之后答案里不可能再有 `<`，所以后面插入的标签一定是"我们自己的"。
    """
    total = len(sources)
    safe = html.escape(answer or "", quote=True)

    def repl(m: re.Match) -> str:
        n = int(m.group(1))
        if not 1 <= n <= total:
            # 越界的不是链接，也不能静默丢掉 —— 标出来，让人看见
            return f'<span class="cite-bad" title="引用了不存在的来源 {n}">[{n}]</span>'
        # 只接受整数，所以 data-i / href 里不可能混进引号或 javascript:
        return f'<sup class="cite"><a href="#src-{n}" data-i="{n}">[{n}]</a></sup>'

    body = CITE_RE.sub(repl, safe)
    items = "".join(
        f'<li id="src-{s.idx}"><b>[{s.idx}]</b> '
        f'<span class="src-name">{html.escape(s.source, quote=True)} · 第 {s.page} 页</span>'
        f'<div class="src-text">{html.escape(s.text, quote=True)}</div></li>'
        for s in sources
    )
    return (
        f'<div class="citation-answer">{body}</div>'
        f'<div class="citation-sources"><h4>{html.escape(title)}</h4><ol>{items}</ol></div>'
    )


def highlight(text: str, start: int, end: int) -> str:
    """把来源里被引用的那一段标出来（先转义再插标签，同一条规矩）。"""
    if not (0 <= start < end <= len(text)):
        return html.escape(text, quote=True)
    return (
        html.escape(text[:start], quote=True)
        + "<mark>" + html.escape(text[start:end], quote=True) + "</mark>"
        + html.escape(text[end:], quote=True)
    )
