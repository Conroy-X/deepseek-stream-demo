"""Unit 16 · pgvector —— 把向量存进 Postgres，用 SQL 做检索。

Unit 15 里向量还在内存里，进程一停就没了。
这一单元把它落到数据库：让"相似"变成一条可以索引、可以过滤、可以事务的 SQL。

════════════════════════════════════════════════════════════════════
【整体架构】一个文件干四件事：

    文本 ──embed──▶ 向量(512维) ──▶ PostgreSQL(pgvector 扩展)
                                        │
                        ┌───────────────┼────────────────┐
                     INSERT入库      <=> 余弦距离      HNSW/IVFFlat
                     (/insert)       Top-K检索(/search)  索引(/index)

    ① /init    建扩展 + 建表（幂等，重复调用不报错）
    ② /insert  文本 → 向量 → 入库（md5 去重）
    ③ /search  Top-K 相似检索（可开关索引、可看执行计划）
    ④ /index   切换 HNSW / IVFFlat / 无索引（为 bench.py 压测做准备）

【为什么这单元重要】这是 RAG 的存储层地基：
    内存版(Unit15) → 数据库版(Unit16) → 文档解析(17) → 切块(18) → 真RAG(20)
    后面所有 RAG 单元检索用的都是这里这套 "embedding + <=> + 索引" 的组合。

【核心心智模型】pgvector 本质就三件事：
    1. vector(N) 列类型 —— 定长浮点数组，存进普通 Postgres 表
    2. 距离算符      —— <=> 余弦 / <-> L2 / <#> 内积，SQL 里直接写
    3. ANN 索引      —— HNSW(图) / IVFFlat(聚类)，拿召回率换速度

启动：
    uv run uvicorn main:app --reload --port 8016
    打开 http://127.0.0.1:8016/

自检（不启服务，直接验一遍关键路径）：
    uv run python main.py

压测（造 1k/1万/5万 数据，对比无索引 / HNSW / IVFFlat）：
    uv run python bench.py
"""

# from __future__ import annotations：让类型注解支持 `dict | None` 这种
# PEP 604 写法在老版本 Python（<3.10）下不报错——注解全部按字符串延迟求值。
# 本项目 Python 3.12 其实不需要，但写上是零成本的习惯。
from __future__ import annotations

import asyncio   # 只给 selfcheck 用：_run() 里 asyncio.run() 跑异步函数
import json      # meta 字段序列化成 JSON 字符串，再 CAST 进 jsonb 列
import os        # 读环境变量 DATABASE_URL / EMBEDDING_DIM
import time      # perf_counter 计时，测检索耗时（比 time.time() 精度高）
from contextlib import asynccontextmanager   # 把普通生成器函数变成 lifespan 异步上下文
from pathlib import Path     # 拼文件路径：.env、static/index.html 都相对本文件定位

from dotenv import load_dotenv            # 把 .env 文件内容灌进 os.environ
from fastapi import FastAPI, HTTPException   # Web 框架 + 手动抛 4xx 错误
from fastapi.middleware.cors import CORSMiddleware  # 允许浏览器跨域调接口
from fastapi.responses import FileResponse   # 直接回文件（首页静态页）
from pydantic import BaseModel, Field        # 请求体模型：自动校验 + 自动生成 OpenAPI 文档
from sqlalchemy import create_engine, text   # text() 是"参数化 SQL"，防注入 + 绑定参数

# 复用 Unit 15 的 embedder（同一条接口：await embedder.embed([text]) -> list[list[float]]）
# 这样两个单元的向量空间一致，检索结果可以直接对照。
from embedding import get_embedder

# .env 必须显式加载：uvicorn 从别的目录启动时，工作目录里没有 .env。
# Path(__file__).with_name(".env") 锚定"本文件旁边"，不管从哪启动都能找到。
load_dotenv(Path(__file__).with_name(".env"))

# ── 数据库连接串 ──────────────────────────────────────────────
# 本机 PG 17 走 Unix socket + peer 认证（用户 rik），和 Unit 11 用的是同一个库。
#   · Unix socket：不走 TCP，本机进程间直连，没有端口、没有网络开销
#   · peer 认证  ：PG 说"操作系统用户是 rik，我就当你是 PG 用户 rik"，免密码
# 换成 TCP 就写 postgresql+psycopg://user:pass@127.0.0.1:5432/ai_journey
# 前缀 postgresql+psycopg 指定驱动是 psycopg3（不是老的 psycopg2）
DEFAULT_URL = "postgresql+psycopg://rik@/ai_journey?host=/var/run/postgresql"
DATABASE_URL = os.getenv("DATABASE_URL", DEFAULT_URL).strip()

