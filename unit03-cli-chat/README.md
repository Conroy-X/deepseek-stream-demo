# Unit 3 · CLI Chat —— 用 Python 调用 DeepSeek API

> 目标：在 WSL 里跑通「Python → LLM API → 多轮对话 → 打印 token 成本」的最小闭环。
> 预计耗时：60 分钟（环境已装好，实际写代码约 50 分钟）。

---

## 一、项目位置

> ✅ **已经放进你的 WSL 了**（2026-10-10，通过 SSH 完成）。

```bash
cd ~/ai-journey/unit03-cli-chat
ls -la
```

Windows 工作区保留了一份备份：`C:\Users\rik\Documents\deepseek-harness\default-workspace\ai-journey\`。
**以 WSL 里的这份为准**，后续改动都在 WSL 里做。

---

## 二、环境已经装好（记录备查）

> ✅ **这一步也已替你做完**。直接跳到「三、先做离线自检」。
> 下面是实测记录，换机器或重装时照做。

### 本机实测环境（WSL Debian 13 trixie）

| 项目 | 实测结果 | 说明 |
| --- | --- | --- |
| 会话用户 | `rik`，**uid 1001** | `/home/rik` 原本属于 uid 1000，已用 sudo 修复归属 |
| Python | **3.13.5**（`/usr/bin/python3`） | 别装 3.12：uv 下载 Python 走 GitHub，**本机 GitHub 不通** |
| uv | 0.12.23（`~/.local/bin/uv`） | PyPI 直连只有 ~8KB/s，必须走镜像 |
| PyPI 镜像 | **USTC**，写在 `~/.config/uv/uv.toml` | 阿里云镜像索引过期（pydantic 2.14.0 的 wheel 404），Tsinghua 403 |
| 虚拟环境 | `~/ai-journey/unit03-cli-chat/.venv` | 用 `/usr/bin/python3` 创建，避开 uv 自带的 3.14 shim |
| 依赖版本 | `openai 3.28.0`、`pydantic 2.14.0`、`rich 15.0.0`、`python-dotenv 1.2.4` | 已实测导入成功 |

### 每次开新终端的固定动作

```bash
cd ~/ai-journey/unit03-cli-chat
source .venv/bin/activate      # 提示符前面会出现 (.venv)
python selfcheck.py            # 不花钱，确认代码没坏
```

### 配置 API Key（**不要写进代码**）

1. 打开 <https://platform.deepseek.com/api_keys> 申请 Key（形如 `sk-xxxx`）。
   已确认本机 WSL **能直连** `api.deepseek.com`（0.13 秒响应），不需要代理。
2. 一条命令写好 `.env`（把 `sk-你的key` 换成真实的）：

```bash
cd ~/ai-journey/unit03-cli-chat
cat > .env <<'EOF'
DEEPSEEK_API_KEY=sk-你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
EOF
chmod 600 .env
```

3. 确认 `.gitignore` 里有 `.env`（本项目已包含）。

> ⚠️ **安全红线**：API Key 永远只放在 `.env` 或环境变量里。一旦 commit 到 GitHub，密钥会在几秒内被爬虫扫走并盗刷。真发生了就立刻去控制台吊销重建。

---

## 三、先做离线自检（不花一分钱，约 1 分钟）

```bash
cd ~/ai-journey/unit03-cli-chat && source .venv/bin/activate
python selfcheck.py
```

这个脚本用「假的」openai / dotenv / rich 模块替换真实依赖，把 `llm.py` 和 `chat.py`
的核心逻辑真跑一遍（27 项断言）：参数透传、token 统计、成本计算、异常翻译、流式接口、
`/reset` 与 `/system` 的行为。目的是在你还没配置 API Key、也不想花钱的时候，先确认
代码本身没问题。

看到 `结果：全部通过 ✅` 就说明环境以外的逻辑是对的（**已在本机实测通过**）。

---

## 四、跑第一个调用（约 10 分钟）

```bash
python 01_hello.py
```

你应该看到类似输出：

```
[模型] deepseek-flash
[提问] 用一句话解释什么是 LLM 的 token。
[回答] Token 是模型处理文本的最小单位……
[用量] 输入 24 tokens / 输出 18 tokens / 约 ¥0.0002
```

### 这一步要看懂的核心 API（面试常问）

| 参数 | 作用 | 练习建议 |
| --- | --- | --- |
| `model` | 用哪个模型 | 换成 `deepseek-v4-pro` 对比质量与价格 |
| `messages` | 对话消息数组 | `system` 定人设，`user` 提问题，`assistant` 是历史回答 |
| `temperature` | 随机性 0~2 | 改成 0 和 1.5 各跑一次，观察稳定性 |
| `max_tokens` | 输出上限 | 练习期设 512，防止意外烧钱 |
| `stream` | 是否流式 | `01_hello.py` 用非流式，Unit 6 再讲流式 |

**关键认知**：LLM 本身**没有记忆**。所谓「多轮对话」，是每次请求都把之前的全部消息重新发一遍。这正是 Unit 10「Context 管理」要解决的问题。

---

## 五、跑多轮对话 CLI（约 40 分钟）

```bash
python chat.py
```

```
你 > 我叫小明，在做 AI 应用开发的转型
AI > 你好小明！……

