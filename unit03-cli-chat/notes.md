# Unit 3 笔记 · LLM API 与 CLI Chat

> 边做边填，控制在 5 分钟内写完。这些笔记在 Unit 87 模拟面试时就是你的复习材料。

## 今天做了什么

- 

## 遇到的报错 & 解决过程

| 报错 | 原因 | 怎么解决 |
| --- | --- | --- |
| 不知道怎么获取header |  | 通过事件`hook` 或者 `chat` 调用的时候使用 `with_raw_response`  |
|当前只是能够读懂unit03的代码， 让自己写估计有点难写出来， 需要对着API一点点才行|| -- |

## 今天学到的 3 个关键点

1. 了解ai调用必要参数有哪些、 model, message[], 非不要参数有哪些： 温度， max_token, stream
2. 清空上下文实际上就是清楚所有的user prompt 和 assitant answer
3. 怎么查看当前会话token 和 统计当前例会总token

## 亲手验证的 4 个实验结论

1. `/reset` 之后再问「我叫什么」→   不再记住我之前说的名字
2. 改 System Prompt 后风格变化 →    会按照新的system prompt 作为基准， 作为职责边界回答问题
3. 连续 10 轮后 `/tokens` 的变化趋势 → 使用量越来越多、 
4. 错误 Key 时的报错信息 → ❌ API Key 无效或已过期，请到控制台重新生成。

## 我自己的话解释「为什么 LLM 没有记忆」

- 按照我目前的理解， LLM的输入和api参数相关； 记忆完全依赖于message参数， 以system prompt 作为职责边界， 以user input 作为问题， 以assitant answer 作为响应； 每一轮对话都存在message， 每一次API都会带上全量的内容。

## Token / 成本

- 今日累计：输入 3420 tokens，输出 1857 tokens，约 ¥0.0108

## 明天的第一件事

- Unit 4 Prompt 工程：写好五段式 Prompt（角色 + 任务 + 约束 + 输出格式 + 示例）
