"""Unit 34 · Agent Loop —— 一个 while 循环，没有框架。

循环本身只有二十行，难的是**什么时候停**：

    model 返回 tool_calls  → 执行、回填、继续
    model 返回 content     → 收尾
    两个都没有              → 空转，必须停（stopped="empty"）
    步数用完                → 停（stopped="max_steps"）

`AgentRunner` 做成可步进的（`step()` 一次推进一轮），
这样页面上的「走一步」和自检里的「一次跑完」走的是同一份代码 ——
否则你永远不知道可视化出来的东西和真实逻辑是不是一回事。
"""

from __future__ import annotations

import json
from typing import Any

SYSTEM = "你是助手。需要查资料时先调工具，拿到结果再回答。不要编造工具没返回的信息。"
MAX_STEPS_ANSWER = "达到最大步数仍未得到最终答案。"
DIGEST_CHARS = 80


class ToolSpec:
    def __init__(self, name: str, description: str, parameters: dict, fn):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn

    def openai(self) -> dict:
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": self.parameters},
        }


def to_openai_tools(tools: list[ToolSpec]) -> list[dict]:
    return [t.openai() for t in tools]


def digest(value: Any, limit: int = DIGEST_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + f"…(+{len(text) - limit})"


def unanswered_calls(messages: list[dict]) -> list[str]:
    """找出没有被 tool 消息应答的 tool_call id。

    触顶停下时最容易踩：如果最后一条 assistant 带着 tool_calls 却没有对应 tool 消息，
    这个数组发出去就是 400。所以本单元触顶时**仍然执行完最后一次调用**。
    """
    pending: dict[str, str] = {}
    orphan: list[str] = []
    for m in messages:
        if m["role"] == "tool":
            pending.pop(m.get("tool_call_id") or "", None)
            continue
        orphan.extend(pending)
        pending = {c["id"]: c["function"]["name"] for c in m.get("tool_calls") or []}
    orphan.extend(pending)
    return orphan


class AgentRunner:
    """手写循环。step() 推进一轮，done 之后不再推进。"""

    def __init__(self, question: str, model, tools: list[ToolSpec], max_steps: int = 6):
        if max_steps < 1:
            raise ValueError("max_steps 必须 >= 1，否则一次都不会跑")
        self.question = question
        self.model = model
        self.tools = {t.name: t for t in tools}
        self.max_steps = max_steps
        self.messages: list[dict] = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": question},
        ]
        self.trace: list[dict] = []
        self.steps = 0
        self.stopped: str | None = None
        self.answer = ""

    # -- 状态 ---------------------------------------------------------------
    @property
    def done(self) -> bool:
        return self.stopped is not None

    def state(self) -> dict:
        return {
            "question": self.question,
            "steps": self.steps,
            "max_steps": self.max_steps,
            "stopped": self.stopped,
            "answer": self.answer,
            "trace": self.trace,
            "messages": self.messages,
            "unanswered": unanswered_calls(self.messages),
        }

    # -- 一轮 ---------------------------------------------------------------
    def step(self) -> dict:
        if self.done:
            return self.state()

        self.steps += 1
        msg = self.model.complete(self.messages)
        calls = msg.get("tool_calls") or []

        entry = {
            "step": self.steps,
            "type": "model",
            "decision": None,
            "content": msg.get("content"),
            "tool_calls": [
                {"id": c["id"], "name": c["function"]["name"], "arguments": c["function"].get("arguments") or "{}"}
                for c in calls
            ],
            "message": msg,
        }

        if not calls:
            # 唯一的收尾条件：没有 tool_calls。content 空不空是第二层判断。
            self.messages.append({"role": "assistant", "content": msg.get("content")})
            if msg.get("content"):
                entry["decision"] = "answer"
                self.stopped = "answer"
                self.answer = msg["content"]
            else:
                # 既没有工具调用也没有内容 —— 再问一次也是空，直接停
                entry["decision"] = "empty"
                self.stopped = "empty"
                self.answer = ""
            self.trace.append(entry)
            return self.state()

        # 有 tool_calls：即使 content 也有内容，也要继续调工具（content 通常是「我查一下」）
        entry["decision"] = "call_tools"
        self.messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": calls})
        self.trace.append(entry)

        for call in calls:
            name = call["function"]["name"]
            raw = call["function"].get("arguments") or "{}"
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                args = {}
                result = {"ok": False, "content": json.dumps({"error": "arguments 不是合法 JSON"}, ensure_ascii=False)}
            else:
                result = self._execute(name, args)

            tool_msg = {"role": "tool", "tool_call_id": call["id"], "name": name, "content": result["content"]}
            self.messages.append(tool_msg)
            self.trace.append(
                {
                    "step": self.steps,
                    "type": "tool",
                    "tool": name,
                    "args": args,
                    "ok": result["ok"],
                    "digest": digest(result["content"]),
                    "message": tool_msg,
                }
            )

        if self.steps >= self.max_steps:
            # 注意：先执行完工具再停，否则 messages 会停在「有 tool_calls 没有 tool 消息」的非法态
            self.stopped = "max_steps"
            self.answer = MAX_STEPS_ANSWER
        return self.state()

    def _execute(self, name: str, args: dict) -> dict:
        spec = self.tools.get(name)
        if spec is None:
            return {"ok": False, "content": json.dumps({"error": f"未知工具 {name}"}, ensure_ascii=False)}
        try:
            data = spec.fn(**args)
        except TypeError as e:
            return {"ok": False, "content": json.dumps({"error": f"参数不匹配：{e}"}, ensure_ascii=False)}
        except Exception as e:  # noqa: BLE001 - 工具异常不能掀翻循环（Unit 36 展开）
            return {"ok": False, "content": json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)}
        return {"ok": True, "content": json.dumps(data, ensure_ascii=False)}

    def run(self) -> dict:
        while not self.done:
            self.step()
        return self.state()


def run_agent(question: str, model, tools: list[ToolSpec], max_steps: int = 6) -> dict:
    return AgentRunner(question, model, tools, max_steps).run()


def replay_state(state: dict) -> list[dict]:
    """只靠 trace 把 messages 重建出来。重建不出一样的东西，说明 trace 记漏了。"""
    out = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": state["question"]},
    ]
    return out + [t["message"] for t in state["trace"]]
