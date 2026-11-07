# 交接文档 · ai-journey 仓库整改任务

> 生成时间：2026-11-08 01:30（Asia/Shanghai）
> 交接原因：用户（rik）将后续工作交给其他 AI 处理
> 仓库：https://github.com/Conroy-X/ai-journey（公开）
> 本地主副本：WSL `~/ai-journey/`（git 仓库根目录即此处）

---

## 一、这个项目是什么

**用户 李玉斌（rik）** 正在从传统 Web/前端 转向 **AI 应用开发工程师**。
这个仓库是他的 90 单元学习工程：每个 `unitNN-xxx/` 是一个学习单元，包含
`main.py`（离线自检）+ `README.md`（讲义）+ `notes.md`（个人笔记，留空给用户填）。

**重要**：单元是「学习单元」，不是「日历天」。没有「落后」的概念。

---

## 二、当前状态（截至本次交接）

### 已完成 ✅

| 范围 | 状态 |
| --- | --- |
| Unit 1–60 | 全部有代码 + 文档，**批量自检全绿** |
| Unit 61–78 | 已生成，**自检全绿**（Docker 真机构建 76 秒通过） |
| Unit 79–81 | **已按计划主题重做**，自检全绿（79=14项 / 80=14项 / 81=16项） |
| 仓库更名 | `deepseek-stream-demo` → `ai-journey`（描述已更新） |
| 模型名统一 | 全仓 `deepseek-chat` → `deepseek-flash` |
| PII 扫描 | 干净（`python3 00-setup/check-pii.py`） |

**最新提交**：`767d88a feat(unit81): 按计划重做——Research Agent 优化`
**远端同步**：local == remote ✅

### 剩余未完成 ⬜

用户在最后一轮说「全部处理」，指的是审计发现的三大类问题。
已完成 79/80/81 的重做，**剩下这些**：

#### A. 计划主题重做（79–83 中的 82、83）

| 单元 | 目录 | 计划要求 | 当前实际 | 需要做 |
| --- | --- | --- | --- | --- |
| **82** | `unit82-langgraph/` | LangGraph 优化：**子图 / 并行节点 / 流式事件** | 只有迷你图执行（节点/边/条件路由） | 补子图、并行、流式事件三块 |
| **83** | `unit83-mcp/` | **把 MCP 工具挂进 Research Agent** | 只有工具注册表（list/call） | 接进 Unit 81 的 Research Agent |

#### B. 覆盖补齐

| 单元 | 计划要求 | 当前 | 需要做 |
| --- | --- | --- | --- |
| **70** | 30 条评测集 + 检索命中率 + 忠实度 + 答案相关性 | 只有 4 条示例 + 简单指标 | 扩到 30 条，补忠实度/相关性指标 |
| **87** | 模拟面试 LLM+RAG：**30 题** | 8 题 | 扩到 30 题 |
| **88** | 模拟面试 Agent+MCP：**30 题** | 8 题 | 扩到 30 题 |
| **89** | 模拟面试 AI 工程化：**30 题** | 8 题 | 扩到 30 题 |
| **61** | Docker：**FastAPI + PostgreSQL 用 compose 一键起** | 只有 FastAPI，无 PG | compose 加 PG 服务 + 连接验证 |

#### C. 结构统一（84–90）

Unit 84–90 目前**没有 FastAPI app**（只有脚本 + 自检），而 61–83 都有 HTTP 端点。
且 84–90 的 README/notes 是**模板生成**（37 行/30 行），比 61–78（60–112 行）薄。

需要：给 84–90 各加 `app = FastAPI(...)` + 至少 2 个 HTTP 端点 + 加深 README。

---

## 三、环境与约束（重要，别踩坑）

### 网络

| 项 | 情况 |
| --- | --- |
| GitHub | **HTTPS 不通，SSH 22 可用**（`git push` 走 SSH） |
| GitHub API | `api.github.com` 可用（需 PAT） |
| PyPI | 走 USTC 镜像（已配） |
| Docker Hub | 已配 daemon 代理 `http://172.19.64.1:7897`（宿主 Clash） |
| Dockerfile 内 pip | **必须** `-i https://mirrors.ustc.edu.cn/pypi/simple`，否则卡死 |

### 命令

```bash
# WSL 里 uv 在 ~/.local/bin，非交互 shell 要手动加 PATH
export PATH="$HOME/.local/bin:$PATH"

# 每个单元的固定流程
cd ~/ai-journey/unitNN-xxx
uv venv --python /usr/bin/python3 .venv
uv pip install -q -r requirements.txt
uv run python main.py          # 离线自检
```

### 提交前必做

```bash
cd ~/ai-journey
python3 00-setup/check-pii.py   # PII 扫描，必须通过
```

**PII 扫描器会拦测试夹具**（如手机号样例）。豁免方式：在该行行尾加
`# pii-check: allow`（Python）或 `<!-- pii-check: allow -->`（Markdown）。

