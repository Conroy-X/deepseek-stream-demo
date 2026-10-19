/**
 * Unit 8 · 极简 Markdown 渲染器（零依赖，本地内置）
 *
 * 为什么不直接用 marked / markdown-it？
 *   - 本机 npm 可用，但为了单元自我包含、离线也能跑，这里手写一个小实现
 *   - 更重要的是：手写一遍你才知道「渲染 Markdown 到底要处理哪些边界」
 *
 * 支持：
 *   - 围栏代码块 ```lang（带语言标签）
 *   - 行内代码 `code`
 *   - 标题 # ~ ######
 *   - 粗体 **x**、斜体 *x*、删除线 ~~x~~
 *   - 有序 / 无序列表（支持一层嵌套）
 *   - 引用 >
 *   - 表格（GFM）
 *   - 链接、分隔线
 *
 * 安全：所有用户/模型内容都经过 escapeHtml，不会注入 HTML。
 */

const ESCAPES = {
  "&": "&amp;",
  "<": "&lt;",
  ">": "&gt;",
  '"': "&quot;",
  "'": "&#39;",
};

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ESCAPES[c]);
}

/* ------------------------------------------------------------------
   行内元素：**粗体** *斜体* ~~删除~~ `代码` [文本](链接)
   注意顺序：先处理行内代码，避免代码里的 * 被当成强调
   ------------------------------------------------------------------ */
function renderInline(text) {
  // 用占位符保护行内代码
  const codes = [];
  let s = text.replace(/`([^`\n]+)`/g, (_, code) => {
    codes.push(`<code class="inline-code">${escapeHtml(code)}</code>`);
    return `\u0000CODE${codes.length - 1}\u0000`;
  });

  s = escapeHtml(s);

  // 链接 [text](url) —— 只允许 http/https，防 javascript: 注入
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, label, url) =>
      `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${label}</a>`);

  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
  s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");

  // 还原行内代码
  s = s.replace(/\u0000CODE(\d+)\u0000/g, (_, i) => codes[Number(i)]);
  return s;
}

/* ------------------------------------------------------------------
   表格：识别 | a | b | 加下一行分隔 |---|---|
   ------------------------------------------------------------------ */
function isTableSeparator(line) {
  return /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(line) && line.includes("-");
}

function splitRow(line) {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
}

function renderTable(headerLine, sepLine, bodyLines) {
  const head = splitRow(headerLine);
  // 对齐方式：:--- 左 / :---: 中 / ---: 右
  const aligns = splitRow(sepLine).map((c) => {
    const left = c.startsWith(":");
    const right = c.endsWith(":");
    if (left && right) return "center";
    if (right) return "right";
    if (left) return "left";
    return "";
  });

  const th = head
    .map((c, i) => {
      const a = aligns[i] ? ` style="text-align:${aligns[i]}"` : "";
      return `<th${a}>${renderInline(c)}</th>`;
    })
    .join("");

  const rows = bodyLines
    .map((ln) => {
      const cells = splitRow(ln);
      const tds = head
        .map((_, i) => {
          const a = aligns[i] ? ` style="text-align:${aligns[i]}"` : "";
          return `<td${a}>${renderInline(cells[i] ?? "")}</td>`;
        })
        .join("");
      return `<tr>${tds}</tr>`;
    })
    .join("");

  return `<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

/* ------------------------------------------------------------------
   代码块：带语言标签 + 复制按钮 + 简易高亮
   ------------------------------------------------------------------ */
const KEYWORDS = {
  python: /^(def|class|return|if|elif|else|for|while|import|from|as|try|except|finally|with|yield|async|await|lambda|pass|raise|in|not|and|or|is|None|True|False)$/,
  javascript: /^(function|const|let|var|return|if|else|for|while|import|export|from|async|await|class|new|try|catch|finally|throw|typeof|instanceof|null|undefined|true|false|this)$/,
  typescript: /^(function|const|let|var|return|if|else|for|while|import|export|from|async|await|class|new|try|catch|finally|throw|typeof|interface|type|enum|null|undefined|true|false|this)$/,
  bash: /^(if|then|else|fi|for|do|done|while|function|return|export|local|echo|cd|source)$/,
  json: /^(true|false|null)$/,
  sql: /^(select|from|where|insert|into|values|update|set|delete|create|table|join|on|group|by|order|limit|as|and|or|not|null)$/i,
};

