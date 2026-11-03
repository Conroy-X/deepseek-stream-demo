#!/usr/bin/env bash
# Unit 21 端到端冒烟：真起服务，把 6 个案件和 XSS 渲染全打一遍。
set -u
UNIT_DIR="${UNIT_DIR:-$HOME/ai-journey/unit21-citation}"
export PATH="$HOME/.local/bin:$PATH"
cd "$UNIT_DIR" || { echo "找不到 $UNIT_DIR"; exit 1; }
PORT=8021
PASS=0
FAIL=0

ok()  { PASS=$((PASS+1)); echo "  [PASS] $1"; }
bad() { FAIL=$((FAIL+1)); echo "  [FAIL] $1  ${2:-}"; }

uv run uvicorn main:app --port "$PORT" --log-level error >/tmp/u21-smoke.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; wait $SRV 2>/dev/null' EXIT

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 0.5
done

if ! curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  bad "服务启动失败" "见 /tmp/u21-smoke.log"
  tail -5 /tmp/u21-smoke.log
  exit 1
fi
ok "服务已就绪（:$PORT）"

CODE=$(curl -s -o /tmp/u21-index.html -w '%{http_code}' "http://127.0.0.1:$PORT/")
[ "$CODE" = "200" ] && ok "GET / 返回 200" || bad "GET / 返回 $CODE"
grep -qi "<!doctype html\|<html" /tmp/u21-index.html && ok "首页是 HTML" || bad "首页不像 HTML"

H=$(curl -s "http://127.0.0.1:$PORT/health")
echo "$H" | grep -q '"sources": *4' && ok "/health 报告 4 条来源" || bad "/health 来源数不对" "$H"
echo "$H" | grep -q '"ngram": *6' && ok "/health 报告 NGRAM=6" || bad "/health 缺 ngram"
echo "$H" | grep -q 'xss' && ok "/health 列出 6 个案例" || bad "/health 缺案例列表"

# --- 6 个案例全跑一遍 ---
for CASE in good partial out_of_range fabricated year xss; do
  D=$(curl -s "http://127.0.0.1:$PORT/demo?case=$CASE")
  echo "$D" | grep -q '"coverage"' && ok "/demo?case=$CASE 返回报告" || bad "/demo?case=$CASE 失败" "$D"
done

# ok 字段的对照：good/year 应该 ok=true，其余 false
G=$(curl -s "http://127.0.0.1:$PORT/demo?case=good")
echo "$G" | grep -q '"ok": *true' && ok "good 案例 ok=true" || bad "good 案例 ok 不是 true"
P=$(curl -s "http://127.0.0.1:$PORT/demo?case=partial")
echo "$P" | grep -q '"ok": *true' && ok "partial 案例 ok=true（覆盖率 0.5 但无硬伤）" \
  || bad "partial 案例 ok 不是 true"
F=$(curl -s "http://127.0.0.1:$PORT/demo?case=fabricated")
echo "$F" | grep -q '"ok": *false' && ok "fabricated 案例 ok=false" || bad "fabricated 案例没被拦"

# --- 越界 ---
O=$(curl -s "http://127.0.0.1:$PORT/demo?case=out_of_range")
echo "$O" | grep -q '"out_of_range": *\[9\]' && ok "[9] 被标为越界" || bad "越界没检出来" "$O"

# --- [2026] 不是引用 ---
Y=$(curl -s "http://127.0.0.1:$PORT/demo?case=year")
echo "$Y" | grep -q '"out_of_range": *\[\]' && ok "[2026] 不产生越界告警（不当成引用）" || bad "年份被误判" "$Y"

# --- XSS 渲染 ---
R=$(curl -s -X POST "http://127.0.0.1:$PORT/render" -H 'Content-Type: application/json' \
    -d '{"answer":"正常一句。<img src=x onerror=alert(1)>[1]"}')
echo "$R" | grep -q '&lt;img' && ok "渲染结果里 <img> 被转义成 &lt;img" || bad "<img> 没被转义" "$R"
# 注意：响应里同时有 answer（原样回显）和 html（渲染结果），
# 只能在 html 字段里判断有没有可执行标签 —— 在 answer 里找永远能找到。
HTML=$(echo "$R" | python3 -c 'import json,sys; print(json.load(sys.stdin)["html"])')
echo "$HTML" | grep -q '<img src=x' && bad "渲染后的 HTML 里出现了可执行的 <img>（XSS）" \
  || ok "渲染后的 HTML 里没有裸 <img>"
echo "$HTML" | grep -q '<sup' && ok "合法的 [1] 仍被渲染成 <sup>（转义没伤到自己）" || bad "角标没生成"

# --- 越界引用的渲染 ---
R2=$(curl -s -X POST "http://127.0.0.1:$PORT/render" -H 'Content-Type: application/json' \
     -d '{"answer":"编的。[9]"}')
echo "$R2" | grep -q 'cite-bad' && ok "越界引用渲染成可见标记（不静默消失）" || bad "越界引用被吞掉" "$R2"

# --- 定位 ---
L=$(curl -s -X POST "http://127.0.0.1:$PORT/locate" -H 'Content-Type: application/json' \
    -d '{"snippet":"年假需提前 3 个工作日在系统提交申请。","idx":1}')
echo "$L" | grep -q '"start"' && ok "/locate 返回字符区间" || bad "/locate 失败" "$L"
L2=$(curl -s -X POST "http://127.0.0.1:$PORT/locate" -H 'Content-Type: application/json' \
     -d '{"snippet":"完全无关的句子内容在此","idx":1}')
echo "$L2" | grep -q '"found": *false' && ok "/locate 对无关片段返回 found=false（不是瞎猜）" \
  || bad "无关片段定位不对" "$L2"

# --- 来源 ---
S=$(curl -s "http://127.0.0.1:$PORT/sources")
SN=$(echo "$S" | grep -o '"idx":' | wc -l)
[ "$SN" -eq 4 ] && ok "/sources 返回 4 条原文" || bad "/sources 返回 $SN 条"

# --- 校验 ---
BAD1=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/check" \
       -H 'Content-Type: application/json' -d '{"answer":""}')
[ "$BAD1" = "422" ] && ok "空答案返回 422" || bad "空答案返回 $BAD1"

BAD2=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/demo?case=nope")
[ "$BAD2" = "404" ] && ok "未知案例返回 404" || bad "未知案例返回 $BAD2"

echo
echo "结果：$PASS 通过 / $FAIL 失败"
[ "$FAIL" -eq 0 ] || { echo "--- 服务日志尾部 ---"; tail -10 /tmp/u21-smoke.log; }
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
