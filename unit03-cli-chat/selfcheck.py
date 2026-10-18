"""Unit 3 代码自检脚本（离线，不消耗 API 额度）。

用途：在没有真实 API Key、也不想花钱的情况下，验证 llm.py / chat.py 的逻辑是否正确。
它用假的 openai / dotenv / rich 模块替换真实依赖，然后真的跑一遍命令行交互流程。

运行（WSL 里）：
    python selfcheck.py
"""

from __future__ import annotations

import io
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# ---------------------------------------------------------------------------
# 1. 伪造第三方依赖，让脚本无需安装、无需联网即可运行
# ---------------------------------------------------------------------------
fake_usage = types.SimpleNamespace(prompt_tokens=12, completion_tokens=7)
fake_usage.total_tokens = 19


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)
        self.delta = _FakeMessage(content)
        self.finish_reason = "stop"


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]
        self.usage = fake_usage


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return iter([_FakeResponse("流式")])
        return _FakeResponse("这是模型返回的**回答**。")


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeOpenAI:
    """模拟 openai.OpenAI 客户端。"""

    last: "_FakeOpenAI | None" = None

    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.chat = _FakeChat()
        _FakeOpenAI.last = self


class _FakeAPIError(Exception):
    pass


openai_mod = types.ModuleType("openai")
openai_mod.OpenAI = _FakeOpenAI  # type: ignore[attr-defined]
for _name in (
    "AuthenticationError",
    "RateLimitError",
    "APITimeoutError",
    "APIConnectionError",
):
    setattr(openai_mod, _name, type(_name, (_FakeAPIError,), {}))


class _FakeStatusError(_FakeAPIError):
    def __init__(self, status_code: int = 500, message: str = "boom") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message


openai_mod.APIStatusError = _FakeStatusError  # type: ignore[attr-defined]
sys.modules["openai"] = openai_mod

dotenv_mod = types.ModuleType("dotenv")
dotenv_mod.load_dotenv = lambda *a, **k: None  # type: ignore[attr-defined]
sys.modules["dotenv"] = dotenv_mod

rich_mod = types.ModuleType("rich")
console_mod = types.ModuleType("rich.console")
markdown_mod = types.ModuleType("rich.markdown")
panel_mod = types.ModuleType("rich.panel")


class _FakeConsole:
    def __init__(self, *a, **k) -> None:
        self.out: list[str] = []

    def print(self, *args, **kwargs) -> None:
        text = " ".join(str(a) for a in args)
        self.out.append(text)

    def input(self, prompt: str = "") -> str:
        return _SCRIPTED.pop(0)

    class _Status:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def status(self, *a, **k):
        return _FakeConsole._Status()


class _FakeMarkdown(str):
    def __new__(cls, text: str, *a, **k):
        return super().__new__(cls, text)


console_mod.Console = _FakeConsole  # type: ignore[attr-defined]
markdown_mod.Markdown = _FakeMarkdown  # type: ignore[attr-defined]
panel_mod.Panel = types.SimpleNamespace(fit=lambda *a, **k: "panel")  # type: ignore[attr-defined]
rich_mod.console = console_mod  # type: ignore[attr-defined]
rich_mod.markdown = markdown_mod  # type: ignore[attr-defined]
rich_mod.panel = panel_mod  # type: ignore[attr-defined]
sys.modules["rich"] = rich_mod
sys.modules["rich.console"] = console_mod
sys.modules["rich.markdown"] = markdown_mod
sys.modules["rich.panel"] = panel_mod

# ---------------------------------------------------------------------------
# 2. 注入假的 API Key，然后跑真实逻辑
# ---------------------------------------------------------------------------
import os  # noqa: E402

os.environ["DEEPSEEK_API_KEY"] = "sk-selfcheck-fake-key"
os.environ["DEEPSEEK_BASE_URL"] = "https://api.deepseek.com"
os.environ["DEEPSEEK_MODEL"] = "deepseek-flash"

import llm  # noqa: E402

_SCRIPTED: list[str] = []
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  [PASS] {name}")
    else:
        failures.append(name)
        print(f"  [FAIL] {name} {detail}")


print("=" * 62)
print("Unit 3 自检 · 离线逻辑验证（不调用真实 API）")
print("=" * 62)

print("\n[1/6] llm.LLMClient 基础调用")
client = llm.LLMClient(temperature=0.3, max_tokens=512)
check("模型名读取正确", client.model == "deepseek-flash", client.model)
check("base_url 传给 SDK", _FakeOpenAI.last.init_kwargs.get("base_url") == "https://api.deepseek.com")
answer = client.chat([llm.Message("user", "你好")])
check("返回文本", answer == "这是模型返回的**回答**。", answer)
check("messages 结构正确", _FakeOpenAI.last.chat.completions.calls[0]["messages"] == [{"role": "user", "content": "你好"}])
check("temperature 透传", _FakeOpenAI.last.chat.completions.calls[0]["temperature"] == 0.3)
check("max_tokens 透传", _FakeOpenAI.last.chat.completions.calls[0]["max_tokens"] == 512)