function highlight(code, lang) {
  const kw = KEYWORDS[lang];
  const parts = [];
  let out = "";
  let i = 0;
  const push = (html) => parts.push(html);

  while (i < code.length) {
    const ch = code[i];

    // 注释
    if (ch === "#" && (lang === "python" || lang === "bash")) {
      const end = code.indexOf("\n", i);
      const seg = code.slice(i, end === -1 ? code.length : end);
      push(`<span class="tok-comment">${escapeHtml(seg)}</span>`);
      i += seg.length;
      continue;
    }
    if (ch === "/" && code[i + 1] === "/" && lang !== "python") {
      const end = code.indexOf("\n", i);
      const seg = code.slice(i, end === -1 ? code.length : end);
      push(`<span class="tok-comment">${escapeHtml(seg)}</span>`);
      i += seg.length;
      continue;
    }

    // 字符串
    if (ch === '"' || ch === "'" || ch === "`") {
      let j = i + 1;
      while (j < code.length && code[j] !== ch) {
        if (code[j] === "\\") j++;
        j++;
      }
      const seg = code.slice(i, Math.min(j + 1, code.length));
      push(`<span class="tok-string">${escapeHtml(seg)}</span>`);
      i += seg.length;
      continue;
    }

    // 数字
    if (/[0-9]/.test(ch) && !/[a-zA-Z_]/.test(code[i - 1] || "")) {
      let j = i;
      while (j < code.length && /[0-9._]/.test(code[j])) j++;
      push(`<span class="tok-number">${escapeHtml(code.slice(i, j))}</span>`);
      i = j;
      continue;
    }

    // 标识符 / 关键字
    if (/[a-zA-Z_]/.test(ch)) {
      let j = i;
      while (j < code.length && /[a-zA-Z0-9_]/.test(code[j])) j++;
      const word = code.slice(i, j);
      if (kw && kw.test(word)) {
        push(`<span class="tok-keyword">${escapeHtml(word)}</span>`);
      } else if (code[j] === "(") {
        push(`<span class="tok-func">${escapeHtml(word)}</span>`);
      } else {
        push(escapeHtml(word));
      }
      i = j;
      continue;
    }

    push(escapeHtml(ch));
    i++;
  }

  void out;
  return parts.join("");
}

let codeBlockSeq = 0;

function renderCodeBlock(lang, code) {
  const id = `code-${++codeBlockSeq}`;
  const langLabel = lang || "text";
  const highlighted = highlight(code, lang);
  // 把原始代码存进 data 属性，供复制按钮使用（经过转义，不会破坏属性）
  return [
    `<div class="code-block" data-lang="${escapeHtml(langLabel)}">`,
    `<div class="code-head">`,
    `<span class="code-lang">${escapeHtml(langLabel)}</span>`,
    `<button class="copy-btn" data-target="${id}" type="button">复制</button>`,
    `</div>`,
    `<pre id="${id}"><code>${highlighted}</code></pre>`,
    `<textarea class="code-raw" style="display:none">${escapeHtml(code)}</textarea>`,
    `</div>`,
  ].join("");
}

/* ------------------------------------------------------------------
   主入口：把 markdown 文本转成 HTML
   ------------------------------------------------------------------ */
