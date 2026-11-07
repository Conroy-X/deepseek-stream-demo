# Unit 61 · Docker：容器化

> 目标：把服务装进容器，**可重复构建、一键起、可被健康检查**。

---

## 一、学什么

没有新算法。学的是工程动作：

```
源码 + 依赖清单 → Dockerfile → 镜像 → docker compose 一键起
```

本单元把根目录同款的最小 FastAPI 服务（`/health` + `/info`）容器化，
产出一套标准产物：`Dockerfile` / `.dockerignore` / `docker-compose.yml`。

## 二、怎么跑

### 1. 离线自检（不需要 Docker）

```bash
cd ~/ai-journey/unit61-docker
uv sync
uv run python main.py
```

27 项静态校验：Dockerfile 语法、非 root、层缓存顺序、.dockerignore 排除项、
compose 端口一致性、健康检查声明、密钥不进镜像。

### 2. 真机构建 + 起容器（需要 Docker，本机已装）

```bash
docker compose up --build -d
curl http://127.0.0.1:8061/health
docker compose ps          # 看 STATUS 列是否 healthy
docker compose down
```

> 本机实测（2026-11-07）：`docker compose up --build -d` 全程 **76 秒**，
> 容器 7 秒 healthy，`/health` 返回 200。
> 两个网络前提（本机已配好，换机器要重配）：
> 1. Docker daemon 走宿主代理 `http://172.19.64.1:7897`（拉基础镜像快）
> 2. Dockerfile 里 pip install 用 USTC 镜像（否则容器内 pip 直连 PyPI 极慢）

```bash
docker compose up --build -d
curl http://127.0.0.1:8061/health
docker compose ps          # 看 STATUS 列是否 healthy
docker compose down
```

## 三、六个必须知道的坑（都在自检里查）

| # | 坑 | 后果 | 正确做法 |
| --- | --- | --- | --- |
| 1 | Dockerfile 第一句不是 FROM | 构建直接报错 | 首个有效指令必须 FROM |
| 2 | 容器内 root 运行 | 被攻破 = 容器内 root | `USER appuser` 切非 root |
| 3 | 先 COPY 源码再 pip install | 改一行代码就重装全部依赖 | 先 COPY requirements → pip install → 再 COPY 源码 |
| 4 | .dockerignore 漏掉 .env | **密钥进镜像**，`docker history` 都能挖出来 | .env / .venv / logs 必须排除 |
| 5 | 没有 HEALTHCHECK | 坏实例不会被自动发现 | 显式 HEALTHCHECK / compose healthcheck |
| 6 | EXPOSE 与 compose 端口对不上 | 容器起得来，外面连不上 | 两者一致 |
| 7 | 容器内 pip 直连 PyPI | 构建卡在 pip install（国内直连极慢） | pip install 加 `-i USTC 镜像` |

## 四、文件说明

| 文件 | 作用 |
| --- | --- |
| `main.py` | 目标服务 + 26 项静态自检 |
| `Dockerfile` | 构建指令（非 root / 分层缓存 / 健康检查） |
| `.dockerignore` | 镜像构建时排除的路径（密钥、venv、日志） |
| `docker-compose.yml` | 一键起：端口 8061→8000 + 健康检查 + 重启策略 |
| `requirements.txt` / `pyproject.toml` | 依赖声明 |

## 五、完成标准

- [ ] `uv run python main.py` → 27 通过 / 0 失败
- [ ] `docker compose up --build -d` 后 `/health` 返回 200
- [ ] `docker compose ps` 显示 healthy
- [ ] 能说出「为什么先 COPY requirements 再 pip install」
- [ ] 能说出「.dockerignore 漏 .env 的后果」
- [ ] 能解释 HEALTHCHECK 和 compose healthcheck 的区别

## 六、面试追问

**Q: 镜像怎么瘦身？**
→ 多阶段构建（builder 装依赖，runtime 只复制产物）、slim/alpine 基础镜像、
`PIP_NO_CACHE_DIR`、合并 RUN 层。

**Q: 密钥怎么进容器才安全？**
→ 构建期用 `--secret`（BuildKit），运行期用环境变量/secret 挂载，
**绝不 COPY 进镜像**（镜像层不可逆）。

**Q: HEALTHCHECK 挂了会怎样？**
→ 编排系统标记 unhealthy；compose 配了 `restart: unless-stopped` 会重启，
但更推荐让它 unhealthy 暴露出来而不是盲目重启掩盖问题。

---

## 七、资料

- [Dockerfile 最佳实践](https://docs.docker.com/build/building/best-practices/)
- [Docker 官方 Get Started](https://docs.docker.com/get-started/)
- [Python 镜像最佳实践](https://docs.docker.com/language/python/)
- [docker compose 参考](https://docs.docker.com/compose/)
- [.dockerignore 参考](https://docs.docker.com/build/concepts/context/#dockerignore-files)
