# Unit 5 笔记 · 结构化输出

> 边做边填。模拟面试的复习材料。

## 今天做了什么

> 2026-10-18 全天记录（含凌晨、白天、下午三段时间线）

### 凌晨 00:05–01:09 · 收尾 day04、踩坑与幕间

- 收尾 `workbuddy2api-panel` 的 docker compose 构建，判断环境不合适后**主动放弃 docker 路线**，
  改回 WSL 原生环境部署（这个决定影响了后面所有天的环境方案）。
- 装 `dsh-workbuddy-connect` 插件失败（报「无法连接插件源」），排查网络后未解决，暂时搁置。
- 让 AI 整理本机的 WSL 安装记录、学习计划与工作区资料，归档到本地备份盘（路径不在仓库中记录）。
- 深夜和 AI 聊了一次「为什么要转型」。结论：**转型的核心动机是抗风险能力和长期安全感，
  不是单纯「想学 AI」**。这个认知直接影响后面怎么取舍学习内容——
  优先投入能变成可展示成果的部分，而不是无限刷概念。
  （细节属私人内容，不在此展开。）
- 01:09 关机。

### 13:14–14:39 · 白天 · 环境清理与小需求

- 删除两个废弃会话（`session-ca6added`、`session-7740cac8`）。
- 问怎么下载《三角洲行动》、找放置挂机类网页游戏 —— 与主线无关，属日常需求。

### 16:07–18:08 · 下午 · Unit 5 主战场（本单元核心）

**1. 环境故障排查（16:07）**
- 报错：`extract.py` 跑不起来。定位到不是代码问题，而是**没用 venv 解释器**——
  `python3` 指向 `/usr/bin/python3`，缺 `rich` 等包。
- 结论：**显式激活 venv 或直接用 `.venv/bin/python`**，不要用运行时自动切换解释器的做法
  —— 它会隐式改行为，出问题时反而更难排查。

**2. 计划体系重构（16:11–16:38）**
- 明确「**不追求 90 天完成，按既定节奏推进**」，放弃日历绑定。
- 计划从「Day N 逐天」改为「**Unit N 学习单元**」，删掉落后追赶表，改为「单元被卡住怎么办」。
- 全仓库 `day*` → `unit*` 重命名，约 200 处引用；补齐 unit01（FastAPI）/ unit02（LLM 基础）
  两个此前只有概念没有代码的单元。

**3. 基础设施（16:41–17:08）**
- **Node**：用 nvm 安装 v24.22.0。GitHub raw 不通，nvm 改从 gitee 镜像拉；
  npm 源设 npmmirror，实测装包 1 秒。
- **Docker 29.9.1 + Compose v5.6.0**：发现 Docker Hub 域名被 **DNS 污染**
  （解析到 108.160.162.98 等无关 IP），配 daocloud / 1ms.run 加速器后可正常拉镜像。
- **PostgreSQL 17.11 + pgvector 0.8.0 + Redis 8.0.2**：原生安装，服务全部 active。
- 中途踩坑：`curl | echo "$PW" | sudo -S tee` 串联导致 sudo 抢走 stdin，
  表现为「GPG key 下载失败」，实际是脚本写法问题。

**4. 隐私事故与善后（17:16–17:26）**
- **发现 unit05 示例简历里有真实姓名、学校、雇主**，且已推送到公开仓库。
- 处理：改为虚构数据（张三 / 某某大学 / 某科技公司 / example.com）。
- 用 `git filter-repo` 重写全部历史 → 强推 → 但发现 **GitHub 仍保留孤儿提交**，
  旧 SHA 的 diff 依然可访问。
- 最终方案：**删库重建**（同名），彻底清除。代价是丢了 1 个 star。
- 期间还发现**自己写的 SECURITY.md 反例里又用了真实信息**，二次重写才干净。
- 新增 `00-setup/check-pii.py` 扫描工具 + `SECURITY.md` 规则文档。

**5. Unit 6 顺带搭建（17:13）**
- 建了 `unit06-streaming/`：FastAPI SSE 后端 + 浏览器实验台，
  实测 curl 每 250ms 到一块、首块 12ms / 总 2219ms；真实模型 75 分块。