print("\n[2/6] Usage 统计与成本计算")
u = llm.Usage()
u.add(1_000_000, 1_000_000)
check("token 累加", (u.prompt_tokens, u.completion_tokens) == (1_000_000, 1_000_000))
check("总 token", u.total_tokens == 2_000_000)
check("成本 = 1 + 4 = 5 元", abs(u.cost_cny - 5.0) < 1e-9, str(u.cost_cny))
check("调用次数", u.calls == 1)
client2 = llm.LLMClient()
client2.chat([llm.Message("user", "x")])
check("真实调用后自动累计", client2.usage.calls == 1 and client2.usage.total_tokens == 19, str(client2.usage))

print("\n[3/6] 缺少 API Key 时的报错")
os.environ["DEEPSEEK_API_KEY"] = ""
try:
    llm.LLMClient()
    check("抛出 LLMConfigError", False, "没有抛异常")
except llm.LLMConfigError as exc:
    check("抛出 LLMConfigError", True)
    check("提示里有申请链接", "platform.deepseek.com" in str(exc))
os.environ["DEEPSEEK_API_KEY"] = "sk-selfcheck-fake-key"

print("\n[4/6] 异常翻译成人类可读提示")
_orig_create = _FakeCompletions.create


def _raise(auth_error):
    def _inner(self, **kwargs):
        raise auth_error("boom")

    return _inner


for exc_name, keyword in [
    ("AuthenticationError", "API Key"),
    ("RateLimitError", "429"),
    ("APITimeoutError", "超时"),
    ("APIConnectionError", "无法连接"),
]:
    _FakeCompletions.create = _raise(getattr(openai_mod, exc_name))
    c = llm.LLMClient()
    try:
        c.chat([llm.Message("user", "x")])
        check(f"{exc_name} → LLMCallError", False)
    except llm.LLMCallError as exc:
        check(f"{exc_name} → 友好提示含「{keyword}」", keyword in str(exc), str(exc))

_FakeCompletions.create = _raise(lambda m: _FakeStatusError(402, "Insufficient Balance"))
c = llm.LLMClient()
try:
    c.chat([llm.Message("user", "x")])
    check("402 → 余额不足提示", False)
except llm.LLMCallError as exc:
    check("402 → 余额不足提示", "余额不足" in str(exc), str(exc))
_FakeCompletions.create = _orig_create

print("\n[5/6] 流式接口")
c = llm.LLMClient()
chunks = list(c.stream([llm.Message("user", "你好")]))
check("能拿到流式分片", chunks == ["流式"], str(chunks))
check("流式也统计用量", c.usage.total_tokens == 19, str(c.usage))

print("\n[6/6] chat.py 交互流程（模拟输入）")
import chat  # noqa: E402

_SCRIPTED.extend(
    [
        "你好",            # 正常一轮
        "/tokens",         # 查看用量
        "/system 你只说文言文",  # 改 System
        "再说一次",         # 改完之后必须再问一次，才能验证新 System 真的发出去了
        "/reset",          # 清空上下文
        "第三次提问",       # 清空后再问一次，验证历史确实被丢弃
        "/help",           # 帮助
        "/不存在的命令",     # 未知命令
        "/exit",           # 退出
    ]
)
sys.argv = ["chat.py"]  # 清掉 pytest 等外部参数
code = chat.main()
check("正常退出码 0", code == 0, str(code))

fake_console = chat.console
output = "\n".join(fake_console.out)
check("打印了助手回答", "这是模型返回的" in output)
check("显示 token 统计", "输入" in output and "输出" in output)
check("System Prompt 已更新", "System Prompt 已更新" in output)
check("说明上下文已清空", "上下文已清空" in output)
check("未知命令有提示", "未知命令" in output)


def _history_has_system_role() -> bool:
    """System 改变后，发给模型的 messages[0] 必须是新的 system。"""
    calls = _FakeOpenAI.last.chat.completions.calls
    return any(
        m.get("role") == "system" and m.get("content") == "你只说文言文"
        for call in calls
        for m in call["messages"]
    )


check("新 System 真的传给了模型", _history_has_system_role())


def _reset_dropped_history() -> bool:
    """`/reset` 之后的那次请求，不应再包含 reset 之前的消息。"""
    calls = _FakeOpenAI.last.chat.completions.calls
    # 找到「第三次提问」对应的那次调用（reset 之后的第一轮）
    for call in calls:
        if any(m.get("content") == "第三次提问" for m in call["messages"]):
            return len(call["messages"]) == 2  # 只剩 system + 本次 user
    return False


check("/reset 之后历史确实被丢弃", _reset_dropped_history())

print("\n" + "=" * 62)
if failures:
    print(f"结果：{len(failures)} 项失败 → {failures}")
    sys.exit(1)
print("结果：全部通过 ✅ 代码逻辑无误（真实 API 调用请按 README 配置 .env 后运行 chat.py）")