### 提交规范

```bash
git -c user.name="Conroy-X" -c user.email="Conroy-X@users.noreply.github.com" \
  commit -m "feat(unitNN): 简述"
```

类型前缀：`feat` / `fix` / `docs` / `chore` / `refactor`。
仓库有 **pre-commit 守门员钩子**，会自动拦截敏感文件/密钥/大文件。

### GitHub PAT

用户的 classic PAT 存在**个人画像记忆**里（`C:\Users\rik\.workbuddy\user-*/MEMORY.md` L44）。
⚠️ 这枚 token 权限接近全权（含 `delete_repo`/`admin:org`），**建议提醒用户降权**。
改仓库名/描述需要它；日常 git 操作走 SSH 不需要。

---

## 四、工作方式约定（用户偏好，务必遵守）

1. **直接动手，不要过度询问**——用户明确说过「新建文件直接操作即可」
2. **用简体中文**回复
3. **notes.md 里「我自己的话解释」段落必须留空**给用户自己填，不要代写
4. **README 里的命令必须真跑过**，不能只是写上去
5. **每个单元自检必须全绿**才算完成
6. 用户有「过度准备、迟迟不交付」的倾向，**要推着他跑起来**而不是只读文档

---

## 五、如何继续

### 立刻可做的下一步

```bash
cd ~/ai-journey
git pull                      # 确保最新（应该已经在 767d88a）
ls -d unit8[2-9]* unit90*     # 看剩下的目录
```

**推荐顺序**（按价值/成本比）：

1. **B 类补齐**（性价比最高，纯内容填充）
   - 87–89 题库 8 → 30 题：改 `main.py` 里的 `BANK` 列表即可，自检会自动验证
   - 70 评测集 4 → 30 条 + 加忠实度/相关性指标
2. **A 类重做 82、83**（需要理解已有代码）
3. **C 类统一 84–90**（工作量最大，每个单元要加 app + 扩文档）

### 验证方式

改完任何单元，跑：

```bash
cd ~/ai-journey/unitNN-xxx && uv run python main.py
```

必须看到 `结果：N 通过 / 0 失败`。

全量验证（批量跑 61–90）：

```bash
cd ~/ai-journey
for n in $(seq 61 90); do
  d=$(ls -d unit${n}-*/ | head -1)
  ( cd "$d" && uv run python main.py 2>&1 | grep -oE '结果：[0-9]+ 通过 / [0-9]+ 失败' | sed "s/^/unit$n: /" )
done
```

---

## 六、审计发现的完整问题清单（供参考）

这次审计是「从非自检角度」做的——不看自检是否通过，而查：

| 类别 | 发现 | 状态 |
| --- | --- | --- |
| 计数不一致 | unit61 README 说 26 实际 27；unit62 说「约30」实际 26 | ✅ 已修 |
| 计划缺口 | unit74 缺 KV Cache、unit75 缺 DPO/量化/投机解码 | ✅ 已补 |
| 章节编号 | 插入新章节导致 unit74/75 出现重复编号 | ✅ 已修 |
| 主题漂移 | unit79–83 与计划主题不符 | 🟡 79/80/81 已重做，82/83 待做 |
| 覆盖不足 | 70 评测集不足、87–89 题库不足、61 未接 PG | ⬜ 待做 |
| 结构不统一 | 84–90 无 FastAPI app、README 模板化 | ⬜ 待做 |
| 技术正确性 | BPE/Transformer/Attention/LLM 原理逐段读过 | ✅ 未发现事实错误 |
| git 卫生 | 866 文件，无 venv/logs/db 误提交 | ✅ 干净 |

---

## 七、关键文件索引

| 文件 | 作用 |
| --- | --- |
| `README.md` | 仓库门面：进度、目录树、快速开始 |
| `docs/学习单元计划.md` | **主计划**：90 单元主题、资料、完成标准（含「实做差异说明」） |
| `docs/interview-questions.md` | 面试题库（Unit 87–89 用） |
| `00-setup/check-pii.py` | PII 扫描器（提交前必跑） |
| `unitNN-xxx/main.py` | 每单元自检入口（`uv run python main.py`） |

---

## 八、交接检查清单

接手后建议先确认：

- [ ] `cd ~/ai-journey && git log --oneline -3` 看到 `767d88a`
- [ ] `git status` 干净
- [ ] `python3 00-setup/check-pii.py` 通过
- [ ] 随便挑一个单元 `uv run python main.py` 能跑
- [ ] 读一遍 `docs/学习单元计划.md` 的「实做差异说明」段
- [ ] 确认用户当前学习进度（**Unit 20 基础 RAG 学习中**，21–90 材料已备好待学）

---

**交接人备注**：这个仓库的质量基线是「每个单元离线自检全绿 + README 讲清为什么」。
任何新单元都要达到这个基线，否则不如不写。
