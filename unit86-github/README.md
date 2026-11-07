# Unit 86 · GitHub 整理：仓库卫生检查

> 目标：**GitHub 是技术简历的第二页。面试官点进来 30 秒就决定要不要往下看。**

---

## 一、五项检查

| 检查 | 看什么 | 不通过的后果 |
| --- | --- | --- |
| **README** | 能不能 30 秒看懂、有没有「怎么跑」 | 不知道这项目干什么 |
| **.gitignore** | .env / .venv / *.db 有没有挡住 | 密钥进库、仓库几百 MB |
| **敏感文件** | 目录里是否真有 .env | 直接泄露凭据 |
| **密钥扫描** | 代码/文档里的 sk- / ghp_ | 同上，且历史 commit 也删不掉 |
| **commit 规范** | feat/fix/docs 前缀 | 看不出改了什么 |

## 二、commit 规范（Conventional Commits）

| 前缀 | 用在哪 |
| --- | --- |
| `feat` | 新功能 / 新单元 |
| `fix` | 修 bug |
| `docs` | 只改文档 |
| `chore` | 杂活（依赖、配置） |
| `refactor` | 重构（行为不变） |
| `test` | 测试 |

格式：`type(scope): 一句话说明`，例如 `fix(unit61): 修 compose 密码注入`。

## 三、怎么跑

```bash
cd ~/ai-journey/unit86-github
uv sync
uv run python main.py            # 离线自检（用它检查自己）

uv run uvicorn main:app --port 8086
curl "http://127.0.0.1:8086/audit"                                  # 检查本目录
curl "http://127.0.0.1:8086/audit/commits?messages=feat:+新增|fix:+修bug"
```

## 四、检查自己的项目

把 `audit_repo()` 指向任意目录即可：

```python
from main import audit_repo
print(audit_repo("~/ai-journey/unit61-docker"))
```

## 五、完成标准

- [ ] `uv run python main.py` 自检全过
- [ ] 用本单元检查 `ai-journey` 根目录，score ≥ 80
- [ ] 自己的仓库有 README + .gitignore + 无 .env
- [ ] 最近 10 条 commit 里规范率 ≥ 80%
- [ ] 能说出「密钥进了 commit 为什么删文件也没用」

## 六、为什么密钥删了也没用

```
git rm .env          ← 只是删了「当前版本」的文件
git commit           ← 历史里那一版还在
```

密钥留在历史 commit 里，任何人 `git log -p` 都能翻出来。真正的处理：
**立刻吊销密钥** + 重写历史（`git filter-repo`）+ 强制推送 + 通知协作者重新克隆。

## 七、面试追问

**Q: 面试官会看 GitHub 的什么？**
→ 三眼：README（做什么）→ 目录结构（工程能力）→ commit 历史（习惯）。
代码细节反而看得少，因为看不完。所以门面必须清楚。

**Q: 仓库多好还是少好？**
→ 少而精。2–3 个能讲透的项目，胜过 20 个半成品 fork。
fork 很多不会加分，只显得没自己的东西。

**Q: pinned repositories 放什么？**
→ 最能代表你的 3–6 个：主项目、有难度的、有文档的、有 Demo 的。

---

## 八、资料

- [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)
- [Shields.io 徽章](https://shields.io/)
- [git filter-repo 清历史](https://github.com/newren/git-filter-repo)
- [Unit 85 简历校验器](../unit85-resume/)