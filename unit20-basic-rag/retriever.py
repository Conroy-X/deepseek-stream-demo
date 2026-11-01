"""Unit 20 · 检索层：内存里的 16 段语料 + 余弦排序。

和 Unit 19 的区别只有一个：这里不落 SQLite。
因为本单元要讲的是「检索之后怎么生成」，存储那套已经单独验证过，
再复制一遍只是让代码更长。真上生产时，把 `Retriever.search` 换成一个
`VectorStore.search` 调用即可，接口形状是刻意对齐的。

分数含义提醒：本地哈希向量的绝对值在 0.1~0.5 之间（512 维大多是 0），
不要拿「余弦 0.8 才算相关」这种经验值来卡它。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import embedding as emb
from embedding import cosine_similarity

CORPUS = [
    {"id": "handbook:1:01", "source": "handbook.md", "page": 1,
     "text": "年假：入职满一年享 5 天，满三年 10 天，满五年 15 天。年假需提前 3 个工作日在系统提交申请，由直属主管审批。"},
    {"id": "handbook:1:02", "source": "handbook.md", "page": 1,
     "text": "跨年未休完的年假最多结转 5 天，结转部分须在次年 3 月底前用完。离职时未休年假按日工资折算补偿。"},
    {"id": "handbook:2:03", "source": "handbook.md", "page": 2,
     "text": "病假需提供二级以上医院证明，全年累计不超过 30 天。超过 30 天的部分按事假处理，不计薪。"},
    {"id": "handbook:2:04", "source": "handbook.md", "page": 2,
     "text": "迟到：每月累计迟到超过 60 分钟，按半天事假处理。因公共交通故障迟到的，凭运营方公告可申诉撤销。"},
    {"id": "handbook:3:05", "source": "handbook.md", "page": 3,
     "text": "加班：工作日加班按 1.5 倍调休，休息日 2 倍，法定节假日 3 倍。调休需在 3 个月内使用，逾期作废。"},
    {"id": "policy:1:06", "source": "policy.md", "page": 1,
     "text": "未发货订单可随时申请全额退款，1 个工作日内处理。已发货订单需先拒收或寄回，仓库签收后 3 个工作日内退款。"},
    {"id": "policy:1:07", "source": "policy.md", "page": 1,
     "text": "虚拟商品一经激活不支持退款，未激活可在 7 天内申请。会员类服务按已使用天数折算，剩余部分可退。"},
    {"id": "policy:2:08", "source": "policy.md", "page": 2,
     "text": "退款金额原路退回，到账时间以支付渠道为准，通常 3–5 个工作日。超时未到账请提供订单号与支付流水号。"},
    {"id": "ops:1:09", "source": "ops.md", "page": 1,
     "text": "差旅报销需在出差结束后 10 个工作日内提交，附发票原件与行程单。超过 5000 元的报销需部门总监二次审批。"},
    {"id": "ops:1:10", "source": "ops.md", "page": 1,
     "text": "住宿标准：一线城市每晚不超过 600 元，其他城市不超过 450 元。市内交通凭票实报，跨城优先选择高铁二等座。"},
    {"id": "ops:2:11", "source": "ops.md", "page": 2,
     "text": "单笔采购金额低于 5000 元由部门负责人审批。5000 至 5 万元需采购部复核并比价三家。超过 5 万元须走招标流程。"},
    {"id": "ops:2:12", "source": "ops.md", "page": 2,
     "text": "合同用印须在系统发起用印申请，附合同终稿与审批记录。对方盖章回传后，原件由行政归档，扫描件上传至合同库。"},
    {"id": "sec:1:13", "source": "sec.md", "page": 1,
     "text": "密码要求 12 位以上且包含大小写与符号，每 90 天更换一次。连续 5 次登录失败锁定账号 30 分钟。"},
    {"id": "sec:1:14", "source": "sec.md", "page": 1,
     "text": "所有生产数据禁止下载到个人设备。确需导出的，须走数据导出审批，导出文件加水印并设置 7 天有效期。"},
    {"id": "rel:1:15", "source": "rel.md", "page": 1,
     "text": "发布流程：提交变更单 → 代码评审通过 → 预发环境验证 → 灰度 10% → 全量。核心指标异常超过阈值自动暂停发布。"},
    {"id": "rel:1:16", "source": "rel.md", "page": 1,
     "text": "回滚后先检查数据库迁移是否可逆，再确认缓存已清理。回滚脚本须在变更单里预先填写，不允许现场编写。"},
]


@dataclass
class Chunk:
    id: str
    text: str
    source: str
    page: int
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "source": self.source,
            "page": self.page,
            "score": round(self.score, 4),
        }


class Retriever:
    """向量在构造时一次性算好 —— 语料是静态的，正文 16 次 embed 足够便宜。"""

    def __init__(self, corpus: list[dict] | None = None):
        self.corpus = corpus if corpus is not None else CORPUS
        self.embedder = emb.get_embedder()
        self._vecs = asyncio.run(self.embedder.embed([c["text"] for c in self.corpus])) if self.corpus else []

    def search(self, query: str, top_k: int = 3, min_score: float | None = None) -> list[Chunk]:
        if top_k <= 0:
            raise ValueError(f"top_k 必须 > 0，收到 {top_k}")
        qv = asyncio.run(self.embedder.embed([query]))[0]
        scored = [
            Chunk(c["id"], c["text"], c["source"], int(c.get("page", 1)), cosine_similarity(qv, v))
            for c, v in zip(self.corpus, self._vecs)
        ]
        if min_score is not None:
            scored = [c for c in scored if c.score >= min_score]
        scored.sort(key=lambda c: (-c.score, c.id))
        return scored[:top_k]