# 表名固定，方便 EXPLAIN 时对照（压测脚本和 notes.md 都写死这个名字）
TABLE = "chunks"

# 表的 vector(N) 与 embedder 的输出维度必须一致，否则 /insert 会直接报类型错误。
# ⚠ 换 embedding 模型 = 换维度 = 必须重建表（vector 列的维度是建表时定死的，
#   不是运行时自适应的），所有旧向量也要重算——所以这一步要在换模型前想清楚。
EMBED_DIM = int(os.getenv("EMBEDDING_DIM", "512"))

# SQLAlchemy 引擎 = 连接池 + 方言适配。
#   future=True     ：启用 2.0 风格 API（本文件全部用的是 2.0 风格）
#   pool_pre_ping=True：每次从池里拿连接前先发一个轻量探活，防止
#                       "MySQL server has gone away" 同款的 PG 断连残留——
#                       长时间空闲后第一次请求拿到死连接的经典坑。
engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)

# 模块级单例：一个进程只建一个 embedder（模型加载一次，后面复用）
embedder = get_embedder()

# 启动时维度对账：embedder 自报的维度 vs 表定义的维度。
# 不一致不拦着启动（可能只是 .env 改了还没重建表），但必须大声警告，
# 否则错误要到第一次 /insert 才爆，而且报错信息很难联想到是维度问题。
_warn_dim = getattr(embedder, "dim", EMBED_DIM)
if _warn_dim != EMBED_DIM:
    print(
        f"[warn] embedder 维度 {_warn_dim} 与表定义 vector({EMBED_DIM}) 不一致："
        "改模型后必须同步改 EMBEDDING_DIM 并重建表"
    )


# ── 请求体模型 ────────────────────────────────────────────────
class DocIn(BaseModel):
    """POST /insert 的请求体。Pydantic 自动做类型校验：
    text 缺失/为空 → FastAPI 直接回 422，根本进不了函数体。"""
    text: str = Field(min_length=1)      # min_length=1 挡掉空串入库
    meta: dict | None = None             # 任意 JSON 元数据（来源、页码等），可省


class SearchIn(BaseModel):
    """POST /search 的请求体。Field 的 ge/le 是数值范围校验：
    top_k=0 或 top_k=1000 都会被 422 挡下，不用手写 if。"""
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    use_index: bool = True  # 关掉它就能对比"全表扫"的耗时
    explain: bool = False  # 要执行计划（EXPLAIN ANALYZE 的原始输出）


