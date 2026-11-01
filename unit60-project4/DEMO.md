# Demo

## 1. 正常查询：销售额排名

```bash
curl -X POST http://127.0.0.1:8060/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"销售额排名","tenant":"t-acme"}'
```

输出片段：
```json
{"answer":"销售额排名：\n  c-001: ¥998","trace_id":"abc12345","final_sql":"SELECT customer_id, sum(amount) ... WHERE tenant_id = 't-acme' LIMIT 100","rounds":1}
```

## 2. 正常查询：订单状态

```bash
curl -X POST http://127.0.0.1:8060/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":"订单状态","tenant":"t-globex"}'
```

## 3. 攻击被拦：多语句注入

```bash
curl http://127.0.0.1:8060/api/attacks
```

输出片段：
```json
{"attacks":[{"label":"多语句","sql":"SELECT * FROM orders; DROP TABLE orders","blocked":true,"reason":"多语句"}]}
```

## 4. 攻击被拦：UPDATE

同样在 `/api/attacks` 里，UPDATE 被「只允许 SELECT」拦下。

## 5. 错误自愈

```bash
curl -X POST http://127.0.0.1:8060/api/ask \
  -H 'Content-Type: application/json' \
  -d '{"question":" nonexistent查询","tenant":"t-acme"}'
```

无法生成 SQL 时返回 `"无法生成 SQL"`，不会瞎编。