**6. Unit 5 概念攻克（17:30–18:08）**
- 追问 `json_mode` 与 `schema` 的区别。
- 追问「硬编人名」「校验拦结构不拦内容」这些结论到底什么意思。
- 自己发现关键问题：**当前 schema 模式有没有保证内容正确的代码？**（答案：没有）
- 追问 `model_validate_json` 是哪来的（→ Pydantic `BaseModel` 内置方法）。
- 提出要求：**以后代码必须打印或保存模型原始响应**，否则只看到解析结果。
  → 实现两层日志（`logs/*-call.json` 存完整 HTTP 响应、`logs/*-extract.json` 存原始输出+解析结果），
  新增 `--show-raw` 参数。
- 要求检查 Unit 5 学习成果 → 三条完成标准全部达标（10/10 通过）。
- 最后由 AI 整理修正笔记。

---

## 三种模式对比（extract.py 的结果直接抄）

| 模式                  | 结果（n/10） | 失败原因（如果有） |
| --------------------- | ------------ | ------------------ |
| ① naive（直接 loads） | 10 / 10      | 没有               |
| ② json_mode           | 10 / 10      | 没有               |
| ③ schema + Pydantic   | 10 / 10      | 没有               |

### ⚠️ naive 的 10/10 是「假象」，别被通过率骗了

10 轮测试里 naive 和 json_mode 都是 10/10，看起来和 schema 一样稳。
但这**不代表 naive 安全**，原因有三：

**① System Prompt 在强行兜底**
`build_extract_messages()` 里的 System Prompt 已经写了"输出指定 JSON 结构"。
模型被 Prompt 约束着吐 JSON，`json.loads` 当然成功。
→ naive 测的不是"模型裸奔时的能力"，而是"模型 + 我们的 Prompt"。

**② 失败场景不在这个数据集里**（实测对照）

| 场景 | naive (`json.loads`) | `parse_fenced` |
| --- | --- | --- |
| 裸 JSON | ✅ 成功 | ✅ 成功 |
| 带 ```` ```json ```` 围栏 | ❌ 抛异常 | ✅ 成功 |
| 前后有闲聊 | ❌ 抛异常 | ❌ 抛异常 |
| 尾随逗号 `{"a":1,}` | ❌ 抛异常 | ❌ 抛异常 |

这些坑在当前 Prompt + 当前模型 + 当前输入下不触发。
换模型、改 Prompt、输入变长、模型开始"解释"时就会冒出来。

**③ 通过率高 ≠ 数据可用**
naive 那 10 次"成功"里，拿到的可能是 `name: null` 这类空值，
`json.loads` 完全不管，程序一路往下跑，最后在业务层才炸。

**判断标准应当是「失败时怎么失败」，而不是「成功了几次」：**
- naive：静默错讹（拿到 null 继续跑）→ **最危险**
- schema：快速失败（ValidationError 立刻抛，精确到字段）→ 可观测、可重试

## 对抗实验（喂了段打篮球吃火锅的文本）

三种模式的真实表现（`--adversarial --show-raw`，同一份输入）：

| 模式 | 结果 | 模型实际输出 |
| --- | --- | --- |
| ① naive | ✅ 1/1「成功」 | `{"name": null, "years_of_experience": null, "seniority": null, "skills": []}` |
| ② json_mode | ✅ 1/1「成功」 | `{"name": null, "years_of_experience": 0, "skills": [], ...}` |
| ③ schema | ❌ 0/1 失败 | `{"name": "", "years_of_experience": 0, "skills": [], "seniority": "junior", ...}` |

- **模型的行为**：没有编造具体人名，而是返回了"空壳"——name 为空、数组为空。
  但 `json_mode` 把 `years_of_experience` 填成了 `0`，`schema` 填了 `seniority: "junior"`，
  这已经是**无中生有的取值**（原文里根本没有这些信息）。

- **naive / json_mode 拦住了什么**：什么都没拦。
  - `naive` 只做 `json.loads`，`{"name": null}` 是合法 JSON，所以算"成功"
  - `json_mode` 也只保证是 JSON，同样放行
  - 这两个的"成功"是**假成功**：数据是空的/编的，程序却以为拿到了结果

- **schema 模式拦住了什么**：3 个字段级错误
  ```
  1. name              String should have at least 1 character      (string_too_short)
  2. skills            List should have at least 1 item, not 0     (too_short)
  3. work_experiences  List should have at least 1 item, not 0     (too_short)
  ```
  这是 `min_length=1` 在起作用——**空字符串和空列表都被挡在门外**。

- **没拦住什么（关键结论）**：
  Pydantic 只检查"形式合法"，拦不住"内容编造"。实测把 name 改成 `"王小明"`、
  再补一段虚构工作经历，结构完全合法，Pydantic **一路放行、零报错**。
  → 结构化校验解决的是"**能不能安全使用这个数据**"，不解决"**这个数据可不可信**"。
  → 抽取类任务要加**来源核验**（evidence / 原文定位），否则空壳和编造都能混过去。

## 遇到的报错 & 解决过程

| 报错 | 原因 | 怎么解决 |
| ---- | ---- | -------- |
| `ModuleNotFoundError: No module named 'rich'` | 用了系统 `python3` 而非 venv 里的解释器 | 先 `source .venv/bin/activate`，或直接用 `.venv/bin/python xxx.py` |
| GPG key 下载失败（Docker） | `curl ... \| echo "$PW" \| sudo -S tee` 串联，sudo 从 stdin 读密码时把 curl 数据抢走 | 先 `sudo -S -v` 建立凭据缓存，之后正常 sudo，不混在管道里 |
| `npm: command not found`（装完 nvm 后） | `nvm use` 的 PATH 变更在管道子 shell 里失效 | 脚本里直接用绝对路径调用 |
| 真实个人信息进了公开仓库 | 示例简历用了真实姓名/学校/雇主 | 改虚构数据 + `git filter-repo` 重写历史 + **删库重建**（强推清不掉孤儿提交） |

## 我自己的话解释「为什么不能直接 json.loads 模型的输出」

当没有指定 schema + pydantic 的时候，LLM 响应不一定是 JSON 格式。

补充：即使 Prompt 强制了 JSON，模型的输出也常带 markdown 围栏（```` ```json ````）、
前后闲聊、尾随逗号，`json.loads` 直接崩。实测这四种输入里 naive 只有「裸 JSON」能过。

