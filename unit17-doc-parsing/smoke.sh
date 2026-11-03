#!/usr/bin/env bash
# Unit 17 端到端冒烟：起服务 → 打真实请求（含 multipart 上传）→ 关服务
set -u
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/ai-journey/unit17-doc-parsing" || exit 1

uv run uvicorn main:app --port 8017 --log-level warning > /tmp/unit17-uvicorn.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT

for _ in $(seq 1 40); do
  sleep 1
  curl -sf http://127.0.0.1:8017/health > /dev/null 2>&1 && break
done

echo "=== GET / （静态页） ==="
curl -s -o /tmp/u17-index.html -w "HTTP %{http_code}  %{size_download} bytes\n" http://127.0.0.1:8017/
grep -o "<title>[^<]*</title>" /tmp/u17-index.html

echo
echo "=== GET /health ==="
curl -s http://127.0.0.1:8017/health

echo
echo
echo "=== GET /sample?name=员工手册.md ==="
curl -s "http://127.0.0.1:8017/sample?name=%E5%91%98%E5%B7%A5%E6%89%8B%E5%86%8C.md" \
  | head -c 320

echo
echo
echo "=== POST /parse-text （GBK 文本走内存） ==="
curl -s -X POST http://127.0.0.1:8017/parse-text \
  -H 'Content-Type: application/json' \
  -d '{"name":"a.txt","text":"退款政策：未发货订单可全额退款。"}' | head -c 260

echo
echo
echo "=== POST /parse 上传真实 PDF ==="
curl -s -X POST http://127.0.0.1:8017/parse -F "file=@fixtures/multi.pdf" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('kind=%s pages=%d chars=%d warnings=%s' % (d['kind'], len(d['pages']), d['chars'], d['warnings']))"

echo "=== POST /parse 上传伪装 PDF（应为 415） ==="
curl -s -o /tmp/u17-fake.json -w "HTTP %{http_code}  " -X POST http://127.0.0.1:8017/parse -F "file=@fixtures/fake.pdf"
cat /tmp/u17-fake.json | head -c 160

echo
echo
echo "=== POST /parse 上传 DOCX ==="
curl -s -X POST http://127.0.0.1:8017/parse -F "file=@fixtures/simple.docx" \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('kind=%s pages=%d chars=%d warnings=%s' % (d['kind'], len(d['pages']), d['chars'], d['warnings']))"

echo
echo "=== 未知样例（应 404） ==="
curl -s -o /dev/null -w "HTTP %{http_code}\n" "http://127.0.0.1:8017/sample?name=nope.md"

echo
echo "=== 服务日志尾部 ==="
tail -3 /tmp/unit17-uvicorn.log
