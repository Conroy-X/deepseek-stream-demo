#!/usr/bin/env bash
# ============================================================
#  AI Journey · 一键自检
#
#  做两件事，都不花钱、不调用模型：
#    1. 环境可用性：uv / python / node / git hook / Postgres / Redis / Docker / .env
#    2. 每个已学单元的离线自检：跑它自己的 selfcheck，汇总成一张表
#
#  用法：
#      bash 00-setup/healthcheck.sh            # 只看汇总
#      bash 00-setup/healthcheck.sh -v         # 失败项打印完整输出
#      bash 00-setup/healthcheck.sh -v -a      # 全部打印完整输出
#
#  退出码：0 = 全绿；1 = 有失败项（可以直接拿去做 CI / pre-push）
#
#  设计约束（与仓库约定一致）：
#    · 只跑「不联网、不花钱」的自检，绝不偷偷调用 API
#    · 每个单元的 .venv 各自隔离，不共享、不重装
#    · 完整输出落在 logs/（已 gitignore），终端只给结论
# ============================================================
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
ROOT=$(pwd)
STAMP=$(date +%Y%m%d-%H%M%S)
LOG_DIR="$ROOT/logs"
LOG="$LOG_DIR/healthcheck-$STAMP.log"
mkdir -p "$LOG_DIR" && : > "$LOG"

VERBOSE=0
SHOW_ALL=0
for arg in "$@"; do
  case "$arg" in
    -v|--verbose) VERBOSE=1 ;;
    -a|--all)     VERBOSE=1; SHOW_ALL=1 ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "未知参数：$arg（用 -h 看用法）" >&2; exit 2 ;;
  esac
done

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
YELLOW=$'\033[33m'; RESET=$'\033[0m'
if [ ! -t 1 ]; then BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; RESET=""; fi

PASS=0; FAIL=0; SKIP=0
FAILED_NAMES=()

hr()    { printf '%s\n' "------------------------------------------------------------"; }
head_() { printf '\n%s%s%s\n' "$BOLD" "$1" "$RESET"; }

# ---------------------------------------------------------------- 单元表
# 名称 | 在单元目录里执行的命令 | 期望（只用于展示）
# 有 pyproject.toml 的单元统一写 __PY__（脚本会展开成 uv run --no-sync python），
# 与各单元 README 的启动方式保持一致；Node 单元写 __NODE__。
UNIT_NAMES=(); UNIT_CMDS=(); UNIT_EXPECT=()
add_unit() {
  UNIT_NAMES+=("$1"); UNIT_CMDS+=("$2"); UNIT_EXPECT+=("$3")
}
add_unit unit01-fastapi           "__PY__ main.py"            "18 项"
add_unit unit02-llm-basics        "__PY__ demo.py --offline"  "离线 token 计数"
add_unit unit03-cli-chat          "__PY__ selfcheck.py"       "27 项"
add_unit unit04-prompt            "__PY__ selfcheck.py"       "53 项"
add_unit unit05-structured        "__PY__ models.py"          "Pydantic 校验演示"
add_unit unit06-streaming         "__PY__ main.py"            "16 项"
add_unit unit07-web-chat          "__PY__ main.py"            "15 项"
add_unit unit08-chat-ui      "__NODE__ test-markdown.mjs"          "32 项"
add_unit unit09-conversations     "__PY__ main.py"           "20 项"
add_unit unit10-context           "__PY__ test_context.py"    "31 项"

