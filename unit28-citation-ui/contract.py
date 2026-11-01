"""Unit 28 · 引用 UI 的契约层：先校验，再渲染，最后才谈跳转。

这个文件不含任何 FastAPI 代码，全是纯函数，方便单独测：

    validate_answer_payload()  一份后端响应到底能不能拿去渲染
    render_answer()            把 [n] 变成可点击角标（先转义再替换）
    jump_target()              这条引用能不能跳到 PDF 页，跳不了就降级
    RaceGuard                  「连点 [1][3][5]，后到的响应盖掉先到的」怎么防

三条规矩，顺序不能换：

    1. 不可信的 JSON 先进 validate，不进模板。缺 page、重复 id、未知 [9]、
       javascript: 协议，都是**数据问题**，让渲染层去兜就已经输了。
    2. 渲染只有一个出口 render_answer()，它第一步就是 html.escape。
    3. 跳转是**可选能力**，不是必然能力。page 缺失时必须能降级成「只看片段」，
       而不是弹一个 404 或者跳到第 1 页假装成功。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

# 版本号：引用定位的语义一变就要 +1，否则旧答案会被新 UI 误读。
LOCATOR_VERSION = 1

# 只认 1–3 位数字。和 Unit 21 一样，防止 [2026] 这种年份被当引用。
MARKER_RE = re.compile(r"\[(\d{1,3})\]")

REQUIRED_FIELDS = ("id", "chunk_id", "document_id", "title", "text", "source_url")

# 允许的协议白名单。javascript: 和 data: 是这条链上最经典的两个洞：
# 前者直接执行脚本，后者可以塞一个 text/html 的 data URL 进去。
ALLOWED_SCHEMES = ("http://", "https://", "/")

MAX_ANSWER_CHARS = 8000


@dataclass
class Citation:
    id: int
    chunk_id: str
    document_id: str
    title: str
    text: str
    source_url: str
    page: int | None = None
    snippet: str | None = None
    locator_version: int = LOCATOR_VERSION

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "title": self.title,
            "text": self.text,
            "source_url": self.source_url,
            "page": self.page,
            "snippet": self.snippet,
            "locator_version": self.locator_version,
        }


@dataclass
class AnswerPayload:
    request_id: str
    answer: str
    citations: list[Citation] = field(default_factory=list)
    incomplete: bool = False


def problem(level: str, code: str, message: str) -> dict:
    return {"level": level, "code": code, "message": message}


# ---------------------------------------------------------------------------
# 一、契约校验
# ---------------------------------------------------------------------------
def validate_answer_payload(payload: dict) -> list[dict]:
    """返回问题列表，不抛异常、不修补数据。

    分两级：
        error   —— 这份响应不能直接渲染，渲染出来一定是错的（缺字段、重复 id、坏协议）
        warning —— 能渲染，但有可疑之处（未知 [9]、有引用没人用）
    把两级混成一个 bool，前端就只剩「能不能用」一个开关，
    于是「能渲染但有个角标是坏的」这种情况无处表达，只能整页报错。
    """
    problems: list[dict] = []

    if not isinstance(payload, dict):
        return [problem("error", "BAD_PAYLOAD", "响应不是对象")]

    req = payload.get("request_id")
    if not isinstance(req, str) or not req.strip():
        problems.append(problem("error", "BAD_REQUEST_ID", "request_id 缺失或为空"))

    answer = payload.get("answer")
    if not isinstance(answer, str):
        problems.append(problem("error", "BAD_ANSWER", "answer 不是字符串"))
        answer = ""
    elif len(answer) > MAX_ANSWER_CHARS:
        problems.append(
            problem("error", "ANSWER_TOO_LONG", f"answer 超过 {MAX_ANSWER_CHARS} 字，拒绝渲染")
        )

    raw = payload.get("citations")
    if not isinstance(raw, list):
        problems.append(problem("error", "BAD_CITATIONS", "citations 不是数组"))
        raw = []

    seen: set[int] = set()
    ids: set[int] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            problems.append(problem("error", "BAD_CITATION", f"第 {i} 条引用不是对象"))
            continue

        missing = [f for f in REQUIRED_FIELDS if f not in item]
        if missing:
            problems.append(
                problem("error", "MISSING_FIELD", f"第 {i} 条引用缺字段 {','.join(missing)}")
            )
            continue

        cid = item.get("id")
        # bool 是 int 的子类，True 会被当成 1 混过去，这里必须显式排除
        if isinstance(cid, bool) or not isinstance(cid, int) or cid < 1:
            problems.append(problem("error", "BAD_ID", f"第 {i} 条引用 id 不是正整数：{cid!r}"))
            continue
        if cid in seen:
            problems.append(problem("error", "DUP_ID", f"引用 id {cid} 重复出现"))
            continue
        seen.add(cid)

        for name in ("chunk_id", "document_id", "title", "text", "source_url"):
            if not isinstance(item.get(name), str) or not item[name].strip():
                problems.append(
                    problem("error", "EMPTY_FIELD", f"引用 [{cid}] 的 {name} 为空")
                )

        page = item.get("page")
        if page is not None and (isinstance(page, bool) or not isinstance(page, int) or page < 1):
            problems.append(problem("error", "BAD_PAGE", f"引用 [{cid}] 的 page 非法：{page!r}"))

        url = item.get("source_url") or ""
        if isinstance(url, str) and not url.lower().startswith(ALLOWED_SCHEMES):
            problems.append(
                problem("error", "BAD_URL", f"引用 [{cid}] 的链接协议不在白名单：{url[:32]}")
            )

        ids.add(cid)

    # 正文里的 [n] 必须都有对应数据。未知编号不能静默丢掉 ——
    # 用户看到 [4] 去点，点下去没反应，会以为是自己点错了。
    for n in extract_markers(answer):
        if n not in ids:
            problems.append(
                problem("warning", "UNKNOWN_MARKER", f"正文引用了 [{n}]，但没有这条引用数据")
            )

    # 有数据但正文没用到：不算错，通常是 top_k 取多了，但值得记一笔
    used = set(extract_markers(answer))
    for cid in sorted(ids - used):
        problems.append(
            problem("warning", "UNUSED_CITATION", f"引用 [{cid}] 未被正文使用")
        )

    if payload.get("incomplete") is True:
        problems.append(problem("warning", "INCOMPLETE", "后端标记这份答案不完整（可能被截断）"))

    return problems


def extract_markers(answer: str) -> list[int]:
    """按出现顺序去重取编号。"""
    out: list[int] = []
    for m in MARKER_RE.finditer(answer or ""):
        n = int(m.group(1))
        if n not in out:
            out.append(n)
    return out


def has_errors(problems: list[dict]) -> bool:
    return any(p["level"] == "error" for p in problems)


def codes(problems: list[dict]) -> list[str]:
    return [p["code"] for p in problems]


# ---------------------------------------------------------------------------
# 二、渲染
# ---------------------------------------------------------------------------
def render_answer(answer: str, citations: list[Citation]) -> str:
    """把 [n] 渲染成 sup 角标。**先转义再替换**，顺序反了就是 XSS。

    和 Unit 21 的 render() 是同一条规矩，但这里多一个分支：
    编号存在 → 可点击；编号不存在 → `cite-bad`，显式标红而不是丢掉。
    """
    known = {c.id for c in citations}
    safe = html.escape(answer or "", quote=True)

    def repl(m: re.Match) -> str:
        n = int(m.group(1))
        if n in known:
            return f'<sup class="cite" data-n="{n}">[{n}]</sup>'
        return f'<sup class="cite-bad" data-n="{n}">[{n}]</sup>'

    return MARKER_RE.sub(repl, safe)


def render_citation_text(text: str, limit: int = 240) -> str:
    """引用原文片段也要转义。这里最容易漏：大家只想着转义答案，忘了转义引用。"""
    s = " ".join((text or "").split())
    if len(s) > limit:
        s = s[:limit] + "…"
    return html.escape(s, quote=True)


# ---------------------------------------------------------------------------
# 三、跳转能力：能不能跳，跳不了怎么办
# ---------------------------------------------------------------------------
def jump_target(c: Citation) -> dict:
    """返回 {status, ...}。status 只有三种，UI 必须三种都画得出来：

        ok       —— 有页码、链接合法，可以跳
        no_page  —— 有原文但没有页码（比如 .md 来源），只能展开片段
        bad_url  —— 链接协议不合法，直接禁掉，不给「复制链接」的机会
    """
    url = (c.source_url or "").strip()
    if not url.lower().startswith(ALLOWED_SCHEMES):
        return {
            "status": "bad_url",
            "citation_id": c.id,
            "reason": "链接协议不在白名单，已禁用跳转",
        }
    if not c.page:
        return {
            "status": "no_page",
            "citation_id": c.id,
            "reason": "这条来源没有页码，只能展开原文片段",
        }
    return {
        "status": "ok",
        "citation_id": c.id,
        "document_id": c.document_id,
        "page": c.page,
        "url": f"{url}#page={c.page}",
        "locator_version": c.locator_version,
    }


def locate_in_page(text: str, snippet: str) -> dict:
    """在页文本里找这条引用对应的区间，给前端画高亮用。

    找不到也不算错 —— 页内高亮失败时退化成「整页可读、片段在侧栏」，
    总比弹一个「定位失败」然后什么都不给强。
    """
    hay = text or ""
    needle = " ".join((snippet or "").split())
    if not needle:
        return {"found": False, "start": 0, "end": 0, "reason": "没有片段可用于定位"}
    idx = hay.find(needle[:80])
    if idx < 0:
        return {"found": False, "start": 0, "end": 0, "reason": "页文本里没有找到这段文字"}
    return {"found": True, "start": idx, "end": idx + min(len(needle), 80)}


class RaceGuard:
    """连点 [1][3][5] 时，只有最后一次点击的响应允许上屏。

    没有这个的话，网络抖动会让 [1] 的慢响应盖掉 [5] 的快响应，
    侧栏显示的是 [1] 的内容、角标高亮的是 [5]，而且没人能复现。
    """

    def __init__(self) -> None:
        self._seq = 0

    def begin(self) -> int:
        self._seq += 1
        return self._seq

    def accept(self, token: int) -> bool:
        return token == self._seq

    @property
    def current(self) -> int:
        return self._seq


# ---------------------------------------------------------------------------
# 四、模拟文档：没有真 PDF，也要有真页码
# ---------------------------------------------------------------------------
DOC_PAGES: dict[tuple[str, int], str] = {
    ("hr-handbook", 1): "第 1 章 总则\n\n本手册适用于全体正式员工，自入职之日起生效。",
    ("hr-handbook", 3): (
        "第 3 章 休假管理\n\n"
        "年假需提前 3 个工作日在系统提交申请，由直属主管审批。入职满一年享 5 天年假，满三年 10 天。"
    ),
    ("hr-handbook", 7): (
        "第 4 章 结转与清零\n\n"
        "跨年未休完的年假最多结转 5 天，结转部分须在次年 3 月底前用完，逾期清零。"
    ),
    ("refund-policy", 2): (
        "第 2 节 退款时效\n\n退款金额原路退回，到账时间以支付渠道为准，通常 3–5 个工作日。"
    ),
    ("refund-policy", 5): (
        "第 3 节 异常处理\n\n超过 15 个工作日仍未到账的，请提交工单并附上订单号与支付截图。"
    ),
}

DOC_TITLES = {"hr-handbook": "员工手册", "refund-policy": "退款政策"}


def page_text(document_id: str, page: int) -> str | None:
    return DOC_PAGES.get((document_id, page))


def page_count(document_id: str) -> int:
    pages = [p for (d, p) in DOC_PAGES if d == document_id]
    return max(pages) if pages else 0


# ---------------------------------------------------------------------------
# 五、固定 fixture：四种必须画得出来的情况
# ---------------------------------------------------------------------------
def _c(cid: int, doc: str, page: int | None, text: str) -> Citation:
    return Citation(
        id=cid,
        chunk_id=f"{doc}-c{cid}",
        document_id=doc,
        title=DOC_TITLES[doc],
        text=text,
        source_url=f"/docs/{doc}.pdf",
        page=page,
        snippet=text,
    )


_C1 = _c(1, "hr-handbook", 3, "年假需提前 3 个工作日在系统提交申请，由直属主管审批。")
_C2 = _c(2, "hr-handbook", 7, "跨年未休完的年假最多结转 5 天，结转部分须在次年 3 月底前用完。")
_C3 = _c(3, "refund-policy", 2, "退款金额原路退回，到账时间以支付渠道为准，通常 3–5 个工作日。")

ANSWER_NORMAL = (
    "年假需提前 3 个工作日在系统提交申请，由直属主管审批。[1]"
    "跨年未休完的年假最多结转 5 天。[2]"
    "退款通常 3–5 个工作日到账。[3]"
)

FIXTURE_CASES = ("normal", "missing_page", "unknown_marker", "malicious")


def build_fixture(case: str = "normal") -> AnswerPayload:
    """四种固定数据。它们不是「好/坏」的示例，是四种**必须画得出来**的状态。"""
    if case == "normal":
        return AnswerPayload("req_normal", ANSWER_NORMAL, [_C1, _C2, _C3])

    if case == "missing_page":
        # 有原文、没页码：能展开片段，不能跳页。UI 不能说「加载失败」，那是另一回事。
        no_page = _c(2, "hr-handbook", None, "跨年未休完的年假最多结转 5 天。")
        return AnswerPayload("req_missing_page", ANSWER_NORMAL, [_C1, no_page, _C3])

    if case == "unknown_marker":
        # 正文写了 [4]，但只有三条引用数据。必须显式标红，不能静默消失。
        answer = ANSWER_NORMAL + "具体以人事系统为准。[4]"
        return AnswerPayload("req_unknown_marker", answer, [_C1, _C2, _C3])

    if case == "malicious":
        # 两件事一起演示：答案里的 img/onerror，和引用里的 javascript: 协议。
        bad = _c(1, "hr-handbook", 3, "年假需提前 3 个工作日在系统提交申请。")
        bad.source_url = "javascript:alert(document.cookie)"
        dup = _c(1, "hr-handbook", 7, "重复 id 的引用，校验层必须拦下来。")
        answer = '<img src=x onerror=alert(1)>年假需提前 3 个工作日在系统提交申请。[1]'
        return AnswerPayload("req_malicious", answer, [bad, dup])

    raise KeyError(f"没有这个 fixture：{case}，可选 {FIXTURE_CASES}")


def payload_to_dict(p: AnswerPayload) -> dict:
    return {
        "request_id": p.request_id,
        "answer": p.answer,
        "citations": [c.to_dict() for c in p.citations],
        "incomplete": p.incomplete,
    }
