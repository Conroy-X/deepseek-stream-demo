#!/usr/bin/env bash
# Unit 20 端到端冒烟：真起服务、真问两句话、真查 trace。
# 前提：没有 API key（有 key 会走 api 模式并真实计费，脚本会先提示）。
set -u
UNIT_DIR="${UNIT_DIR:-$HOME/ai-journey/unit20-basic-rag}"
export PATH="$HOME/.local/bin:$PATH"
cd "$UNIT_DIR" || { echo "找不到 $UNIT_DIR"; exit 1; }
PORT=8020
PASS=0
FAIL=0

ok()  { PASS=$((PASS+1)); echo "  [PASS] $1"; }
bad() { FAIL=$((FAIL+1)); echo "  [FAIL] $1  ${2:-}"; }

uv run uvicorn main:app --port "$PORT" --log-level error >/tmp/u20-smoke.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; wait $SRV 2>/dev/null' EXIT

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 0.5
done

if ! curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  bad "服务启动失败" "见 /tmp/u20-smoke.log"
  tail -5 /tmp/u20-smoke.log
  exit 1
fi
ok "服务已就绪（:$PORT）"

# --- 静态页 ---
CODE=$(curl -s -o /tmp/u20-index.html -w '%{http_code}' "http://127.0.0.1:$PORT/")
[ "$CODE" = "200" ] && ok "GET / 返回 200" || bad "GET / 返回 $CODE"
grep -qi "<!doctype html\|<html" /tmp/u20-index.html && ok "首页是 HTML" || bad "首页内容不像 HTML"

# --- /health ---
H=$(curl -s "http://127.0.0.1:$PORT/health")
MODE=$(echo "$H" | grep -o '"mode": *"[a-z]*"' | grep -o '"[a-z]*"$' | tr -d '"')
echo "$H" | grep -q '"mode"' && ok "/health 报告当前模式（$MODE）" || bad "/health 没报模式" "$H"
[ "$MODE" = "extractive" ] && ok "未配 key → 走离线 extractive（零成本）" \
  || echo "  [INFO] 当前是 $MODE 模式，冒烟会真实调用 API 并计费"
echo "$H" | grep -q '"chunks": *16\|"corpus": *16\|"docs": *16' && ok "/health 报告 16 段语料" \
  || echo "  [INFO] /health 语料字段名不是 chunks/corpus/docs：$(echo "$H" | head -c 160)"

# --- 相关问题：该答 ---
A=$(curl -s -X POST "http://127.0.0.1:$PORT/ask" -H 'Content-Type: application/json' \
    -d '{"question":"退款多久到账"}')
echo "$A" | grep -q '"refused": *false' && ok "相关问题 refused=false" || bad "相关问题被判拒答" "$A"
echo "$A" | grep -q '\[1\]' && ok "答案带 [1] 编号" || bad "答案没有编号" "$A"
echo "$A" | grep -q '"trace_id"' && ok "返回 trace_id（可回溯）" || bad "没有 trace_id"
TID=$(echo "$A" | grep -o '"trace_id": *"[^"]*"' | grep -o '[^"]*"$' | tr -d '"')

# --- 无关问题：该拒 ---
B=$(curl -s -X POST "http://127.0.0.1:$PORT/ask" -H 'Content-Type: application/json' \
    -d '{"question":"今天天气怎么样"}')
echo "$B" | grep -q '"refused": *true' && ok "无关问题 refused=true（拒答）" || bad "无关问题没拒答" "$B"
echo "$B" | grep -q '资料里没有相关内容' && ok "拒答是固定话术（可断言）" || bad "拒答话术不对" "$B"

# --- 阈值调高：相关问题也被拒 ---
C=$(curl -s -X POST "http://127.0.0.1:$PORT/ask" -H 'Content-Type: application/json' \
    -d '{"question":"退款多久到账","threshold":0.3}')
echo "$C" | grep -q '"refused": *true' && ok "阈值 0.3 → 相关问题变拒答（阈值在生成前生效）" \
  || bad "阈值调高没拦住" "$C"

# --- 阈值调 0：无关问题也开口 ---
D=$(curl -s -X POST "http://127.0.0.1:$PORT/ask" -H 'Content-Type: application/json' \
    -d '{"question":"今天天气怎么样","threshold":0}')
echo "$D" | grep -q '"refused": *false' && ok "阈值 0 → 无关问题也说起了话（拒答确实来自阈值）" \
  || bad "阈值 0 仍拒答" "$D"

# --- trace 回查 ---
if [ -n "$TID" ]; then
  T=$(curl -s "http://127.0.0.1:$PORT/trace/$TID")
  echo "$T" | grep -q '"prompt"' && ok "trace 里存了 prompt" || bad "trace 缺 prompt"
  echo "$T" | grep -q '"hits"' && ok "trace 里存了 hits" || bad "trace 缺 hits"
  echo "$T" | grep -q 'handbook.md\|policy.md' && ok "trace 的 hits 带来源文件名" || bad "hits 缺来源"
else
  bad "拿不到 trace_id，跳过 trace 检查"
fi

L=$(curl -s "http://127.0.0.1:$PORT/traces?limit=5")
echo "$L" | grep -q '"question"' && ok "/traces 能列出历史记录" || bad "/traces 是空的" "$L"

# --- 参数校验 ---
E=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/ask" \
    -H 'Content-Type: application/json' -d '{"question":""}')
[ "$E" = "422" ] && ok "空问题返回 422" || bad "空问题返回 $E"

F=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/ask" \
    -H 'Content-Type: application/json' -d '{"question":"x","threshold":2}')
[ "$F" = "422" ] && ok "threshold 越界返回 422" || bad "threshold 越界返回 $F"

echo
echo "结果：$PASS 通过 / $FAIL 失败"
[ "$FAIL" -eq 0 ] || { echo "--- 服务日志尾部 ---"; tail -10 /tmp/u20-smoke.log; }
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
