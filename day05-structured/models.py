"""Day 5 · Pydantic 模型：简历抽取的目标结构（今天一切围绕这个文件展开）。

为什么用 Pydantic 而不是手写 dict 校验？
    1. 声明一次，处处受益：类型校验、默认值、别名、序列化全都有
    2. `model_json_schema()` 能直接生成 JSON Schema —— 可以整份塞给模型当"输出格式说明书"
    3. `model_validate_json()` 一步完成「解析 + 校验」，坏数据在哪一行都告诉你
    4. FastAPI 原生就是 Pydantic（Day 7 起无缝衔接）

运行自检：python models.py
"""

from __future__ import annotations

from datetime import date
from enum import Enum


# --- venv 自愈：忘记 activate 也能跑（自动切换到本目录的 .venv） ---
def _reexec_in_venv():
    import os as _os
    import sys as _sys

    if _sys.prefix != _sys.base_prefix:
        return  # 已经在 venv 里，什么都不做
    _here = _os.path.dirname(_os.path.abspath(__file__))
    _venv_py = _os.path.join(_here, ".venv", "bin", "python")
    if _os.path.exists(_venv_py):
        _os.execv(_venv_py, [_venv_py, _os.path.abspath(__file__)] + _sys.argv[1:])


_reexec_in_venv()
# --- venv 自愈 结束 ---

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Seniority(str, Enum):
    """职级枚举 —— 演示「枚举字段」如何天然约束取值范围。"""

    junior = "junior"
    mid = "mid"
    senior = "senior"
    staff = "staff"
    lead = "lead"


class WorkExperience(BaseModel):
    """一段工作经历。"""

    company: str = Field(..., min_length=1, description="公司名")
    title: str = Field(..., min_length=1, description="职位")
    start_date: str = Field(..., pattern=r"^\d{4}(-\d{2})?$", description="YYYY 或 YYYY-MM")
    end_date: str | None = Field(
        None,
        pattern=r"^\d{4}(-\d{2})?$|^(至今|present|now)$",
        description="YYYY / YYYY-MM / 至今；在校或空窗可为 null",
    )
    highlights: list[str] = Field(default_factory=list, max_length=5, description="最多 5 条亮点")


class Education(BaseModel):
    """教育经历。"""

    school: str = Field(..., min_length=1)
    degree: str = Field(..., description="本科/硕士/大专/博士 等")
    major: str | None = None
    graduation_year: int = Field(..., ge=1990, le=2035, description="毕业年份，范围校验")