你 > 我叫什么？
AI > 你叫小明。            ← 证明上下文生效了

你 > /tokens
[用量] 本会话 3 轮 · 输入 412 tokens · 输出 286 tokens · 约 ¥0.0031

你 > /exit
再见！
```

### 可用命令

| 命令 | 作用 |
| --- | --- |
| `/exit`、`/quit` | 退出 |
| `/reset` | 清空上下文（观察模型立刻"失忆"） |
| `/tokens` | 打印本会话累计 token 与估算费用 |
| `/system <文本>` | 动态修改 System Prompt，立刻对比回答风格差异 |
| `/help` | 帮助 |

命令行参数：`--system`、`--temperature`、`--max-tokens`、`--stream`。

### 一定要亲手做的 4 个实验（这是今天的真正收获）

1. **`/reset` 之后再问「我叫什么？」** → 模型答不出来。理解「上下文 = 每次重发历史」。
2. **`/system 你只能回答一个字`** → 感受 System Prompt 的约束力（Unit 4 的主题）。
3. **连问 10 轮**，`/tokens` 看输入 token 如何**逐轮增长**（历史被重复计费）→ 体会成本与截断的必要性。
4. **把 Key 改错**跑一次 → 看 `AuthenticationError` 的友好提示，理解 Unit 13 错误处理的必要性。

---

## 六、文件说明

| 文件 | 作用 |
| --- | --- |
| `01_hello.py` | 最小调用示例，带注释，先读这个 |
| `llm.py` | 把模型调用封装成类，Unit 4/5/6/13 直接复用 |
| `chat.py` | 多轮对话 CLI（今天的主产出） |
| `selfcheck.py` | 离线自检：不联网、不花钱验证代码逻辑 |
| `.env.example` | 环境变量模板（真 `.env` 不进 git） |
| `requirements.txt` | 依赖清单 |
| `notes.md` | 今日笔记模板，边做边填 |

---

## 七、Unit 3 完成标准（勾完再睡）

- [ ] `python selfcheck.py` 输出「全部通过 ✅」
- [ ] `python 01_hello.py` 能拿到回答
- [ ] `python chat.py` 能连续 3 轮对话并记住上文
- [ ] `/tokens` 能打印输入/输出 token 与估算费用
- [ ] `/reset` 后模型确实"失忆"
- [ ] 错误 Key / 断网时给出友好提示，不吐 traceback
- [ ] `.env` 没有进 git（`git status` 里看不到它）
- [ ] 填完 `notes.md` 并 `git commit`

```bash
git init
git add .
git commit -m "unit03: CLI chat with DeepSeek API (multi-turn + usage + error handling)"
```

> git 身份还没配（`user.name` / `user.email` 都是空的），首次 commit 前先设置：
> `git config --global user.name "你的名字" && git config --global user.email "你的邮箱"`

---

## 八、选做（有时间才做）

`chat.py` 已经内置 `--stream` / `--temperature` / `--max-tokens` / `--system`，所以选做换成下面这些更有价值的事：

1. **读一遍 `llm.py` 的 `stream()`**：搞清 `stream=True` 时返回的是迭代器，最后一块才带 `usage`（Unit 6 正式讲 SSE）。
2. 加 `/save` 和 `/load`：把当前会话存成 JSON 并恢复。
3. 加 `/cost`：按「高峰/空闲时段」自动切单价（读 DeepSeek 价格页的时段规则）。

---

## 九、常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| `openai.AuthenticationError` | Key 错了或 `.env` 没加载；确认 `.env` 与脚本同目录 |
| `Connection error` | WSL 网络/DNS：`curl -I https://api.deepseek.com` 测一下（本机实测正常） |
| `RateLimitError (429)` | 触发限速，等几秒或降低并发（Unit 13 系统处理） |
| `Insufficient Balance (402)` | 余额不足，去控制台充值 |
| 中文乱码 | `export LANG=C.UTF-8` 加到 `~/.bashrc` |
| `uv: command not found` | 新终端没读到 PATH：执行 `export PATH="$HOME/.local/bin:$PATH"` |
| `uv pip install` 卡住或 404 | 镜像问题：检查 `~/.config/uv/uv.toml` 里是 USTC 地址 |
| `uv python install 3.12` 超时 | 正常：本机 GitHub 不通，用系统 Python 3.13.5 即可 |
| `python3` 变成 3.14 | 裸 `python3` 命中了 uv 的 shim；用 `.venv/bin/python` 或 `uv venv --python /usr/bin/python3` |

