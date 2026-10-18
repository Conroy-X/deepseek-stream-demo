"""Unit 3 · 最小 LLM 调用示例。

先跑通这一个文件，再去看 chat.py。

运行：
    python 01_hello.py
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from openai import OpenAI

# ---------------------------------------------------------------------------
# 1. 读取配置
# ---------------------------------------------------------------------------
# load_dotenv() 会把同目录下的 .env 文件读进环境变量，密钥就不必写死在代码里。
load_dotenv()

API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip()

if not API_KEY or API_KEY.startswith("sk-你的"):
    print("❌ 没有找到 DEEPSEEK_API_KEY。")
    print("   1) 复制模板：cp .env.example .env")
    print("   2) 填入你在 https://platform.deepseek.com/api_keys 申请的 Key")
    sys.exit(1)

# ---------------------------------------------------------------------------
# 2. 创建客户端
# ---------------------------------------------------------------------------
# DeepSeek 使用 OpenAI 兼容协议，所以直接用 openai 官方 SDK，只改 base_url。
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

# ---------------------------------------------------------------------------
# 3. 发起对话
# ---------------------------------------------------------------------------
# messages 是一个有序数组，三种角色：
#   system    —— 给模型的长期设定（人设、规则、输出格式）
#   user      —— 用户的输入
#   assistant —— 模型之前的回答（多轮对话时由我们自己回填）
messages = [
    {"role": "system", "content": "你是一位耐心的 AI 应用开发导师，回答简洁，多用例子。"},
    {"role": "user", "content": "用一句话解释什么是 LLM 的 token。"},
]

print(f"[模型] {MODEL}")
print("[提问] 用一句话解释什么是 LLM 的 token。")

try:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=1.5,   # 0~2，越小越稳定；事实类问题建议 0~0.3
        max_tokens=512,    # 输出上限，练习期设小一点，避免意外烧钱
        stream=False,
    )
except Exception as exc:  # noqa: BLE001 - 第一天先看全异常类型，Unit 13 再做精细分类
    print(f"❌ 调用失败：{type(exc).__name__}: {exc}")
    sys.exit(1)

answer = resp.choices[0].message.content
print(f"[回答] {answer}")

# ---------------------------------------------------------------------------
# 4. 打印用量与成本（练习期必须养成习惯）
# ---------------------------------------------------------------------------
usage = resp.usage
# deepseek-flash 空闲时段参考价：输入 1 元 / 百万 token，输出 4 元 / 百万 token
# 高峰时段翻倍。价格会变动，以官方页面为准：
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing
cost = usage.prompt_tokens / 1_000_000 * 1.0 + usage.completion_tokens / 1_000_000 * 4.0

print(f"[用量] 输入 {usage.prompt_tokens} tokens / 输出 {usage.completion_tokens} tokens / 约 ¥{cost:.6f}")
print(f"[提示] 本次回答的结束原因：{resp.choices[0].finish_reason}")

# 小实验：把 temperature 改成 0 和 1.5 各跑一次，观察回答稳定性的变化。