class Resume(BaseModel):
    """简历抽取的最终目标：8 个字段。

    注意 model_config：
        - extra="forbid"  模型多吐字段 → 直接报错（默认是 ignore，会静默丢字段）
        - str_strip_whitespace  所有字符串自动去首尾空白
        - populate_by_name     允许用别名回填（模型有时爱用别的键名）
    """

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        str_coerce_numbers_to_str=False,
        populate_by_name=True,
    )

    name: str = Field(..., min_length=1, description="姓名")
    email: str | None = Field(None, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", description="邮箱，可空")
    phone: str | None = Field(None, description="电话，可空；不做格式强校验（号段太杂）")
    years_of_experience: float = Field(..., ge=0, le=50, description="总工作年限")
    skills: list[str] = Field(..., min_length=1, max_length=30, description="技能列表，1~30 项")
    seniority: Seniority = Field(..., description="职级，五选一")
    work_experiences: list[WorkExperience] = Field(..., min_length=1, description="工作经历，至少 1 段")
    education: list[Education] = Field(default_factory=list, description="教育经历，可为空")

    @field_validator("email", mode="before")
    @classmethod
    def empty_to_none(cls, v):
        """模型爱输出空字符串/\"无\"表示没有 —— 统一归一成 None 再校验。"""
        if v is None:
            return None
        if isinstance(v, str) and v.strip() in ("", "无", "暂无", "null", "None"):
            return None
        return v


# ---------------------------------------------------------------------------
# 让 LLM 输出这套结构的三件套（extract.py 会用到）
# ---------------------------------------------------------------------------
RESUME_JSON_INSTRUCTION = """输出要求（严格遵守）：
1. 只输出一个 JSON 对象，不要 markdown 代码块标记，不要任何解释文字
2. 顶层恰好 8 个字段：name, email, phone, years_of_experience, skills, seniority, work_experiences, education
3. 字段类型与约束：
   - name: str，姓名
   - email: str 或 null（没有就 null，不要写 "无"）
   - phone: str 或 null（同上）
   - years_of_experience: number，总工作年限（应届填 0；实习不算正式年限）
   - skills: 字符串数组，1~30 项
   - seniority: 只能是 "junior" / "mid" / "senior" / "staff" / "lead" 之一
   - work_experiences: 数组，每项 {company, title, start_date, end_date, highlights}
     · start_date/end_date 格式 "YYYY" 或 "YYYY-MM"；在职的 end_date 写 "至今"
     · highlights 是字符串数组，最多 5 条，每条一个短句
   - education: 数组（可为空 []），每项 {school, degree, major, graduation_year}
4. 只用简历中出现的信息，不要编造；没有的字段给 null / []，不要猜"""


def build_extract_messages(resume_text: str) -> list[dict[str, str]]:
    """构造抽取请求的消息数组：system 放规则，user 放简历原文。"""
    return [
        {
            "role": "system",
            "content": "你是简历信息抽取引擎。你的唯一职责是把用户给出的简历文本转换成"
            "指定 JSON 结构，不做评价、不补充信息。\n\n" + RESUME_JSON_INSTRUCTION,
        },
        {"role": "user", "content": f"简历原文：\n{resume_text}"},
    ]


if __name__ == "__main__":
    # 自检：不联网。确认 ①合法数据能过 ②非法数据会被拦 ③Schema 能生成
    good = {
        "name": "张三",
        "email": "zhangsan@example.com",
        "phone": None,
        "years_of_experience": 6.5,
        "skills": ["Python", "FastAPI", "TypeScript"],
        "seniority": "mid",
        "work_experiences": [
            {
                "company": "某公司",
                "title": "前端工程师",
                "start_date": "2019-07",
                "end_date": "至今",
                "highlights": ["从 0 搭建组件库"],
            }
        ],
        "education": [{"school": "某某大学", "degree": "本科", "major": "网络工程", "graduation_year": 2019}],
    }
    r = Resume.model_validate(good)
    print("✅ 合法数据通过：", r.name, r.seniority, f"{r.years_of_experience} 年")

    bad_cases = {
        "多吐字段": {**good, "salary_expectation": "30k"},
        "年限为负": {**good, "years_of_experience": -1},
        "seniority 非法": {**good, "seniority": "大佬"},
        "日期格式错": {**good, "work_experiences": [{**good["work_experiences"][0], "start_date": "19年7月"}]},
        "email 填了 '无'": {**good, "email": "无"},
    }
    for label, payload in bad_cases.items():
        try:
            got = Resume.model_validate(payload)
            extra_note = "（'无'被归一成 None，属预期通过）" if label == "email 填了 '无'" else ""
            print(f"✅ {label}: 通过 {extra_note} -> email={got.email!r}" if "email" in label else f"⚠️ {label}: 竟然通过了")
        except Exception as exc:  # noqa: BLE001
            first_err = str(exc).split("\n")[1] if "\n" in str(exc) else str(exc)
            print(f"🚫 {label}: 被拦下 -> {first_err[:80]}")

    schema = Resume.model_json_schema()
    import json

    print(f"\n✅ JSON Schema 生成成功：{len(json.dumps(schema, ensure_ascii=False))} 字符，")
    print("   顶层字段：", [k for k in schema["properties"]])
