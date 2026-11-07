# 基础镜像固定小版本：可复现；slim 体积小
FROM python:3.13-slim

# 不装交互式前端、不写 pyc、实时输出日志（容器里 stdout 必须及时可读）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先复制依赖清单再安装：源码改动不会让 pip install 这层缓存失效
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 再复制应用代码
COPY main.py ./

# 非 root 运行：容器被攻破时不至于拿到 root
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

# 健康检查：让编排系统（k8s / compose）能自动发现坏实例
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
