"""Day 3 · 把 LLM 调用封装成可复用模块。

为什么单独抽一个文件？
    Day 4 的 Prompt 实验、Day 5 的结构化输出、Day 6 的流式接口、Day 13 的
    超时重试，都会复用这里的调用逻辑。先封装好，后面只改这一个文件。

运行自检：
    python llm.py
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Iterable

from dotenv import load_dotenv
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)

load_dotenv()

# deepseek-flash 空闲时段参考价（元 / 百万 token）；高峰时段翻倍。
# 官方价格：https://api-docs.deepseek.com/zh-cn/quick_start/pricing
PRICE_INPUT_PER_M = 1.0
PRICE_OUTPUT_PER_M = 4.0


class LLMConfigError(RuntimeError):
    """配置缺失（例如没有 API Key）。"""


class LLMCallError(RuntimeError):
    """调用失败，已翻译成人类可读的提示。"""


@dataclass
class Usage:
    """累计用量统计。"""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def cost_cny(self) -> float:
        return (
            self.prompt_tokens / 1_000_000 * PRICE_INPUT_PER_M
            + self.completion_tokens / 1_000_000 * PRICE_OUTPUT_PER_M
        )

    def add(self, prompt_tokens: int, completion_tokens: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens

    def summary(self) -> str:
        return (
            f"本会话 {self.calls} 轮 · 输入 {self.prompt_tokens} tokens · "
            f"输出 {self.completion_tokens} tokens · 合计 {self.total_tokens} · "
            f"约 ¥{self.cost_cny:.4f}"
        )


@dataclass
class Message:
    """一条对话消息。role 只能是 system / user / assistant。"""

    role: str
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class LLMClient:
    """DeepSeek（OpenAI 兼容协议）的轻量封装。

    之所以用 OpenAI SDK 而不是手写 requests：
      - 官方 SDK 会处理重试、超时、类型解析；
      - 换服务商（通义 / Kimi / 智谱 / 本地 vLLM）时只需要改 base_url 和 model。
    """

    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int = 1024,
        timeout: float = 60.0,
    ) -> None:
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()

        if not api_key or api_key.startswith("sk-你的"):
            raise LLMConfigError(
                "缺少 DEEPSEEK_API_KEY。请执行 `cp .env.example .env` 并填入真实 Key "
                "（申请地址 https://platform.deepseek.com/api_keys）"
            )

        self.model = (model or os.getenv("DEEPSEEK_MODEL", "deepseek-flash")).strip()
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.usage = Usage()
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=timeout)

    # ------------------------------------------------------------------ 同步
    def chat(self, messages: Iterable[Message | dict[str, str]]) -> str:
        """非流式调用，返回回答文本。"""
        payload = [m.to_dict() if isinstance(m, Message) else m for m in messages]

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=payload,  # type: ignore[arg-type]
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
        except AuthenticationError as exc:
            raise LLMCallError("API Key 无效或已过期，请到控制台重新生成。") from exc
        except RateLimitError as exc:
            raise LLMCallError("触发限速（429），稍等几秒再试。") from exc
        except APITimeoutError as exc:
            raise LLMCallError("请求超时。网络慢或问题太长，可降低 max_tokens 重试。") from exc
        except APIConnectionError as exc:
            raise LLMCallError(
                "无法连接 api.deepseek.com。检查 WSL 网络/代理：curl -I https://api.deepseek.com"
            ) from exc
        except APIStatusError as exc:
            if exc.status_code == 402:
                raise LLMCallError("余额不足（402），请到平台充值。") from exc
            raise LLMCallError(f"服务返回错误 {exc.status_code}: {exc.message}") from exc

        if resp.usage:
            self.usage.add(resp.usage.prompt_tokens, resp.usage.completion_tokens)

        return resp.choices[0].message.content or ""

    # ------------------------------------------------------------------ 流式
    def stream(self, messages: Iterable[Message | dict[str, str]]):
        """流式调用，逐段 yield 文本（Day 3 选做 / Day 6 正式讲）。"""
        payload = [m.to_dict() if isinstance(m, Message) else m for m in messages]

        try:
            stream = self._client.chat.completions.create(
                model=self.model,
                messages=payload,  # type: ignore[arg-type]
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                stream=True,
            )
            for chunk in stream:
                if chunk.usage:  # 开启 include_usage 时最后一块会带统计
                    self.usage.add(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield delta
        except AuthenticationError as exc:
            raise LLMCallError("API Key 无效或已过期。") from exc
        except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
            raise LLMCallError(f"流式调用失败：{type(exc).__name__}") from exc


if __name__ == "__main__":
    # 自检：确认配置正确、能拿到回答
    client = LLMClient(max_tokens=128)
    print(f"[自检] model={client.model} base_url={os.getenv('DEEPSEEK_BASE_URL')}")
    try:
        print("[回答]", client.chat([Message("user", "只回答两个字：成功")]))
        print("[用量]", client.usage.summary())
    except LLMConfigError as exc:
        print(f"❌ 配置错误：{exc}")
