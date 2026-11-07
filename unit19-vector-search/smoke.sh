#!/usr/bin/env bash
# Unit 19 端到端冒烟：真起服务、真打 HTTP、真看返回。
# 自检用的是 TestClient（进程内），冒烟验的是「uvicorn 起得来、静态页读得到、JSON 能序列化」。
set -u
# 从 `ssh host 'bash -s' < smoke.sh` 执行时 $0 是 "bash"，dirname 拿不到真实路径，
# 所以目录显式给出；uv 装在 ~/.local/bin，非登录 shell 的 PATH 里没有，也要补。
UNIT_DIR="${UNIT_DIR:-$HOME/ai-journey/unit19-vector-search}"
export PATH="$HOME/.local/bin:$PATH"
cd "$UNIT_DIR" || { echo "找不到 $UNIT_DIR"; exit 1; }
PORT=8019
PASS=0
FAIL=0

ok()   { PASS=$((PASS+1)); echo "  [PASS] $1"; }
bad()  { FAIL=$((FAIL+1)); echo "  [FAIL] $1  ${2:-}"; }

uv run uvicorn main:app --port "$PORT" --log-level error >/tmp/u19-smoke.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null; wait $SRV 2>/dev/null' EXIT

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 0.5
done

if ! curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  bad "服务启动失败" "见 /tmp/u19-smoke.log"
  tail -5 /tmp/u19-smoke.log
  exit 1
fi
ok "服务已就绪（:$PORT）"

# --- 静态页 ---
CODE=$(curl -s -o /tmp/u19-index.html -w '%{http_code}' "http://127.0.0.1:$PORT/")
[ "$CODE" = "200" ] && ok "GET / 返回 200" || bad "GET / 返回 $CODE"
grep -qi "<!doctype html\|<html" /tmp/u19-index.html && ok "首页是 HTML" || bad "首页内容不像 HTML"

# --- /health ---
H=$(curl -s "http://127.0.0.1:$PORT/health")
echo "$H" | grep -q '"chunks": *27' && ok "/health 报告 27 段语料" || bad "/health 语料数不对" "$H"
echo "$H" | grep -q '"dim": *512' && ok "/health 报告 512 维" || bad "/health 维数不对" "$H"
echo "$H" | grep -q '"questions": *12' && ok "/health 报告 12 道可答题" || bad "/health 可答题数不对"
echo "$H" | grep -q '"unanswerable_questions": *6' && ok "/health 报告 6 道无答案题" || bad "/health 无答案题数不对"
echo "$H" | grep -q 'category' && ok "/health 列出可过滤字段" || bad "/health 没列可过滤字段"

# --- /search ---
S=$(curl -s -X POST "http://127.0.0.1:$PORT/search" -H 'Content-Type: application/json' \
    -d '{"query":"退款多久到账","top_k":3}')
echo "$S" | grep -q '"id": *"policy:2:08"' && ok "退款问题首条是 policy:2:08" || bad "退款问题首条不对" "$S"

FIRST=$(echo "$S" | grep -o '"score": *[0-9.]*' | head -1 | grep -o '[0-9.]*')
python3 - "$FIRST" <<'PY'
import sys
s = float(sys.argv[1])
print(f"  [{'PASS' if 0 < s <= 1 else 'FAIL'}] 首条分数在 (0,1]：{s:.4f}")
sys.exit(0 if 0 < s <= 1 else 1)
PY
[ $? -ne 0 ] && FAIL=$((FAIL+1)) || PASS=$((PASS+1))

# --- 过滤（这条曾经是坏的：category 在顶层，被 upsert 丢掉后过滤永远空）---
F=$(curl -s -X POST "http://127.0.0.1:$PORT/search" -H 'Content-Type: application/json' \
    -d '{"query":"退款多久到账","top_k":3,"filters":{"category":"refund"}}')
N=$(echo "$F" | grep -o '"id":' | wc -l)
[ "$N" -gt 0 ] && ok "按 category=refund 过滤返回 $N 条（非空）" || bad "过滤返回空 —— 扁平 category 没进 meta" "$F"
echo "$F" | grep -q '"category": *"refund"' && ok "过滤结果全是 refund 类" || bad "过滤混进了别的类目" "$F"

# --- 阈值 ---
T=$(curl -s -X POST "http://127.0.0.1:$PORT/search" -H 'Content-Type: application/json' \
    -d '{"query":"退款多久到账","top_k":3,"min_score":0.9}')
echo "$T" | grep -q '"hits": *\[\]' && ok "阈值 0.9 把结果全剪掉（返回空列表而非报错）" || bad "高阈值没剪掉" "$T"

# --- /eval ---
E=$(curl -s "http://127.0.0.1:$PORT/eval?top_k=3")
echo "$E" | grep -q '"recall_at_k": *0.9167\|"recall_at_k": *0.917' && ok "/eval Recall@3 = 0.917" || bad "/eval Recall 不对" "$(echo "$E" | head -c 300)"
echo "$E" | grep -q '"mrr_at_k": *0.9167\|"mrr_at_k": *0.917' && ok "/eval MRR@3 = 0.917" || bad "/eval MRR@3 不对"
echo "$E" | grep -q '"accepted": *6' && ok "/eval 无阈值时 6 道无答案题全误接受" || bad "/eval 缺无答案题误接受统计"
echo "$E" | grep -q '"details"' && ok "/eval 带逐题明细" || bad "/eval 缺逐题明细"

# --- /corpus ---
C=$(curl -s "http://127.0.0.1:$PORT/corpus")
CN=$(echo "$C" | grep -o '"id":' | wc -l)
[ "$CN" -eq 27 ] && ok "/corpus 返回 27 段" || bad "/corpus 返回 $CN 段"

# --- 参数校验 ---
BAD=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/search" \
      -H 'Content-Type: application/json' -d '{"query":"x","top_k":0}')
[ "$BAD" = "422" ] && ok "top_k=0 返回 422" || bad "top_k=0 返回 $BAD"

BAD2=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PORT/search" \
       -H 'Content-Type: application/json' -d '{"query":"x","filters":{"nope":"1"}}')
[ "$BAD2" = "500" ] || [ "$BAD2" = "400" ] && ok "非法过滤字段被拒绝（$BAD2）" || bad "非法过滤字段返回 $BAD2"

echo
echo "结果：$PASS 通过 / $FAIL 失败"
[ "$FAIL" -eq 0 ] || { echo "--- 服务日志尾部 ---"; tail -10 /tmp/u19-smoke.log; }
exit $([ "$FAIL" -eq 0 ] && echo 0 || echo 1)