def _vec_literal(vec: list[float]) -> str:
    """把 Python list 编成 pgvector 的文本字面量 '[0.1,0.2,...]'。

    为什么不能直接把 list 塞进参数？
        psycopg 会把 list 适配成 PostgreSQL 数组（ARRAY[float8]），
        而 vector 列不接受数组 —— 报 "column emb is of type vector but
        expression is of type double precision[]"。
    两种解法：
        (a) 转字符串 + CAST(:e AS vector)      ← 本文件用这个，零额外依赖
        (b) pgvector 包注册的类型适配器         ← 更优雅，但多一层隐式转换

    实现细节：
        repr(float(x)) 保证每个数是合法的 Python/PG 浮点字面量
        （-inf、科学计数法 1e-05 也能正确输出）；float(x) 先转一遍，
        防止 numpy.float32 之类混进来 repr 出 "np.float32(0.1)" 这种脏货。
    """
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 生命周期钩子：yield 前在启动时跑一次，yield 后在退出时跑一次。

    这里只做"启动体检"：SELECT 1 是数据库界的 ping。
    注意连接失败只打印不 raise —— 服务照样起，页面能开，
    调 /insert 时才真正报 500。好处：至少能打开 /docs 看接口列表排查问题；
    坏处：坏状态下服务是"带病运行"的。教学项目选前者，生产建议 fail-fast。
    """
    try:
        with engine.connect() as c:       # connect() 借一个连接，with 退出自动还池
            c.execute(text("SELECT 1"))   # 最便宜的连通性探针
    except Exception as e:
        print("数据库连不上：%s" % str(e)[:120])
        print("检查 .env 里的 DATABASE_URL，或确认 PostgreSQL 已启动")
    yield
    # yield 之后没有清理代码：engine/embedder 进程退出时自动回收，无需手动关


app = FastAPI(title="Unit 16 · pgvector", lifespan=lifespan)
# CORS 全开：本机前端（file:// 或别的端口）直接调这个 API，不加会报跨域错误。
# 教学项目允许 *，生产环境必须收敛到具体域名。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def index():
    """首页：直接回 static/index.html，一个自带表单的调试页。"""
    return FileResponse(Path(__file__).with_name("static") / "index.html")


# ---------------------------------------------------------------- 1. 建表
@app.post("/init")
def init_table():
    """建扩展 + 建表，幂等。

    为什么要 IF NOT EXISTS：不加的话第二次调用直接报
    "relation already exists" / "extension already exists"，接口不能重复调用。
    幂等的价值：前端页面加载时调一次、自检调一次、压测调一次，谁都不用
    关心"表是不是已经建过"。

    engine.begin() = 借连接 + 自动开事务 + 退出时 commit（异常则 rollback）。
    三条 DDL 放一个事务里：要么全成，要么全不成，不会出现"表建了、索引没建"的中间态。
    """
    with engine.begin() as c:
        # pgvector 是"扩展"不是内置类型，每个库要启用一次。
        # CREATE EXTENSION 需要超级用户或库属主权限——所以用本机 peer 认证的 rik 没问题。
        c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        c.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE} (
                    -- bigserial：bigint 自增主键，插入时自动分配，不用管 id
                    id         bigserial PRIMARY KEY,
                    -- 原文存全文：向量检索命中的是这个字段，不是向量本身
                    text       text NOT NULL,
                    -- jsonb：二进制 JSON，可建索引可查询（json 不能）；整个单元其实还没用到它，
                    -- 但 RAG 后续单元要靠它存 doc_id/chunk_id/页码，先把坑占上
                    meta       jsonb,
                    -- 向量列。vector(512) 定维：插 513 维直接报错，
                    -- 这是数据库层最后的维度防线（应用层 /insert 已经先挡了一道）
                    emb        vector({EMBED_DIM}) NOT NULL,
                    -- timestamptz：带时区时间戳，永远存 UTC，按会话时区显示，别用无时区的 timestamp
                    created_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
        )
        # 去重靠这条：同一条文本只存一行。
        # 为什么用 md5(text) 不直接 UNIQUE(text)：
        #   ① PostgreSQL 的 btree 索引单行上限约 2704 字节，长文本直接建 UNIQUE 会报错
        #   ② md5 是定长 32 字节，索引又小又快
        #   ③ md5 碰撞概率在文本去重场景可以忽略
        # 注意：去重键是"文本完全一致"，两条差一个字的文本是两行——语义去重不归这层管。
        c.execute(
            text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {TABLE}_text_md5 "
                f"ON {TABLE} (md5(text))"
            )
        )
    return {"ok": True, "table": TABLE, "dim": EMBED_DIM}


# ---------------------------------------------------------------- 2. 入库
@app.post("/insert")
async def insert_doc(doc: DocIn):
    """文本 → 向量 → 入库。

    重复插入同一条文本不会炸：靠 md5(text) 唯一索引 + ON CONFLICT DO NOTHING，
    返回值里 reused=True 表示"这行原来就有"，id 指向已有行。

    async def 的原因：await embedder.embed(...) 是真的异步（可能调远程 API）。
    在 FastAPI 里 async 函数跑在事件循环上，不会像同步 def 那样被丢进线程池。
    """
    # embed() 接收批量接口：传 [text] 取 [0] 拿单条。接口形状不变，以后批量插入零改造。
    vec = (await embedder.embed([doc.text]))[0]
    # 应用层的维度防线：在写库前就拦住，报 400 + 人话，
    # 比让 PostgreSQL 报一串类型错误好排查得多。
    if len(vec) != EMBED_DIM:
        raise HTTPException(
            400, f"向量维度 {len(vec)} 与表定义 {EMBED_DIM} 不一致，检查 EMBEDDING_DIM"
        )

    payload = _vec_literal(vec)   # list → '[0.1,0.2,...]' 文本字面量
    # ensure_ascii=False：中文直接存 UTF-8 字符而不是 \\uXXXX 转义，库里可读
    meta_json = json.dumps(doc.meta, ensure_ascii=False) if doc.meta is not None else None

    with engine.begin() as c:     # 事务：INSERT 和后面的补救 SELECT 同进同退
        row = c.execute(
            text(
                f"""
                INSERT INTO {TABLE} (text, meta, emb)
                -- 三处 CAST：参数都是文本，靠 CAST 告诉 PG 目标类型
                --   :m → jsonb   :e → vector（见 _vec_literal 的注释）
                VALUES (:t, CAST(:m AS jsonb), CAST(:e AS vector))
                -- ON CONFLICT 的精髓：撞上 md5 唯一索引时不报错、不更新，安静跳过
                -- （比"先 SELECT 查存在再 INSERT"好：① 少一次往返 ② 没有并发窗口——
                --   两个请求同时插同一文本，先查后插的写法会双双判定"不存在"然后撞车）
                ON CONFLICT (md5(text)) DO NOTHING
                -- RETURNING id：插入成功时把自增 id 随 INSERT 带回来，省一次 SELECT
                RETURNING id
                """
            ),
            {"t": doc.text, "m": meta_json, "e": payload},
        ).first()
        if row:
            # 插入了新行
            return {"ok": True, "id": row[0], "reused": False, "dim": len(vec)}
        # 冲突了（row 为 None）—— DO NOTHING 不给 id，只能反查已有行。
        # 反查条件 md5(text) = md5(:t) 走的正是那条唯一索引，点查极快。
        existed = c.execute(
            text(f"SELECT id FROM {TABLE} WHERE md5(text) = md5(:t)"), {"t": doc.text}
        ).scalar_one()
    return {"ok": True, "id": existed, "reused": True, "dim": len(vec)}


# ---------------------------------------------------------------- 3. 检索
@app.post("/search")
async def search(q: SearchIn):
    """Top-K 检索。整个单元的核心接口。

    为什么 ORDER BY 用 `emb <=> :qv` 而不是 `ORDER BY score DESC`：
        前者是"按索引的有序距离取前 K 个"，HNSW 能直接沿图走；
        后者要先把所有行的距离算出来、再整体排序 —— 等于自愿放弃索引。
        （ planner 认的是 ORDER BY emb <=> 常量 这个表达式形状，
           score 是 SELECT 里的别名，它认不出来。）
    为什么用 1 - 距离当分数：向量已 L2 归一化，余弦距离 = 1 - 余弦相似度，
        且二者单调反向，取前 K 的顺序完全一致。
        <=> 返回"距离"（越小越像），人习惯"分数"（越大越好），1- 一下就翻过来。
    """
    # 查询向量和入库向量必须出自同一个模型——不同模型的向量空间不通用，
    # 拿 A 模型的查询向量去 B 模型的库里搜，结果是垃圾。
    qv = _vec_literal((await embedder.embed([q.query]))[0])

    # SQL 拼成字符串是因为它要在普通查询和 EXPLAIN 两处复用，保证两条语句完全一致。
    # <=> 是 pgvector 的余弦距离算符：0 = 同向，1 = 垂直，2 = 反向。
    # CAST(:qv AS vector)：参数是文本，CAST 一次，ORDER BY 和 SELECT 两处共用。
    sql_txt = f"""
        SELECT id, text, meta,
               1 - (emb <=> CAST(:qv AS vector)) AS score
        FROM {TABLE}
        ORDER BY emb <=> CAST(:qv AS vector)
        LIMIT :k
    """

    t0 = time.perf_counter()   # perf_counter 是单调时钟，不受系统对时影响
    if q.use_index:
        # 正常路径：connect()（自动提交模式），让 planner 自己决定走不走索引。
        with engine.connect() as c:
            rows = c.execute(text(sql_txt), {"qv": qv, "k": q.top_k}).mappings().all()
            # mappings() → 每行是 dict（RowMapping），按列名取值，比下标可读
            plan = (
                c.execute(
                    # EXPLAIN (ANALYZE, BUFFERS)：真跑一遍并报告
                    #   ANALYZE → 给出实际耗时/行数（不是估算）
                    #   BUFFERS → 读了多少页缓存/磁盘（判断是否走索引的关键证据）
                    text("EXPLAIN (ANALYZE, BUFFERS) " + sql_txt),
                    {"qv": qv, "k": q.top_k},
                )
                .scalars()   # 每行 EXPLAIN 输出是一个单列字符串，scalars() 直接取值
                .all()
                if q.explain
                else None
            )
    else:
        # 对照路径：人为禁用所有索引访问方式，逼 planner 全表扫。
        # SET LOCAL 只在当前事务内生效 —— 所以必须和执行语句在同一个 begin() 里。
        #（如果用 SET 不带 LOCAL，会把整个会话的 planner 设置改掉，连接还回池子后
        #  别的请求就遭殃了——这是"连接池 + 会话级状态"的经典事故。）
        with engine.begin() as c:
            c.execute(text("SET LOCAL enable_indexscan = off"))    # 禁普通索引扫
            c.execute(text("SET LOCAL enable_bitmapscan = off"))   # 禁位图索引扫
            rows = c.execute(text(sql_txt), {"qv": qv, "k": q.top_k}).mappings().all()
            plan = (
                c.execute(
                    text("EXPLAIN (ANALYZE, BUFFERS) " + sql_txt),
                    {"qv": qv, "k": q.top_k},
                )
                .scalars()
                .all()
                if q.explain
                else None
            )
    took_ms = (time.perf_counter() - t0) * 1000

    results = [
        {
            "id": r["id"],
            "text": r["text"],
            # round(…, 6)：浮点距离在第 6 位小数后全是噪声，砍掉省带宽也好读
            "score": round(float(r["score"]), 6),
            "meta": r["meta"],   # jsonb 列 psycopg 自动反序列化成 dict
        }
        for r in rows
    ]
    out = {"results": results, "took_ms": round(took_ms, 3), "use_index": q.use_index}
    if plan:
        out["plan"] = [str(x) for x in plan]
    return out


# ---------------------------------------------------------------- 4. 统计
@app.get("/stats")
def stats():
    """表大小、行数、索引 —— 用来填 notes.md 的实测表。

    这里的查询全走 PostgreSQL 系统目录（catalog）：
    pg_indexes 是"当前库里所有索引"的视图—— introspection（自省）能力，
    不需要额外权限，任何能连上库的会话都能查。
    """
    with engine.connect() as c:
        # count(*) 全表数一遍。行数千万级时会很慢，但教学数据量下无所谓；
        # 真要精确行数又怕慢，可以用 pg_class.reltuples 的估算值（ANALYZE 后才准）。
        rows = c.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one()
        # pg_total_relation_size = 表本体 + 全部索引 + TOAST（大字段溢出存储）
        # pg_size_pretty 把字节数格式化成 "123 MB" 这种人话
        size = c.execute(
            text(f"SELECT pg_size_pretty(pg_total_relation_size('{TABLE}'))")
        ).scalar_one()
        idx = c.execute(
            text(
                """
                SELECT indexname,
                       -- pg_relation_size(索引名::regclass)：只算索引本体大小
                       -- ::regclass 把文本名字转成 OID，函数才认
                       pg_size_pretty(pg_relation_size(indexname::regclass)) AS size
                FROM pg_indexes
                WHERE tablename = :t
                ORDER BY 1      -- ORDER BY 1 = 按第 1 列排序
                """
            ),
            {"t": TABLE},
        ).mappings().all()
    return {
        "rows": rows,
        "table_size": size,
        "indexes": [{"name": r["indexname"], "size": r["size"]} for r in idx],
    }


# ---------------------------------------------------------------- 5. 索引
@app.post("/index")
def create_index(kind: str = "hnsw"):
    """按 kind 建索引：hnsw / ivfflat / none。

    两个关键的建索引前提：
      · IVFFlat 要靠已有数据做 k-means 聚类 —— 空表建出来是**废索引**，
        之后即使灌满数据也不会自动重新聚类（必须 REINDEX）。
        所以顺序只能是：先灌数据，再建 IVFFlat。
      · HNSW 不需要先有数据，空表也能建，插新数据会增量维护图。

    策略是"同一时刻只有一个向量索引"：换类型 = 先 DROP 旧的再建新的。
    除了方便对比，还避免 planner 在两个 ANN 索引之间摇摆（它并不真的知道
    哪个 HNSW/IVFFlat 更好，多索引只会让计划不稳定、实验变量不干净）。
    """
    kind = kind.strip().lower()   # 容错：'HNSW ' / 'IVFFLAT' 都接受
    with engine.begin() as c:
        rows = c.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one()
        # 只保留一个向量索引，方便对比（同时也是为了避免 planner 在两个索引间摇摆）
        c.execute(text("DROP INDEX IF EXISTS chunks_emb_hnsw"))
        c.execute(text("DROP INDEX IF EXISTS chunks_emb_ivf"))

        if kind == "none":
            # "none" = 只删不建，回到纯全表扫状态，压测的对照组
            return {"ok": True, "kind": "none", "rows": rows, "dropped": True}

        if kind == "hnsw":
            # HNSW = Hierarchical Navigable Small World，分层可导航小世界图。
            # 原理直觉：多层"跳表式"的近邻图——顶层稀疏（长边，快速接近目标），
            # 底层稠密（短边，精确收敛）。查询从顶层出发贪心下降，O(log N) 量级。
            #   vector_cosine_ops ：算子类，声明"这个索引服务 <=> 余弦距离"——
            #                       索引和查询算符必须匹配，用 <-> 查询就走不了这个索引
            #   m=16              ：每个节点在每层最多 16 条边。↑ 召回率和内存、
            #                       建索引时间都涨。默认 16，常用 8~48
            #   ef_construction=64：建图时每个节点的候选邻居池大小。越大图越精，
            #                       建得越慢。默认 64，常用 32~128
            # 查询侧还有个 ef_search（会话级 SET hnsw.ef_search = N）控制运行时
            # 候选集，默认 40——本文件没动它，bench.py 里可以调。
            c.execute(
                text(
                    f"""
                    CREATE INDEX chunks_emb_hnsw ON {TABLE}
                    USING hnsw (emb vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64)
                    """
                )
            )
            return {
                "ok": True,
                "kind": "hnsw",
                "rows": rows,
                "note": "m=16 每层 16 条边，ef_construction=64 建图时的候选集大小",
            }

        if kind == "ivfflat":
            # IVFFlat = Inverted File with Flat 压缩：把向量空间切成 lists 个桶
            # （k-means 聚类），查询时只扫离查询向量最近的 nprobe 个桶。
            # 快的前提：目标真在扫的那几个桶里 → 桶划分必须合理 → 必须先有数据。
            if rows < 100:
                # 小表保护：聚类样本不足时建出的质心没有代表性，
                # 召回率会烂到没法看，而且这个烂是静默的——不拦就是给自己埋雷。
                return {
                    "ok": False,
                    "kind": "ivfflat",
                    "rows": rows,
                    "error": f"只有 {rows} 行，建 IVFFlat 等于白建（聚类样本不够）——先灌数据",
                }
            # 经验规则：lists ≈ rows/1000（1M 以下），太少=每个桶太大，太多=查询要扫太多桶
            # 夹在 [1, 1000]：rows < 1000 时至少给 1 个桶（虽然此时基本没用），
            # 超过 100 万行也先封顶 1000，避免拍脑袋出天文数字
            lists = max(1, min(1000, rows // 1000))
            c.execute(
                text(
                    f"""
                    CREATE INDEX chunks_emb_ivf ON {TABLE}
                    USING ivfflat (emb vector_cosine_ops)
                    WITH (lists = {lists})
                    """
                )
            )
            return {
                "ok": True,
                "kind": "ivfflat",
                "rows": rows,
                "lists": lists,
                "note": "灌完数据再建；后续大量新增后要 REINDEX 重新聚类",
            }

        raise HTTPException(400, f"未知索引类型：{kind}（可选 hnsw / ivfflat / none）")


# ---------------------------------------------------------------- 自检
def _run(coro):
    """selfcheck 是同步流程，但 /insert、/search 是 async 的——
    这里给协程一个"现跑现等"的入口。每个 check 里独立 event loop，互不干扰。
    （FastAPI 正式跑的时候由 uvicorn 的 loop 驱动，不走这条路径。）"""
    return asyncio.run(coro)


def selfcheck() -> int:
    """不启服务，直接跑关键路径。15 项，全过说明这一单元跑通了。

    设计思路：不是单测（pytest 是另一回事），是"冒烟自检"——
    ① 按依赖顺序验：连接 → 扩展 → 表 → 插入 → 检索 → 索引
    ② 每项都有明确的"过"标准（数值断言，不是'不报错就算过'）
    ③ 失败不中断，跑完全部再汇总——一次看到所有坏点
    返回值当退出码：0 全过 / 1 有挂，shell 里 && 链条能直接感知失败。
    """
    ok = bad = 0

    def check(name, fn):
        """微框架：跑 fn()，正常返回算过，抛异常算挂。
        返回 bool 给后续检查用（比如 #14 依赖 #13 是否建成了索引）。"""
        nonlocal ok, bad
        try:
            detail = fn()
            print(ok + bad + 1, name, "✓", detail)
            ok += 1
            return True
        except Exception as e:  # noqa: BLE001
            # 故意裸吞所有异常：自检的语义就是"异常=失败"，
            # str(e)[:160] 截断防止 traceback 淹没输出
            print(ok + bad + 1, name, "✗", str(e)[:160])
            bad += 1
            return False

    print("=" * 78)
    print("Unit 16 · pgvector 自检")
    print("=" * 78)

    # 1 ── 最底层前提：库能不能连
    check("连上数据库", lambda: engine.connect().close() or DATABASE_URL.split("@")[-1])
    # 小技巧：connect().close() 返回 None（假值），or 接着算后半段——
    # 拿连接串 @ 后面的部分当"细节"显示，同时连接失败本身会抛异常进 except

    # 2 ── 扩展在不在：没有 vector 类型，后面全部免谈
    def ext():
        with engine.connect() as c:
            # pg_extension 是系统目录，记着本库启用了哪些扩展
            v = c.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            ).scalar_one_or_none()   # 没启用时返回 None 而不是抛 NoResultFound
        if not v:
            raise RuntimeError("vector 扩展未启用")
        return "pgvector " + str(v)

    check("vector 扩展已启用", ext)

    # 3 ── 幂等性专门验两连发：第二次调用是幂等实现最容易翻车的地方
    def init_twice():
        r1 = init_table()
        r2 = init_table()
        if not (r1["ok"] and r2["ok"]):
            raise RuntimeError("init 返回不 ok")
        return f"表 {TABLE}，重复调用不报错"

    check("/init 建表且幂等", init_twice)

    # 4 ── 维度不是"我说 512 就 512"，直接问数据库系统目录要答案
    def dim():
        with engine.connect() as c:
            # pg_attribute 存每列的元信息；atttypmod 对 vector 类型存的就是维度 N
            # attrelid = 'chunks'::regclass 把表名转 OID 去匹配
            d = c.execute(
                text(
                    "SELECT atttypmod FROM pg_attribute "
                    "WHERE attrelid = 'chunks'::regclass AND attname = 'emb'"
                )
            ).scalar_one()
        if d != EMBED_DIM:
            raise RuntimeError(f"表里是 {d} 维，期望 {EMBED_DIM}")
        return f"vector({d})"

    check("表列维度 = EMBED_DIM", dim)

    # 5 ── 真插一条。这是后面所有检查的数据来源
    doc = {"text": "pgvector 把向量存进 Postgres，用 <=> 算余弦距离", "meta": {"src": "selfcheck"}}
    first = _run(insert_doc(DocIn(**doc)))

    def ins():
        if not first["id"]:
            raise RuntimeError(str(first))
        return f"id={first['id']} reused={first['reused']}"

    check("/insert 入库返回 id", ins)

    # 6 ── 去重闭环：再插一遍，必须 reused=True 且行数不变
    def dup():
        before = stats()["rows"]
        r = _run(insert_doc(DocIn(**doc)))
        after = stats()["rows"]
        if not r["reused"]:
            raise RuntimeError("repeat insert 未被识别为重复")
        if after != before:
            raise RuntimeError(f"行数变了：{before} → {after}")
        return f"复用 id={r['id']}，行数仍 {after}"

    check("重复插入复用同一行", dup)

    # 7 ── 归一化是余弦检索正确性的前提，必须在库这一侧验证真的成立
    # （而不是信 embedder 的自说自话）
    def norm():
        with engine.connect() as c:
            # unnest(emb::real[])：vector 不能直接 unnest，先 CAST 成 real[]
            # 变成每维一行的列，再 sqrt(sum(x²)) 算 L2 模长
            r = c.execute(
                text(f"SELECT sqrt(sum(x*x)) FROM (SELECT unnest(emb::real[]) AS x FROM {TABLE} WHERE id = :i) s"),
                {"i": first["id"]},
            ).scalar_one()
        if abs(float(r) - 1.0) > 1e-3:
            raise RuntimeError(f"模长 {r}，期望 1")
        return f"|v| = {float(r):.6f}"

    check("入库向量已 L2 归一化", norm)

    # 8 ── 端到端正确性锚点：自己查自己，score 必须 = 1.0
    #（归一化向量的余弦相似度 = 点积 = 自身模长积 = 1）
    # 如果这条挂了，说明 embed/入库/查询某条链路被改坏了——它是最灵敏的回归探针。
    def self_sim():
        r = _run(search(SearchIn(query=doc["text"], top_k=1)))
        s = r["results"][0]["score"]
        if abs(s - 1.0) > 1e-4:
            raise RuntimeError(f"自相似 {s}，期望 1")
        return f"score = {s}"

    check("同一文本检索自己 = 1.0", self_sim)

    # 9 ── 反向验证：无关文本分数必须低。
    # 只有"高分对"没有"低分错"，检索器等于没验。
    # ⚠ 这是字面基线：菜谱和 pgvector 文档字面差异极大，纯语义相近的场景它测不了。
    def irrelevant():
        r = _run(search(SearchIn(query="红烧肉的做法与糖色调配", top_k=3)))
        top = r["results"][0]
        if top["score"] > 0.3:
            raise RuntimeError(f"无关查询最高分 {top['score']} 偏高")
        return f"最高分 {top['score']}（字面基线，语义相近它也看不出来）"

    check("无关文本分数 < 0.3", irrelevant)

    # 10 ── 接口契约：条数 ≤ top_k 且严格降序（下游 UI/重排序都依赖这个约定）
    def topk():
        r = _run(search(SearchIn(query="向量检索", top_k=3)))
        scores = [x["score"] for x in r["results"]]
        if len(scores) > 3:
            raise RuntimeError("返回超过 top_k")
        if scores != sorted(scores, reverse=True):
            raise RuntimeError(f"未按分数降序：{scores}")
        return f"{len(scores)} 条，降序 {scores}"

    check("Top-K 条数与降序", topk)

    # 11 ── 索引开关只该影响速度，不该影响结果顺序
    #（严格说 HNSW 是近似索引，大数据量下顺序可能有小差异；
    #   本单元数据量小、走的是精确路径，顺序应当一致——不一致就是索引配置坏了。）
    def same_result():
        a = _run(search(SearchIn(query="向量检索", top_k=3, use_index=True)))
        b = _run(search(SearchIn(query="向量检索", top_k=3, use_index=False)))
        ida = [x["id"] for x in a["results"]]
        idb = [x["id"] for x in b["results"]]
        if ida != idb:
            raise RuntimeError(f"两条路径结果不同：{ida} vs {idb}")
        return f"顺序一致 {ida}，耗时 {a['took_ms']}ms vs {b['took_ms']}ms"

    check("use_index 开关不改结果", same_result)

    # 12 ── /stats 不是摆设：它报告的数字必须和库的真实状态一致
    def stats_ok():
        s = stats()
        with engine.connect() as c:
            n = c.execute(text(f"SELECT count(*) FROM {TABLE}")).scalar_one()
        if s["rows"] != n:
            raise RuntimeError(f"stats 行数 {s['rows']} ≠ 实际 {n}")
        return f"{s['rows']} 行，{s['table_size']}，索引 {len(s['indexes'])} 个"

    check("/stats 行数与表一致", stats_ok)

    # 13 ── HNSW 能建（同时把索引切到 HNSW，供下一项验执行计划）
    def hnsw():
        r = create_index("hnsw")
        if not r["ok"]:
            raise RuntimeError(str(r))
        return "chunks_emb_hnsw 已建"

    has_hnsw = check("建 HNSW 索引", hnsw)

    # 14 ── 索引"建成"不等于"被用上"，执行计划说了算。
    # 坑：小表时 planner 会觉得全表扫更快，主动放弃索引 → 必须强制它走索引来看。
    # 注意这里是 SET（会话级）不是 SET LOCAL——connect() 是自动提交模式，
    # 每条语句独立事务，SET LOCAL 活不到下一条；且连接用完即还池，不会污染别人。
    def plan_uses_index():
        if not has_hnsw:
            raise RuntimeError("上一步索引没建成")
        with engine.connect() as c:
            c.execute(text("SET enable_seqscan = off"))   # 禁全表扫，逼 planner 用索引
            rows = c.execute(
                text(
                    f"EXPLAIN SELECT id FROM {TABLE} ORDER BY emb <=> CAST(:q AS vector) LIMIT 5"
                ),
                {"q": _vec_literal([0.1] * EMBED_DIM)},
            ).scalars().all()
        joined = " ".join(rows)
        if "Index Scan" not in joined:
            raise RuntimeError("计划里没有 Index Scan：" + joined[:120])
        return "plan 命中 Index Scan using chunks_emb_hnsw"

    check("EXPLAIN 能命中索引", plan_uses_index)

    # 15 ── 验 IVFFlat 的小表保护逻辑本身（防御性代码也要有测试）
    # 表里行数 <100 时应拒绝；如果数据已灌过 100+，则应该真的建成
    def ivf_guard():
        r = create_index("ivfflat")
        if r["ok"] and stats()["rows"] < 100:
            raise RuntimeError("行数不足却建成了 IVFFlat")
        return r.get("error") or f"lists={r.get('lists')}"

    check("IVFFlat 小表拒绝建（防白建）", ivf_guard)

    print("-" * 78)
    print(f"通过 {ok} / 失败 {bad}")
    if bad == 0:
        print("全过。下一步：uv run python bench.py 填 notes.md 的实测表")
    print("=" * 78)
    return 1 if bad else 0


# 直接 `uv run python main.py` 时跑自检而不是启服务（服务用 uvicorn 启）。
# raise SystemExit：把 selfcheck 的返回值变成进程退出码，0=全过 1=有挂，
# 这样 shell 里 `python main.py && bench.py` 的链条能正确感知失败。
if __name__ == "__main__":
    raise SystemExit(selfcheck())