export function renderMarkdown(md) {
  const lines = String(md).replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let i = 0;
  let inCode = false;
  let codeLang = "";
  let codeBuf = [];

  const flushList = (items, ordered) => {
    if (!items.length) return;
    const tag = ordered ? "ol" : "ul";
    out.push(`<${tag}>${items.map((it) => `<li>${renderInline(it)}</li>`).join("")}</${tag}>`);
  };

  let listItems = [];
  let listOrdered = false;

  while (i < lines.length) {
    const line = lines[i];

    // ---- 代码块 ----
    const fence = line.match(/^\s*```(\w*)\s*$/);
    if (fence) {
      if (!inCode) {
        if (listItems.length) { flushList(listItems, listOrdered); listItems = []; }
        inCode = true;
        codeLang = fence[1] || "";
        codeBuf = [];
      } else {
        out.push(renderCodeBlock(codeLang, codeBuf.join("\n")));
        inCode = false;
        codeLang = "";
        codeBuf = [];
      }
      i++;
      continue;
    }
    if (inCode) {
      codeBuf.push(line);
      i++;
      continue;
    }

    // ---- 空行 ----
    if (!line.trim()) {
      if (listItems.length) { flushList(listItems, listOrdered); listItems = []; }
      i++;
      continue;
    }

    // ---- 表格 ----
    if (line.includes("|") && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      if (listItems.length) { flushList(listItems, listOrdered); listItems = []; }
      const header = line;
      const sep = lines[i + 1];
      const body = [];
      let j = i + 2;
      while (j < lines.length && lines[j].includes("|") && lines[j].trim()) {
        body.push(lines[j]);
        j++;
      }
      out.push(renderTable(header, sep, body));
      i = j;
      continue;
    }

    // ---- 标题 ----
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      if (listItems.length) { flushList(listItems, listOrdered); listItems = []; }
      const level = h[1].length;
      out.push(`<h${level}>${renderInline(h[2])}</h${level}>`);
      i++;
      continue;
    }

    // ---- 分隔线 ----
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      if (listItems.length) { flushList(listItems, listOrdered); listItems = []; }
      out.push("<hr />");
      i++;
      continue;
    }

    // ---- 引用 ----
    const q = line.match(/^\s*>\s?(.*)$/);
    if (q) {
      if (listItems.length) { flushList(listItems, listOrdered); listItems = []; }
      out.push(`<blockquote>${renderInline(q[1])}</blockquote>`);
      i++;
      continue;
    }

    // ---- 列表 ----
    const ul = line.match(/^\s*[-*+]\s+(.*)$/);
    const ol = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ul || ol) {
      const ordered = !!ol;
      if (listItems.length && ordered !== listOrdered) {
        flushList(listItems, listOrdered);
        listItems = [];
      }
      listOrdered = ordered;
      listItems.push((ul || ol)[1]);
      i++;
      continue;
    }

    // ---- 普通段落 ----
    if (listItems.length) { flushList(listItems, listOrdered); listItems = []; }
    out.push(`<p>${renderInline(line)}</p>`);
    i++;
  }

  if (listItems.length) flushList(listItems, listOrdered);
  if (inCode) {
    // 未闭合的代码块也要输出，避免内容消失
    out.push(renderCodeBlock(codeLang, codeBuf.join("\n")));
  }

  return out.join("\n");
}

/* ------------------------------------------------------------------
   复制按钮：事件委托，渲染后统一绑定一次
   ------------------------------------------------------------------ */
export function bindCopyButtons(root) {
  root.addEventListener("click", async (e) => {
    const btn = e.target.closest(".copy-btn");
    if (!btn) return;
    const block = btn.closest(".code-block");
    const raw = block?.querySelector(".code-raw");
    if (!raw) return;

    const text = raw.value ?? raw.textContent ?? "";
    try {
      await navigator.clipboard.writeText(text);
      const old = btn.textContent;
      btn.textContent = "已复制";
      btn.classList.add("copied");
      setTimeout(() => {
        btn.textContent = old;
        btn.classList.remove("copied");
      }, 1500);
    } catch {
      // 非 https 环境下 clipboard 不可用，退回选中文本
      const range = document.createRange();
      range.selectNodeContents(raw);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      btn.textContent = "请按 Ctrl+C";
      setTimeout(() => (btn.textContent = "复制"), 2000);
    }
  });
}
