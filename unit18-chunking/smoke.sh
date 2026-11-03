#!/usr/bin/env bash
# Unit 18 端到端冒烟：起服务 → 打真实请求 → 关服务
set -u
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/ai-journey/unit18-chunking" || exit 1

uv run uvicorn main:app --port 8018 --log-level warning > /tmp/unit18-uvicorn.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT

for _ in $(seq 1 40); do
  sleep 1
  curl -sf http://127.0.0.1:8018/health > /dev/null 2>&1 && break
done

echo "=== GET / （静态页） ==="
curl -s -o /tmp/u18-index.html -w "HTTP %{http_code}  %{size_download} bytes\n" http://127.0.0.1:8018/
grep -o "<title>[^<]*</title>" /tmp/u18-index.html

echo
echo "=== GET /health ==="
curl -s http://127.0.0.1:8018/health

echo
echo
echo "=== POST /chunk（recursive, size=160, overlap=30） ==="
curl -s -X POST http://127.0.0.1:8018/chunk \
  -H 'Content-Type: application/json' \
  -d '{"text":"年假：入职满一年 5 天。\n\n报销：出差结束后 10 个工作日内提交，超过 5000 元需总监审批。\n\n退款：签收后 7 天内可申请，3-5 个工作日原路退回。","strategy":"recursive","size":160,"overlap":30}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('块数=%d 每块字数=%s 覆盖=%s' % (len(d['chunks']), [c['chars'] for c in d['chunks']], d.get('coverage')))"

echo "=== POST /chunk 非法参数（overlap=size，应 400） ==="
curl -s -o /tmp/u18-bad.json -w "HTTP %{http_code}  " -X POST http://127.0.0.1:8018/chunk \
  -H 'Content-Type: application/json' \
  -d '{"text":"正文","strategy":"fixed","size":100,"overlap":100}'
head -c 120 /tmp/u18-bad.json

echo
echo
echo "=== POST /compare（三种策略 × 12 题） ==="
curl -s -X POST http://127.0.0.1:8018/compare \
  -H 'Content-Type: application/json' \
  -d '{"size":160,"overlap":30,"top_k":3}' \
  | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('size=%d overlap=%d top_k=%d' % (d['size'], d['overlap'], d['top_k']))
for r in d['rows']:
    print('  %-10s 块数=%-3d 平均字数=%-5.1f 命中=%2d/12 recall@3=%.2f' % (
        r['strategy'], r['chunks'], r['avg_chars'], r['hits'], r['recall@3']))
"

echo
echo "=== 服务日志尾部 ==="
tail -3 /tmp/unit18-uvicorn.log
