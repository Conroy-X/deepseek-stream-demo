#!/usr/bin/env node
/**
 * Unit 8 · markdown.js 的离线测试（不需要浏览器）
 * 用法：node test-markdown.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const src = fs.readFileSync(path.join(__dirname, "static/markdown.js"), "utf8");
// 写成临时 mjs 再 import
const tmp = path.join(__dirname, "__md_test.mjs");
fs.writeFileSync(tmp, src, "utf8");

const mod = await import(`file://${tmp}?t=${Date.now()}`);
const { renderMarkdown, escapeHtml } = mod;

let pass = 0, fail = 0;
function check(name, cond, detail = "") {
  if (cond) { pass++; console.log(`  [PASS] ${name}`); }
  else { fail++; console.log(`  [FAIL] ${name} ${detail}`); }
}

console.log("Unit 8 · markdown.js 离线测试\n");

// --- XSS 防护 ---
check("转义 < > &", escapeHtml('<script>&"') === "&lt;script&gt;&amp;&quot;");
const xss = renderMarkdown('<img src=x onerror=alert(1)>');
check("原始 HTML 不被执行", !xss.includes("<img src=x"), xss.slice(0, 80));
const jsLink = renderMarkdown("[点我](javascript:alert(1))");
// 正确行为：不生成 <a> 标签（原始文本被转义保留），因此不可能触发脚本
check(
  "javascript: 链接不渲染为 <a>",
  !jsLink.includes("<a ") && !jsLink.includes("href="),
  jsLink.slice(0, 80),
);
// 同时确认正常链接仍然可用
const goodLink = renderMarkdown("[点我](https://example.com)");
check("https 链接正常渲染", goodLink.includes('<a href="https://example.com"'));
check("外部链接带 rel=noopener", goodLink.includes('rel="noopener noreferrer"'));

// --- 标题 ---
check("# 标题", renderMarkdown("# 标题").includes("<h1>标题</h1>"));
check("### 三级", renderMarkdown("### 三级").includes("<h3>三级</h3>"));

// --- 强调 ---
check("**粗体**", renderMarkdown("**加粗**").includes("<strong>加粗</strong>"));
check("*斜体*", renderMarkdown("这是*斜体*文字").includes("<em>斜体</em>"));
check("~~删除~~", renderMarkdown("~~删掉~~").includes("<del>删掉</del>"));
check("行内 `code`", renderMarkdown("用 `foo()` 调用").includes('<code class="inline-code">foo()</code>'));

// --- 代码块 ---
const codeMd = "```python\ndef add(a, b):\n    return a + b\n```";
const codeHtml = renderMarkdown(codeMd);
check("代码块生成", codeHtml.includes('class="code-block"'));
check("语言标签", codeHtml.includes(">python<"));
check("有复制按钮", codeHtml.includes("copy-btn"));
check("保留缩进", codeHtml.includes("    return"));
check("关键字高亮", codeHtml.includes("tok-keyword"));
check("原始代码可复制", codeHtml.includes('class="code-raw"'));

// --- 代码块内的 Markdown 不应被解析 ---
const inCode = renderMarkdown("```\n**不该加粗**\n# 不是标题\n```");
check("代码块内 # 不解析为标题", !inCode.includes("<h1>"));
check("代码块内 ** 不解析", !inCode.includes("<strong>"));

// --- 列表 ---
check("无序列表", renderMarkdown("- a\n- b").includes("<ul><li>a</li><li>b</li></ul>"));
check("有序列表", renderMarkdown("1. a\n2. b").includes("<ol>"));

// --- 表格 ---
const tableMd = "| 名称 | 值 |\n|---|---|\n| a | 1 |\n| b | 2 |";
const tableHtml = renderMarkdown(tableMd);
check("表格生成", tableHtml.includes("<table>"));
check("表头", tableHtml.includes("<th>名称</th>"));
check("两行数据", (tableHtml.match(/<tr>/g) || []).length === 3, `实际 ${(tableHtml.match(/<tr>/g) || []).length}`);

// --- 引用与分隔线 ---
check("引用", renderMarkdown("> 引用内容").includes("<blockquote>"));
check("分隔线", renderMarkdown("---").includes("<hr"));

// --- 混合内容（模拟模型真实输出）---
const mixed = [
  "## 什么是 CORS",
  "",
  "CORS 是**跨域资源共享**机制。",
  "",
  "```javascript",
  'fetch("http://api.example.com/data")',
  "```",
  "",
  "| 场景 | 是否跨域 |",
  "|---|---|",
  "| 同源 | 否 |",
  "",
  "> 注意：浏览器会先发 OPTIONS 预检。",
].join("\n");
const mixedHtml = renderMarkdown(mixed);
check("混合内容：标题", mixedHtml.includes("<h2>"));
check("混合内容：代码块", mixedHtml.includes("code-block"));
check("混合内容：表格", mixedHtml.includes("<table>"));
check("混合内容：引用", mixedHtml.includes("<blockquote>"));
check("混合内容：加粗", mixedHtml.includes("<strong>"));

// --- 未闭合代码块不丢内容 ---
const unclosed = renderMarkdown("```python\nprint(1)");
check("未闭合代码块不丢内容", unclosed.includes("print(1)"), unclosed.slice(0, 100));

fs.unlinkSync(tmp);

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
process.exit(fail ? 1 : 0);
