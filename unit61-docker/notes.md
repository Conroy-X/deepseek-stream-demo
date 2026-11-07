# Unit 61 笔记 · Docker 容器化

> 边做边填。模拟面试的复习材料。

## 本次做了什么

- 把最小 FastAPI 服务（/health + /info）容器化：Dockerfile + .dockerignore + docker-compose.yml
- main.py 自带 26 项离线静态自检（不需要 Docker 也能查常见坑）

## 六个必须记住的坑

| 坑 | 后果 | 自检里怎么查 |
| --- | --- | --- |
| 第一句不是 FROM | build 报错 | 检查首个有效指令 |
| 容器内 root | 被攻破拿到 root | 检查 USER 指令且非 root |
| COPY 顺序错 | 改代码重装依赖 | 检查 pip install 在 COPY requirements 之后 |
| .dockerignore 漏 .env | 密钥进镜像层 | 检查排除项 |
| 无 HEALTHCHECK | 坏实例不被发现 | 检查 HEALTHCHECK / compose healthcheck |
| EXPOSE 与映射不符 | 外部连不上 | 比对两者端口 |

## 层缓存的关键顺序

```dockerfile
COPY requirements.txt ./   # 1. 先复制依赖清单
RUN pip install -r ...     # 2. 装依赖（这层被缓存）
COPY main.py ./            # 3. 再复制源码
```

改 `main.py` 时，前面两层缓存命中，只重建最后一层——秒级构建。
顺序反过来，改一行代码就要重跑 pip install。

## 遇到的报错 & 解决过程

| 报错 | 原因 | 怎么解决 |
| ---- | ---- | -------- |
| docker build 拉基础镜像卡住 | Docker daemon 没走代理 | 配 `/etc/systemd/system/docker.service.d/http-proxy.conf` 指向宿主 7897 |
| docker build 卡在 pip install | 容器内 pip 直连 PyPI 极慢 | Dockerfile 里 pip install 加 `-i https://mirrors.ustc.edu.cn/pypi/simple` |

配完后实测：build 76 秒、容器 7 秒 healthy、/health 200。

## 我自己的话解释「为什么密钥不能 COPY 进镜像」

（待填：提示——镜像层是历史记录，docker history 能看到每一层的内容；
删掉文件不等于删掉层）

## 我自己的话解释「HEALTHCHECK 的作用」

（待填：提示——健康检查把「进程活着」和「服务可用」区分开）

## 完成标准对照

- [ ] `uv run python main.py` → 26 通过 / 0 失败
- [ ] `docker compose up --build -d` 后 /health 200
- [ ] `docker compose ps` 显示 healthy
- [ ] 能解释层缓存顺序
- [ ] 能解释 .dockerignore 漏 .env 的后果

## 一句话总结

（待填）

## 面试追问

1. **「镜像怎么瘦身？」** → 多阶段构建 / slim 基础镜像 / 合并 RUN 层 / PIP_NO_CACHE_DIR
2. **「密钥怎么进容器？」** → BuildKit --secret（构建期）、环境变量或 secret 挂载（运行期），不 COPY
3. **「HEALTHCHECK 挂了怎么办？」** → 先暴露 unhealthy 再定位，别盲目 restart 掩盖问题