**官方排错资料**：[DeepSeek 错误码](https://api-docs.deepseek.com/zh-cn/quick_start/error_codes) ｜ [DeepSeek 限速与隔离](https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit)

---

## 十、调试环境（2026-10-14 配置完成）

### 10.1 装了什么 / 在哪

| 配置 | 位置 | 作用 |
| --- | --- | --- |
| `launch.json`（5 个 F5 配置） | `~/ai-journey/.vscode/launch.json` | 断点调试；已绑定 `.venv` 解释器 + 自动注入 `.env`，**不用手动 source activate** |
| `settings.json` | `~/ai-journey/.vscode/settings.json` | VS Code 自动认出 unit03 的虚拟环境、隐藏 `__pycache__` |
| `debugpy 1.8.22` | 装在 `unit03-cli-chat/.venv` | VS Code Python 调试器的后端，**不装按 F5 会报 "debugpy not found"** |
| `debug_response.py` | `unit03-cli-chat/` | 响应查看器：请求 payload → 原始 JSON body → 字段解释 → 流式 chunk |
| `logs/last_response.json` | `unit03-cli-chat/logs/` | 每次调用自动存一份原始响应（已 gitignore） |

### 10.2 怎么用（VS Code 在 Windows 上）

1. VS Code → `F1` → `Remote-SSH: Connect to Host` → 选 `wsl-debian`（或 `WSL: Connect`）
2. 打开文件夹 `~/ai-journey`（**注意是 ai-journey，不是 unit03 子目录**，`.vscode` 在这一层）
3. `Ctrl+Shift+D` 打开 Run and Debug，下拉框选配置，`F5` 启动：

| 配置 | 用途 |
| --- | --- |
| ① 01_hello（第一次真实调用） | 最小调用，验证环境 |
| ② chat.py（多轮对话 CLI） | 调试主产出，可在 `llm.py` 打断点 |
| ③ debug_response（查看原始响应） | **想看服务端到底返回了什么，选这个** |
| ④ debug_response --stream（看 chunk） | 流式逐块输出 |
| ⑤ 当前文件（不读 .env） | 调试任意脚本时的通用配置 |

4. **打断点的位置（第一天就记住这三个）**：
   - `llm.py` → `resp = self._client.chat.completions.create(...)` 之后一行：能直接看 `resp` 对象，展开 `choices` / `usage`
   - `llm.py` → `except AuthenticationError` 那行：调试错误分支
   - `chat.py` → `messages.append(...)` 那行：观察上下文数组如何逐轮增长（Unit 10 的核心）

### 10.3 debug_response.py 命令行用法

```bash
source .venv/bin/activate

python debug_response.py                      # 完整视图（推荐）
python debug_response.py -p "换个问题"          # 换提问
python debug_response.py --raw-http           # 额外打印 SDK 的 HTTP 层日志
python debug_response.py --stream             # 看每个 chunk 的原始 JSON
python debug_response.py --json-only > r.json # 只要原始 JSON，便于 jq 分析
python debug_response.py --raw-http 2>&1 | grep -E "POST|HTTP"   # 只看请求行与状态码
```

### 10.4 实测响应结构（2026-10-14，deepseek-flash）

一次真实调用的返回（节选）：

```json
{
  "id": "6ffba341-8f33-49a2-8b06-ed3ac846a76a",
  "object": "chat.completion",
  "created": 1791908251,
  "model": "deepseek-flash",
  "choices": [{
    "index": 0,
    "message": {
      "role": "assistant",
      "content": "Token 是 LLM 处理文本时的最小单位……",
      "reasoning_content": "我们需要回答用户中文问题……"
    },
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 52,
    "completion_tokens": 158,
    "total_tokens": 210,
    "completion_tokens_details": { "reasoning_tokens": 124 },
    "prompt_cache_hit_tokens": 0,
    "prompt_cache_miss_tokens": 52
  }
}
```

**四个必须记住的点：**

1. **`deepseek-flash` 是推理模型**，`message` 里多了一个 `reasoning_content`（推理过程）。
   实测这一次：输出 158 token 里有 **124 个是推理 token**（占 78%），而推理 token **按输出价计费**。
   所以同样的回答，推理模型比非推理模型贵得多——`max_tokens` 要设小，`temperature` 对它基本无效。
   代码里只读 `content`，推理内容会被静默丢弃但照常扣费。
2. **`finish_reason`**：`stop` = 正常结束；`length` = 被 `max_tokens` 截断（回答会突然断掉，看到它就调大或缩短问题）。
3. **`prompt_cache_hit_tokens`**：命中上下文缓存时这部分输入单价更低。多轮对话中，前面不变的历史会自动命中——
   这是 Unit 10「Context 管理」省钱的原理。
4. **`x-ds-trace-id`**（响应头）：向服务商报障时唯一有用的凭证，`debug_response.py` 会自动打印。

### 10.5 不写代码时的排查手段

```bash
# 1) 看 SDK 到底发了什么 HTTP 请求（openai SDK 原生支持）
OPENAI_LOG=debug python 01_hello.py 2>&1 | grep -E "POST|HTTP/1.1|retry"

# 2) 看最近一次的原始响应
cat logs/last_response.json | jq .

# 3) 确认网络与鉴权（不花 token）
curl -s -o /dev/null -w "%{http_code}\n" https://api.deepseek.com
```

> ⚠️ `--raw-http` 的日志里会**带完整 Authorization 头**，截图分享前记得打码。

### 10.6 怎么拿响应头（x-ds-trace-id 等）—— 2026-10-14 全部实测

先说结论：**`x-ds-trace-id` 只在响应头里，`response.body` 里没有**。所以想要它，必须走下面 4 条路之一。
DeepSeek 一次调用的响应头共 **13 个**（`debug_response.py` 已全部打印）：

```
server: openresty                                    content-encoding: gzip
content-type: application/json                       access-control-allow-credentials: true
vary: accept-encoding, origin, ...                   x-ds-trace-id: 7532cbcafad65e343352fbcca0acc842   ★
strict-transport-security: max-age=31536000; ...     x-content-type-options: nosniff
transfer-encoding: chunked                           connection: keep-alive
date: Tue, 13 Oct 2026 16:28:05 GMT                  eo-log-uuid: 8896895260681856195
eo-cache-status: MISS                                ← 腾讯 EdgeOne CDN，MISS 表示没走缓存
```

#### 方式 A：`with_raw_response`（首选，本项目已在用）

```python
raw = client.chat.completions.with_raw_response.create(model=..., messages=..., max_tokens=8)

headers   = dict(raw.headers)                  # 13 个头，键为小写
trace_id  = headers["x-ds-trace-id"]           # ← 报障凭证
status    = raw.status_code                    # 200
underlying = raw.http_response                 # 底层 httpx2.Response 对象
resp      = raw.parse()                        # 解析成正常 ChatCompletion，业务代码照常用
```

`debug_response.py` 走的就是这条路，所以它的输出里天然带着 trace-id。

#### 方式 B：`httpx2` 事件钩子（生产级，业务代码零改动）

日志系统里想给**每一次**调用自动打 trace-id，用这个。已写进 `headers_demo.py`：

```python
from httpx2 import Client            # ⚠️ 是 httpx2，不是 httpx（见下方坑 2）

def on_response(response):
    logger.info("llm_call status=%s trace_id=%s",
                response.status_code, response.headers.get("x-ds-trace-id"))

client = OpenAI(..., http_client=Client(event_hooks={"response": [on_response]}))
```

后面 Unit 13「LLM 工程化」的日志系统直接复用这个钩子。

#### 方式 C：SDK 调试日志（只能看状态码）

```bash
OPENAI_LOG=debug python 01_hello.py 2>&1 | grep -i "http"
# Building HTTP request: method=POST retries_taken=0
# Sending HTTP Request: POST
# HTTP Response: POST 200
```

⚠️ **它不打印响应头**，所以拿不到 trace-id；它的价值是看 `retries_taken`（重试了几次），Unit 13 调重试逻辑时用它。

#### 方式 D：curl 裸请求（判断是 SDK 还是网络的问题）

```bash
set -a; . ./.env; set +a
curl -sS -D - -o /dev/null -X POST https://api.deepseek.com/chat/completions \
  -H "Authorization: Bearer $DEEPSEEK_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"deepseek-flash","messages":[{"role":"user","content":"hi"}],"max_tokens":1}'
```

`-D -` 把响应头打到终端，`-o /dev/null` 丢掉 body。实测返回 `HTTP/2 200` + 12 个头。

#### ⚠️ 三个实测踩到的坑

| 坑 | 现象 | 正确做法 |
| --- | --- | --- |
| 1. 普通 `create()` 拿不到头 | `completion.response` / `._response` / `.request_id` 全部 `AttributeError`；`._request_id` 存在但值为 `None`（DeepSeek 不返回 `x-request-id`） | 必须用 `with_raw_response` 或事件钩子 |
| 2. `httpx` 在这里不存在 | `import httpx` → `ModuleNotFoundError`（openai 3.28 内部用的是 **httpx2 2.13.1**） | `from httpx2 import Client`；也可 `import openai._base_client as b; b.httpx2` |
| 3. 别用标准字段名去取 | `x-request-id` 取不到东西 | DeepSeek 的追踪 ID 固定叫 `x-ds-trace-id`，换服务商时先 `print(dict(raw.headers))` 看一眼真实字段名 |

> 已经封装好、可直接跑：`python headers_demo.py`（方式 A + B 实跑，C/D 的完整命令）。
