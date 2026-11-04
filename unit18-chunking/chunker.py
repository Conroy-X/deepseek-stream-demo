"""Unit 18 · 切块：固定长度 / 递归 / 语义，三种策略 + 同一套评测。

为什么不能"随手切"：
    切块决定了检索的**上限**。切太大，一块里混着五个主题，向量被平均成一团糊；
    切太小，一句话被砍成两半，"退款 7 天"和"无理由"分居两块，谁也召不回。

三种策略的差别不在实现难度，而在"断开的位置"：
    fixed      每 size 字切一刀，最简单，也最容易把句子砍断
    recursive  优先在段落 → 换行 → 句号处断，实在不行才硬切
    semantic   看相邻句子的相似度，话题变了才断

本模块只负责"切"，评测在 main.py 里用固定问题集跑 —— 不许凭肉眼看。

═══════════════════════════════════════════════════════════════════
全模块设计总纲（读每一段都带着这条）：

  "切"是有损操作，一切以【偏移可还原、引用可核对】为前提。

  每个 chunk 携带 (start, end) 字符偏移 → 映射回页号（page_at）→
  下游 Citation 展示"第几页的原文片段"时，必须能对上用户手里那份原文。
  所以下面反复出现这些"啰嗦"的写法，全为这条原则服务：
    · split 后把分隔符补回去（否则拼不回原文）
    · 块文本不 strip（保留首尾空白，覆盖率才能 100%）
    · 定位用 find(g, cursor) 而不是重新切（避免歧义匹配）
    · overlap 搬整个原子/句子，绝不把一句话切一半
═══════════════════════════════════════════════════════════════════
"""

from __future__ import annotations

# asyncio 本模块没直接用到（异步 embed 接口在隔壁）。
# 留着是因为语义切分未来若接 APIEmbedder，调用链会变异步
import asyncio
import re
from dataclasses import dataclass, field

# 只依赖 Unit 15 的两样东西：哈希 embedder + 余弦相似度。
# 语义切分对 embedder 的唯一要求是"同输入必同输出"（确定性），
# 换 APIEmbedder 也能跑，但阈值必须重标（见 semantic_chunks）
from embedding import LocalHashingEmbedder, cosine_similarity

# 策略注册表：/chunk 用它校验参数，自检用它遍历三种策略
STRATEGIES = ("fixed", "recursive", "semantic")

# 递归切分的分隔符优先级：段落 > 换行 > 句末 > 分号 > 空格 > 硬切
# 顺序 = 语义边界的强弱。段落边界最强（常伴随换话题），空格最弱（几乎无语义）。
# 末尾的 "" 是哨兵：降到这级说明连空格都没有，只能按尺寸硬切
SEPARATORS = ("\n\n", "\n", "。", "！", "？", "；", ". ", " ", "")


class ChunkError(ValueError):
    """参数不合法。路由层统一转 400。

    继承 ValueError 而非 Exception：
    调用方只 except ChunkError 一种；
    其他意外异常照常上冒 —— 错误分类清晰，HTTP 映射才不会一团糟。
    """


