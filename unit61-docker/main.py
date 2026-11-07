"""Unit 61 · Docker：把一个 FastAPI 服务容器化，并静态校验容器化产物。

本单元没有新算法，学的是工程动作：把已有的服务装进容器、可重复构建、可一键起。
目标对象是根目录同款的最小服务（/health + /info），产出一套可直接 `docker compose up` 的产物。

自检是**离线静态校验**：不要求本机有 Docker，也能把 Dockerfile / .dockerignore /
docker-compose.yml 里的常见坑逐个查一遍。真机 `docker build` 属于 smoke，见 README。

静态校验覆盖的坑（都是面试/工作里真实翻车点）：
  1. Dockerfile 第一句必须 FROM（否则 build 报错）
  2. 不能用 root 跑（安全基线；被攻破就是容器内 root）
  3. 依赖安装要先 COPY requirements 再 pip install（利用层缓存，改代码不重装依赖）
  4. .dockerignore 必须排除 .env / .venv / logs（否则密钥和几百 MB 依赖进镜像）
  5. 健康检查要显式声明（HEALTHCHECK / docker-compose healthcheck）
  6. 端口要 EXPOSE 且与 compose 映射一致
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

HERE = Path(__file__).resolve().parent

app = FastAPI(title="Unit 61 · Docker", version="0.1.0")


@app.get("/health", summary="健康检查：容器编排与探针都打这里")
async def health() -> dict:
    return {"ok": True, "unit": 61, "service": "docker-demo"}


@app.get("/info", summary="运行时信息：证明容器内环境符合预期")
async def info() -> dict:
    return {
        "python": sys.version.split()[0],
        "cwd": str(Path.cwd()),
        "env_notes": "容器内不应出现 DEEPSEEK_API_KEY（.dockerignore 已排除 .env）",
        "has_key_in_image": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
    }


@app.get("/", include_in_schema=False)
async def index() -> JSONResponse:
    return JSONResponse({"hint": "看 /health /info，或 /docs"})


# ---------------------------------------------------------------------------
# 离线自检：静态校验容器化产物
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import yaml  # 校验 compose 用

    passed = failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        global passed, failed
        if ok:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            failed += 1
            print(f"  [FAIL] {name} {detail}")

    print("Unit 61 · Docker 静态自检\n")

    # ---------------- 目标服务本身 ----------------
    from fastapi.testclient import TestClient

    c = TestClient(app)
    r = c.get("/health")
    check("服务 /health 200", r.status_code == 200)
    check("health 返回 ok", r.json().get("ok") is True)
    r = c.get("/info")
    check("服务 /info 200", r.status_code == 200)
    check("info 含 python 版本", "python" in r.json())

    # ---------------- Dockerfile ----------------
    df_path = HERE / "Dockerfile"
    check("Dockerfile 存在", df_path.exists())
    if df_path.exists():
        df = df_path.read_text(encoding="utf-8")
        lines = [ln for ln in df.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        check("第一条有效指令是 FROM", lines[0].upper().startswith("FROM"), lines[0])
        check("有 USER 切非 root", any(ln.upper().startswith("USER ") for ln in lines))
        user_lines = [ln for ln in lines if ln.upper().startswith("USER ")]
        check("USER 不是 root", user_lines and "root" not in user_lines[-1].lower(), user_lines[-1] if user_lines else "无")
        check("先 COPY requirements 再 pip install", any("COPY" in ln and "requirements" in ln for ln in lines))
        check("有 EXPOSE 端口", any(ln.upper().startswith("EXPOSE ") for ln in lines))
        check("CMD 里带 uvicorn", any("uvicorn" in ln for ln in lines), "|".join(lines[-3:]))
        check("不 COPY .env 进镜像", not any(ln.strip().startswith("COPY") and ".env" in ln for ln in lines))
        # 层缓存顺序：pip install 之前的最后一行应是 COPY requirements
        idx_pip = next((i for i, ln in enumerate(lines) if "pip install" in ln), -1)
        idx_copy_req = next((i for i, ln in enumerate(lines) if "COPY" in ln and "requirements" in ln), -1)
        check("pip install 在 COPY requirements 之后（利用层缓存）",
              0 <= idx_copy_req < idx_pip, f"copy={idx_copy_req} pip={idx_pip}")

    # ---------------- .dockerignore ----------------
    di_path = HERE / ".dockerignore"
    check(".dockerignore 存在", di_path.exists())
    if di_path.exists():
        di = di_path.read_text(encoding="utf-8")
        for must in (".env", ".venv", "logs", "__pycache__", "*.db"):
            check(f".dockerignore 排除 {must}", must in di, f"缺 {must}")

    # ---------------- docker-compose.yml ----------------
    dc_path = HERE / "docker-compose.yml"
    check("docker-compose.yml 存在", dc_path.exists())
    if dc_path.exists():
        try:
            compose = yaml.safe_load(dc_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            check("compose 是合法 YAML", False, str(exc)[:120])
            compose = None
        if compose:
            svc = compose.get("services", {})
            check("有 services", bool(svc))
            first = next(iter(svc.values()), {})
            check("service 有 build 或 image", "build" in first or "image" in first)
            ports = first.get("ports", [])
            check("service 映射了端口", bool(ports))
            exposed = re.findall(r"EXPOSE\s+(\d+)", df) if df_path.exists() else []
            mapped = "".join(str(p) for p in ports)
            check("compose 端口与 EXPOSE 一致",
                  not exposed or any(e in mapped for e in exposed),
                  f"EXPOSE={exposed} ports={ports}")
            check("有健康检查", "healthcheck" in first or "healthcheck" in str(first.get("environment", "")))

    # ---------------- 密钥不落盘 ----------------
    env_example = HERE / ".env.example"
    check(".env.example 存在（不含真实 key）", env_example.exists())
    if env_example.exists():
        ex = env_example.read_text(encoding="utf-8")
        check("示例 key 是占位符", "sk-你的" in ex or "your" in ex.lower() or ex.strip().split("=")[-1].strip() in ("", '""'))

    print(f"\n结果：{passed} 通过 / {failed} 失败")
    sys.exit(1 if failed else 0)