## 我自己的话解释「json_mode 和 Pydantic 校验的分工」

> 提示：一个管语法层，一个管语义层。

- 一个保证输出的是 JSON 格式，可以被正确解析
- 另一个保证输出的 JSON **结构**符合预期，但**也不能保证 JSON 值完全正确**

修正：第二句严格说是「结构层」而非「语义层」——Pydantic 管的是类型/范围/枚举/必填，
不管这个字段的**值是否属实**。真正的语义（内容真实性）要靠 evidence 核验。

## Token / 成本

- 今日累计：Unit 5 验证跑约 20+ 次调用，约 ¥0.15（含 10 轮通过率测试 ¥0.0296、
  对抗实验、重试闭环各数次）

## 明天 / 下次的第一件事

- 回到 Unit 5 的收尾：`--adversarial` 再跑一次带 `--show-raw` 加深印象，
  然后决定是否补「evidence 来源核验」这个扩展练习。
- Unit 6 Streaming 材料已在 `unit06-streaming/` 备好，随时可开工。

---

## 一句话总结

三种模式在「**坏数据进来时谁先发现**」上完全不同：
`naive` 装聋作哑（null 也当成功）、`json_mode` 只管语法（是 JSON 就行）、
`schema` 当场报警（ValidationError 精确到字段）。
但三者都**不知道内容真假**——要治幻觉得靠 evidence 核验和评测集，不是靠 Pydantic。

## 面试可能的追问（自己先想）

1. **「你的抽取通过率 100%，怎么保证不是 Prompt 太强导致的？」**
   → 换抗干扰输入重测（对抗实验），并把「结构正确率」和「内容准确率」分开统计。

2. **「Pydantic 校验失败之后怎么办？」**
   → 见 `retry_extract.py`：把 ValidationError 原文塞回对话让模型修正，最多 N 轮，
   仍失败就明确报错，绝不带病通过。

3. **「为什么不用 response_format=json_object 就完事了？」**
   → `json_mode` 只保证 JSON 语法，不保证字段名/类型/范围。
   而且 DeepSeek 的 json_object 模式要求 Prompt 里**必须出现 "json" 字样**，
   否则会报错或返回非 JSON——这是官方文档特别标注的坑。