@dataclass
class Chunk:
    """切分结果的最小单元。

    start/end 是这块在【拼接后全文】里的字符偏移（pages 用 "\\n" join），
    page 由 page_at(start) 算出 —— 这三个字段就是 Citation 的定位凭证。
    """

    id: str            # "c000" 式顺序号。注意每次调用从 0 重新编号，
                       # 所以报"块数"必须连参数一起报，单看 id 没有全局意义
    index: int         # 同 id 的数字版，用于排序
    text: str          # 块原文（保留首尾空白，见 recursive_chunks）
    start: int         # 全文中的起始偏移（含）
    end: int           # 结束偏移（不含）—— 半开区间 [start, end)
    page: int          # 1 起始页号，算法与 Unit 17 的 page_of 一致
    strategy: str      # 产出该块的策略名，混用策略排查靠它
    meta: dict = field(default_factory=dict)  # 扩展字段（目前只放 chars）

    @property
    def chars(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict:
        # API 出口统一走这里，字段名就是前端/评测脚本看到的名字
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
    """按中英文句末标点切句，标点留在前一句里。

    只是 sentence_spans 的"只要文本"便捷版。
    标点留在前句而不是丢掉：句号是语义的一部分，
    且丢了它 start/end 就对不上原文了。
    """
    return [s for s, _, _ in sentence_spans(text)]


def sentence_spans(text: str) -> list[tuple[str, int, int]]:
    """返回 (句子, 起始偏移, 结束偏移)。

    偏移必须来自原文扫描，不能靠 `text.find` 反查 ——
    一旦句子被 strip 过，find 就可能定位到别的地方，页码和引用会跟着错。

    正则拆解：
      [^。！？；!?;\\n]+   连续的非句末标点字符
      [。！？；!?;]?      可选的一个句末标点
      → 天然把"句子 + 它的句号"绑一起（标点留在前句）
      → 换行也算分隔，段落空行不会混进句子
    m.start() 就是"从原文扫描"的含义：偏移从根上正确，不靠事后反查。
    """
    spans: list[tuple[str, int, int]] = []
    for m in re.finditer(r"[^。！？；!?;\n]+[。！？；!?;]?", text):
        s = m.group(0)
        if not s.strip():
            # 匹配到的全是空白（连续换行/空格）：不是句子，跳过
            continue
        # 匹配可能以空白开头（换行后的缩进）：
        # 把前导空白剥出"句子"，但偏移要相应后移，保持对齐原文
        lead = len(s) - len(s.lstrip())
        body = s.strip()
        start = m.start() + lead
        spans.append((body, start, start + len(body)))
    return spans


def page_at(offset: int, pages: list[str]) -> int:
    """字符偏移 → 页号。和 Unit 17 的 ParsedDoc.page_of 同一套算法。

    原理：pages 依次拼接（中间补一个 "\\n"），逐页累加长度，
    offset 落进哪一页的区间就算第几页：

      第 1 页 [0, len(p0)+1)      ← +1 是拼接时补的换行
      第 2 页 [len(p0)+1, ...)

    这是 Unit 17"解析阶段记住页码"的兑现时刻：
    解析时丢了页码，这里再怎么算都补不回来。
    """
    if offset <= 0 or not pages:
        return 1
    acc = 0
    for i, p in enumerate(pages):
        acc += len(p) + 1  # +1：拼接时补的换行
        if offset < acc:
            return i + 1
    # offset 超过全文总长（理论上不该发生）：兜底返回最后一页。
    # 宁可页号略可疑也不抛异常 —— 切块是批量操作，一处崩全部白算
    return max(1, len(pages))


def _validate(size: int, overlap: int) -> None:
    """切块参数的三条硬约束，每条都在防一个真实故障。"""
    if size < 20:
        # 太小的块没有上下文："3–5 个"这种块没人知道在说什么
        raise ChunkError("chunk_size 至少 20 字，再小就没有上下文了")
    if overlap < 0:
        raise ChunkError("overlap 不能为负")
    if overlap >= size:
        # 这不是"保守"，是死循环：step = size - overlap ≤ 0，
        # 滑窗永远不前进，fixed 会无限产出同一块
        raise ChunkError("overlap 必须小于 chunk_size，否则切分无法前进")


# ---------------------------------------------------------------------------
# 1. 固定长度
# ---------------------------------------------------------------------------
def fixed_chunks(text: str, size: int = 300, overlap: int = 50) -> list[tuple[str, int, int]]:
    """字符级滑动窗口：最笨但最便宜，也是另外两种策略的对照组。

    step = size - overlap：每块长 size 字，起点每次只前进 step 字，
    相邻两块因此有 overlap 字的重叠区（给跨边界信息买保险）。

    它完全不看内容 —— 句号、段落对它只是普通字符，句子随时会被腰斩。
    它的价值：零依赖、纳秒级、块长绝对均匀
    （embedding 对块长不一致的打分不公平，均匀反而是个小优点）。
    """
    _validate(size, overlap)
    step = size - overlap
    out: list[tuple[str, int, int]] = []
    i = 0
    n = len(text)
    while i < n:
        piece = text[i : i + size]
        if piece.strip():
            # 纯空白的窗口（正好卡在段落间的连续换行）跳过，
            # 但偏移仍按原窗口记录 —— 覆盖率不留洞
            out.append((piece, i, i + len(piece)))
        if i + size >= n:
            break  # 最后一窗已到文末，再滑只会产出纯重叠的冗余块
        i += step
    return out


# ---------------------------------------------------------------------------
# 2. 递归
# ---------------------------------------------------------------------------
def _is_heading(piece: str) -> bool:
    """短、无句末标点、不含逗号 —— 当小标题处理。

    启发式判定，不求准只求别误伤：
    "费用报销与差旅"命中；"住宿标准：一线城市每晚……"因为长且有逗号不命中。
    误判代价可控 —— 最多多粘/少粘一段，不会丢内容。
    """
    t = piece.strip()
    return 0 < len(t) <= 30 and not any(ch in t for ch in "。！？；，")


def _glue_headings(parts: list[str]) -> list[str]:
    """把"小节标题"粘到它后面的正文上。

    不粘的话，标题会被打包进上一块的末尾（凑长度刚好塞得下），
    于是"费用报销与差旅"孤零零挂在一堆年假条款后面 —— 检索时它谁也不代表。

    向量检索的视角：标题是这一块最强的主题信号。
    粘给正文后，整块向量被标题"锚定"到正确话题上，
    这是切块阶段能做的、代价最低的检索优化。
    """
    out: list[str] = []
    i = 0
    while i < len(parts):
        if _is_heading(parts[i]) and i + 1 < len(parts):
            # 标题 + 紧跟正文合成一个原子；连续两个标题会顺次粘下去
            out.append(parts[i] + parts[i + 1])
            i += 2
        else:
            out.append(parts[i])
            i += 1
    return out


def _atoms(text: str, size: int, seps: tuple[str, ...] = SEPARATORS) -> list[str]:
    """把文本拆成不超过 size 的原子片段，尽量在语义边界断开。

    ══ recursive 策略的灵魂：沿分隔符阶梯递归下降 ══

    "递归"不是数据结构，而是：当前级别的分隔符切不动（文本里没有），
    就带着【剩余阶梯】递归降级，直到找到第一个能下刀的位置，或降到哨兵级硬切。

      800 字全文，size=160
      → "\\n\\n" 段落级：切 4 段，其中一段 300 字超长
        → 这段带着 ("\\n","。","！"...) 递归
          → "\\n" 切 2 段，仍有 200 字超长段
            → 降到 "。" 句号级 → 全部 ≤160，收工

    每个原子都在"尽可能强的语义边界"上切出来，
    之后 recursive_chunks 只做装箱，不再碰内容。
    """
    # 递归出口 1：整段已不超过 size，它本身就是原子
    if len(text) <= size:
        return [text]
    # 递归出口 2：阶梯走完（seps 空）—— 理论上到不了，
    # 哨兵 "" 会先接手；这是防御性兜底
    if not seps:
        return [text[i : i + size] for i in range(0, len(text), size)]

    sep, rest = seps[0], seps[1:]
    if sep == "":  # 走到最后一级：硬切
        # 连空格都没有的极端文本（长 URL / 无空格英文），
        # 语义边界不存在，只能牺牲句子完整性保 size 约束
        return [text[i : i + size] for i in range(0, len(text), size)]
    if sep not in text:
        # 本级分隔符不存在 → 带剩余阶梯降级递归。
        # 注意传 rest（去掉本级），否则无限递归
        return _atoms(text, size, rest)

    # split 会丢掉分隔符，再补回去，保证切完还能拼出原文
    # （不补："".join(parts) != text，偏移/覆盖率/引用全崩）
    parts = [p + sep for p in text.split(sep)]
    if parts[-1].endswith(sep):
        # 原文末尾自带分隔符时，补回会多出一个（如 "a。b" → ["a。","b。"]），
        # 剥掉多出的那个，保持与原文严格相等
        parts[-1] = parts[-1][: -len(sep)]
    if sep == "\n\n":
        # 只在段落级做标题粘合：标题的定义（短、无标点）只在段落语境下可靠
        parts = _glue_headings(parts)

    out: list[str] = []
    for p in parts:
        if not p:
            continue
        if len(p) <= size:
            out.append(p)
            continue

        # 超长原子：如果是"标题 + 正文"，只按剩余额度拆正文，别让标题又被甩出去
        # （不特殊处理的话，_glue_headings 刚粘上的标题会被切分重新撕开）
        head, sep2, body = p.partition("\n\n")
        if sep2 and body and _is_heading(head):
            # budget 至少留 20：防止标题过长把额度吃光导致死循环式递归
            budget = max(20, size - len(head) - len(sep2))
            sub = _atoms(body, budget, rest)
            out.append(head + sep2 + sub[0])  # 标题跟正文第一段一起走
            out.extend(sub[1:])
        else:
            # 普通超长原子：降到下一级分隔符继续拆
            out.extend(_atoms(p, size, rest))
    return out


def recursive_chunks(text: str, size: int = 300, overlap: int = 50) -> list[tuple[str, int, int]]:
    """先 _atoms 拆成语义原子，再贪心装箱 —— 两层结构，各管一件事。

    第 1 层（_atoms）：在最强语义边界上把文本拆成 ≤size 的原子。
                      保证"每刀都落在段落/换行/句号上"。
    第 2 层（这里）：把小原子按顺序塞进 buf，装不下就封箱。
                      overlap 的实现也在这层 —— 见下面的"原子级回填"。
    """
    _validate(size, overlap)
    pieces = _atoms(text, size)
    if not pieces:
        return []

    grouped: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for p in pieces:
        if buf and buf_len + len(p) > size:
            # 这一件装不下了：先把当前箱封箱
            grouped.append("".join(buf))
            # ══ 原子级 overlap 回填（recursive 和 fixed 的本质差别）══
            # fixed 是"从尾巴数 N 个字符"复制过来，会把一句话切一半；
            # 这里是"从尾巴往回拿若干个完整原子"，只要总长不超 overlap。
            # 所以重叠区永远是完整的句子/段落，绝不会出现半句话。
            keep: list[str] = []
            klen = 0
            for x in reversed(buf):
                if klen + len(x) > overlap:
                    break
                keep.insert(0, x)  # 插到队首，保持原顺序
                klen += len(x)
            # 新箱子直接从这些尾部原子开始 —— 它们既是重叠区，也是新块的开头
            buf, buf_len = keep, klen
        buf.append(p)
        buf_len += len(p)
    if buf:
        # 收尾：最后一批没装满的原子单独成一箱
        grouped.append("".join(buf))

    out: list[tuple[str, int, int]] = []
    cursor = 0
    for g in grouped:
        if not g.strip():
            continue
        # 不 strip：保留原文首尾空白，覆盖率才能是 100%
        # find(g, cursor) 从游标往后找：cursor 单调前进，
        # 保证重复文本（比如"退款政策。"出现多次）不会定位到前面去
        pos = text.find(g, cursor)
        if pos < 0:
            # 理论上不会发生（grouped 都是从 text 里切的）。
            # 兜底用 cursor，宁可偏移略歪也不抛异常
            pos = cursor
        out.append((g, pos, pos + len(g)))
        # 下一个块的搜索起点：至少前进 1（防死循环），
        # 但回退 overlap 让重叠区仍可能被前面的块"占住"
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

    ══ 阈值怎么标定（实测过的三步法）══
      ① 算出全语料的相邻句相似度分布（main.py 的 _adjacent_sims 就是干这个）
      ② 找"零/低重叠带"与"同话题主带"之间的沟
      ③ 把阈值放在沟里，宁可偏低：错切无法修复，漏切有 size 兜底

    实测（49 句演示语料）：
      0.05 → 15 刀（+size 强制切 = 18 块）
      0.30 → 48 刀（逐句切，策略失效）
      阈值落在 0.05~0.06 之间的句对，内容上都是"年假天数/年假申请"这种
      同话题上下句 —— 抬到 0.06 只是多错杀，不是更准。
    """
    if size < 20:
        raise ChunkError("chunk_size 至少 20 字")
    if not 0 <= threshold <= 1:
        raise ChunkError("threshold 必须在 [0, 1]")

    spans = sentence_spans(text)
    if not spans:
        return []

    # 每句一个向量。只有"确定性"的 embedder 才能用：
    # 同一句两次算出的向量必须完全一样，否则切分结果不可复现
    e = embedder or LocalHashingEmbedder(dim=512)
    vecs = [e._embed_one(s) for s, _, _ in spans]

    # groups 里存的是"句子下标列表"，一个 group = 一个待生成的块
    groups: list[list[int]] = [[0]]
    for i in range(1, len(spans)):
        # 当前 group 若把第 i 句收进来，块长会变成多少？
        # 用偏移相减算，而不是累加 len：这样块长严格等于原文跨度的长度
        cur_len = spans[i][2] - spans[groups[-1][0]][1]
        sim = cosine_similarity(vecs[i - 1], vecs[i])
        # 两个切分条件并列：
        #   ① 语义断层：相似度低于阈值 → 这里换话题了
        #   ② 尺寸兜底：块已经超过 size → 不管语义多连贯都得断
        # ② 很重要：它保证"漏切"这种错误不会无限放大成一整篇一块
        if sim < threshold or cur_len > size:
            groups.append([i])
        else:
            groups[-1].append(i)

    out: list[tuple[str, int, int]] = []
    for gi, g in enumerate(groups):
        # overlap > 0 时，把上一组的最后一句带进来（句子级重叠，不切断句子）
        # 注意只带"一句"：语义切分本来就不靠 overlap 撑召回（边界已语义对齐），
        # 多带只会重复内容、挤占 top_k 名额
        start_idx = g[0] - 1 if (overlap > 0 and gi > 0) else g[0]
        s, end = spans[start_idx][1], spans[g[-1]][2]
        if gi == len(groups) - 1:
            # 最后一块吃到文末：句子切分不会把末尾的空白/换行算进任何句子，
            # 不补这一步，去重覆盖率永远差那么一两个字符（实测 0.999195）。
            # 内容没丢，但 Citation 的偏移是连续的，裂缝迟早会变成错位。
            end = len(text)
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
    """三种策略的统一门面：输入页列表，输出带偏移/页号的 Chunk 列表。

    设计意图：上层（路由、评测、自检）只认这一个函数，
    换策略只改一个字符串参数 —— 这样才谈得上"控制变量做对比实验"。
    """
    if strategy not in STRATEGIES:
        raise ChunkError(f"未知策略 {strategy}，可选：{list(STRATEGIES)}")

    # 多页拼成一篇再切：块可以跨页，页号在事后按 start 反推。
    # 反过来（逐页切）会制造大量人为的块边界，评测就失真了
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
                id=f"c{i:03d}",   # 每次调用重新编号：块 id 是本地产物，不是全局标识
                index=i,
                text=t,
                start=s,
                end=en,
                page=page_at(s, pages),  # 偏移 → 页号，Citation 的定位凭证
                strategy=strategy,
                meta={"chars": len(t)},
            )
        )
    return chunks


def coverage(chunks: list[Chunk], total: int) -> float:
    """**去重**覆盖率：被至少一个 chunk 覆盖到的字符占比。

    overlap 会让"字符总数"超过原文，所以不能简单用 sum(len)。
    做法：开一个与原文等长的位图，把每块的 [start, end) 区间打标，
    最后数被标了多少位 —— 重叠区域只算一次，重复重叠也不虚增。

    这个指标是切块正确性的第一道体检：
      coverage < 1.0 → 有内容没进任何块 → 检索阶段永远找不到 → 静默丢数据
    三种策略的理想值都是 1.0（semantic 曾因末尾空白差 0.0008，已修）。
    """
    if total <= 0:
        return 0.0
    mask = bytearray(total)
    for c in chunks:
        # 夹紧到 [0, total)：防御越界偏移（比如 find 兜底返回 cursor 的情况）
        for i in range(max(0, c.start), min(total, c.end)):
            mask[i] = 1
    return sum(mask) / total
