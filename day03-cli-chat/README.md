# Day 3 · CLI Chat —— 用 Python 调用 DeepSeek API

> 目标：在 WSL 里跑通「Python → LLM API → 多轮对话 → 打印 token 成本」的最小闭环。
> 预计耗时：60 分钟（环境已装好，实际写代码约 50 分钟）。

---

## 一、项目位置

> ✅ **已经放进你的 WSL 了**（2026-10-10，通过 SSH 完成）。

```bash
cd ~/ai-journey/day03-cli-chat
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
| 虚拟环境 | `~/ai-journey/day03-cli-chat/.venv` | 用 `/usr/bin/python3` 创建，避开 uv 自带的 3.14 shim |
| 依赖版本 | `openai 3.28.0`、`pydantic 2.14.0`、`rich 15.0.0`、`python-dotenv 1.2.4` | 已实测导入成功 |

### 每次开新终端的固定动作

```bash
cd ~/ai-journey/day03-cli-chat
source .venv/bin/activate      # 提示符前面会出现 (.venv)
python selfcheck.py            # 不花钱，确认代码没坏
```

### 配置 API Key（**不要写进代码**）

1. 打开 <https://platform.deepseek.com/api_keys> 申请 Key（形如 `sk-xxxx`）。
   已确认本机 WSL **能直连** `api.deepseek.com`（0.13 秒响应），不需要代理。
2. 一条命令写好 `.env`（把 `sk-你的key` 换成真实的）：

```bash
cd ~/ai-journey/day03-cli-chat
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
cd ~/ai-journey/day03-cli-chat && source .venv/bin/activate
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
| `stream` | 是否流式 | `01_hello.py` 用非流式，Day 6 再讲流式 |

**关键认知**：LLM 本身**没有记忆**。所谓「多轮对话」，是每次请求都把之前的全部消息重新发一遍。这正是 Day 10「Context 管理」要解决的问题。

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
2. **`/system 你只能回答一个字`** → 感受 System Prompt 的约束力（Day 4 的主题）。
3. **连问 10 轮**，`/tokens` 看输入 token 如何**逐轮增长**（历史被重复计费）→ 体会成本与截断的必要性。
4. **把 Key 改错**跑一次 → 看 `AuthenticationError` 的友好提示，理解 Day 13 错误处理的必要性。

---

## 六、文件说明

| 文件 | 作用 |
| --- | --- |
| `01_hello.py` | 最小调用示例，带注释，先读这个 |
| `llm.py` | 把模型调用封装成类，Day 4/5/6/13 直接复用 |
| `chat.py` | 多轮对话 CLI（今天的主产出） |
| `selfcheck.py` | 离线自检：不联网、不花钱验证代码逻辑 |
| `.env.example` | 环境变量模板（真 `.env` 不进 git） |
| `requirements.txt` | 依赖清单 |
| `notes.md` | 今日笔记模板，边做边填 |

---

## 七、Day 3 完成标准（勾完再睡）

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
git commit -m "day03: CLI chat with DeepSeek API (multi-turn + usage + error handling)"
```

> git 身份还没配（`user.name` / `user.email` 都是空的），首次 commit 前先设置：
> `git config --global user.name "你的名字" && git config --global user.email "你的邮箱"`

---

## 八、选做（有时间才做）

`chat.py` 已经内置 `--stream` / `--temperature` / `--max-tokens` / `--system`，所以选做换成下面这些更有价值的事：

1. **读一遍 `llm.py` 的 `stream()`**：搞清 `stream=True` 时返回的是迭代器，最后一块才带 `usage`（Day 6 正式讲 SSE）。
2. 加 `/save` 和 `/load`：把当前会话存成 JSON 并恢复。
3. 加 `/cost`：按「高峰/空闲时段」自动切单价（读 DeepSeek 价格页的时段规则）。

---

## 九、常见问题

| 现象 | 原因 / 解决 |
| --- | --- |
| `openai.AuthenticationError` | Key 错了或 `.env` 没加载；确认 `.env` 与脚本同目录 |
| `Connection error` | WSL 网络/DNS：`curl -I https://api.deepseek.com` 测一下（本机实测正常） |
| `RateLimitError (429)` | 触发限速，等几秒或降低并发（Day 13 系统处理） |
| `Insufficient Balance (402)` | 余额不足，去控制台充值 |
| 中文乱码 | `export LANG=C.UTF-8` 加到 `~/.bashrc` |
| `uv: command not found` | 新终端没读到 PATH：执行 `export PATH="$HOME/.local/bin:$PATH"` |
| `uv pip install` 卡住或 404 | 镜像问题：检查 `~/.config/uv/uv.toml` 里是 USTC 地址 |
| `uv python install 3.12` 超时 | 正常：本机 GitHub 不通，用系统 Python 3.13.5 即可 |
| `python3` 变成 3.14 | 裸 `python3` 命中了 uv 的 shim；用 `.venv/bin/python` 或 `uv venv --python /usr/bin/python3` |

**官方排错资料**：[DeepSeek 错误码](https://api-docs.deepseek.com/zh-cn/quick_start/error_codes) ｜ [DeepSeek 限速与隔离](https://api-docs.deepseek.com/zh-cn/quick_start/rate_limit)