# node 走 nvm，非交互 shell 里 PATH 可能没有它
NODE_BIN=$(command -v node 2>/dev/null || true)
if [ -z "$NODE_BIN" ]; then
  NODE_BIN=$(ls -1 "$HOME"/.nvm/versions/node/*/bin/node 2>/dev/null | tail -1)
fi
NODE_BIN=${NODE_BIN:-node}

# Python 侧启动方式：有 pyproject.toml 的单元统一走 uv run（与 README 一致），
# 只有 requirements.txt 的老单元仍直调各自 .venv。
#   --no-sync  : 不联网、不改环境，保证自检可离线跑
#   uv 不在 PATH 时（非交互 shell 常见）退回 ~/.local/bin/uv，再不行退回 .venv
UV_BIN=$(command -v uv 2>/dev/null || true)
if [ -z "$UV_BIN" ] && [ -x "$HOME/.local/bin/uv" ]; then UV_BIN="$HOME/.local/bin/uv"; fi
if [ -n "$UV_BIN" ]; then
  PY_RUN="$UV_BIN run --no-sync python"
else
  PY_RUN=".venv/bin/python"
fi

# ---------------------------------------------------------------- 环境
SKIPPED_ENV=()
env_check() {  # env_check "标题" "命令" "缺失时的提示"
  local title="$1" cmd="$2" hint="${3:-}"
  if out=$(eval "$cmd" 2>&1); then
    printf '  ✅ %-24s %s\n' "$title" "${out%%$'\n'*}"
    PASS=$((PASS+1))
  else
    printf '  ⚠️  %-24s %s\n' "$title" "${hint:-未就绪}"
    SKIP=$((SKIP+1)); SKIPPED_ENV+=("$title")
  fi
}

head_ "一、环境"
env_check "uv"       "\"\$UV_BIN\" --version | awk '{print \$1\" \"\$2}'" "没装 uv：curl -LsSf https://astral.sh/uv/install.sh | sh（或装到 ~/.local/bin）"
env_check "node"     "$NODE_BIN --version"                      "没装 node 或 nvm 未生效（Unit 8 的测试需要）"
env_check "git hook" "test \"\$(git config --get core.hooksPath)\" = .githooks && echo 'core.hooksPath=.githooks'" "没启用：git config core.hooksPath .githooks（铁律 #1 的守门员）"
env_check "Postgres" "psql 'postgresql://rik:rik123@127.0.0.1:5432/ai_journey' -tAc 'select version();' | cut -d' ' -f1-2" "连不上：Unit 11 要用"
env_check "pgvector" "psql 'postgresql://rik:rik123@127.0.0.1:5432/ai_journey' -tAc \"select 'vector ' || extversion from pg_extension where extname='vector';\"" "Unit 15+ 的 RAG 要用"
env_check "Redis"    "redis-cli ping"                           "Unit 62 才需要，现在没有也不影响"
env_check "Docker"   "docker version --format '{{.Server.Version}}' 2>/dev/null" "想用容器跑依赖时才需要"

# .env 检查：只报「有没有、是不是占位」，绝不打印内容
ENV_HAVE=0; ENV_MISS=(); ENV_PLACEHOLDER=()
for f in unit*/.env; do
  [ -f "$f" ] || continue
  v=$(grep -E '^DEEPSEEK_API_KEY=' "$f" 2>/dev/null | head -1 | cut -d= -f2- | tr -d ' "')
  if [ -z "$v" ] || [[ "$v" == *你的* ]]; then
    ENV_PLACEHOLDER+=("${f%.env}")
  else
    ENV_HAVE=$((ENV_HAVE+1))
  fi
done
for d in unit*/; do
  d=${d%/}
  [[ "$d" =~ ^unit[0-9]+ ]] || continue
  [ -f "$d/.env" ] || ENV_MISS+=("$d")
done

if [ "$ENV_HAVE" -gt 0 ]; then
  printf '  ✅ %-24s %s 个单元已配置真实 Key\n' ".env" "$ENV_HAVE"
  PASS=$((PASS+1))
else
  printf '  ⚠️  %-24s 还没有任何单元配好 Key（离线自检不受影响）\n' ".env"
  SKIP=$((SKIP+1))
fi
if [ "${#ENV_PLACEHOLDER[@]}" -gt 0 ]; then
  printf '  ⚠️  %-24s %s 还是占位值\n' ".env 占位" "${ENV_PLACEHOLDER[*]}"
fi

# ---------------------------------------------------------------- 单元
head_ "二、单元自检（不联网、不花钱）"
hr

for i in "${!UNIT_NAMES[@]}"; do
  name=${UNIT_NAMES[$i]}
  raw_cmd=${UNIT_CMDS[$i]}
  cmd=${raw_cmd/__NODE__/$NODE_BIN}
  cmd=${cmd/__PY__/$PY_RUN}
  expect=${UNIT_EXPECT[$i]}
  unit_log="$LOG_DIR/healthcheck-$STAMP.$name.log"

  if [ ! -d "$name" ]; then
    printf '  ⏭  %-24s %-9s 目录不存在\n' "$name" "-"
    SKIP=$((SKIP+1)); continue
  fi
  # 走 Python 的单元必须先有 .venv（node 单元不需要）
  if [ "${raw_cmd#__PY__}" != "$raw_cmd" ] || [ "${cmd#.venv/}" != "$cmd" ]; then
    if [ ! -x "$name/.venv/bin/python" ]; then
      printf '  ⏭  %-24s %-9s 缺 .venv（cd %s && uv sync）\n' \
        "$name" "-" "$name"
      SKIP=$((SKIP+1)); continue
    fi
  fi

  start=$(date +%s%N)
  ( cd "$name" && eval "$cmd" ) >"$unit_log" 2>&1
  rc=$?
  ms=$(( ($(date +%s%N) - start) / 1000000 ))
  { echo "### $name :: $cmd"; cat "$unit_log"; echo; } >>"$LOG"

  summary=$(grep -oE '结果：.*' "$unit_log" | tail -1 | sed 's/\x1b\[[0-9;]*m//g')
  [ -z "$summary" ] && summary="运行成功（期望：$expect）"

  if [ $rc -eq 0 ]; then
    printf '  ✅ %-24s %-9s %s\n' "$name" "${ms}ms" "$summary"
    PASS=$((PASS+1))
    if [ $SHOW_ALL -eq 1 ]; then sed 's/^/      /' "$unit_log"; fi
  else
    printf '  ❌ %-24s %-9s 退出码 %s\n' "$name" "${ms}ms" "$rc"
    FAIL=$((FAIL+1)); FAILED_NAMES+=("$name")
    if [ $VERBOSE -eq 1 ]; then
      sed 's/^/      /' "$unit_log" | tail -30
    else
      printf '      （加 -v 看输出，或看 %s）\n' "$unit_log"
    fi
  fi
done

# ---------------------------------------------------------------- 汇总
hr
head_ "三、汇总"
printf '  通过 %d · 失败 %d · 跳过 %d\n' "$PASS" "$FAIL" "$SKIP"
if [ "${#FAILED_NAMES[@]}" -gt 0 ]; then
  printf '  失败单元：%s\n' "${FAILED_NAMES[*]}"
fi
if [ "${#SKIPPED_ENV[@]}" -gt 0 ]; then
  printf '  环境未就绪：%s\n' "${SKIPPED_ENV[*]}"
fi
printf '  单元输出汇总：%s\n' "$LOG"
printf '  单单元日志：%s/healthcheck-%s.<unit>.log\n' "$LOG_DIR" "$STAMP"

if [ $FAIL -eq 0 ]; then exit 0; else exit 1; fi
