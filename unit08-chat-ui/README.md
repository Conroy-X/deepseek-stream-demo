# Unit 8 · Chat UI

> 目标：把「能对话」升级成「读得舒服」。
> Markdown 渲染 · 代码高亮 · 一键复制 · 自动滚动 · 中途停止。

---

## 一、这个单元做什么

Unit 7 打通了链路，但模型返回的 `**加粗**`、```` ```python ```` 代码块、
`| 表格 |` 在页面上会原样显示成一堆符号。

Unit 8 解决：**把 Markdown 渲染成 HTML，并让代码块能看、能复制**。

| 能力 | 实现方式 |
| --- | --- |
| Markdown → HTML | 手写解析器 `markdown.js`（零依赖，约 380 行） |
| 代码高亮 | 简单词法高亮（关键字/字符串/数字/注释/函数名） |
| 一键复制 | `navigator.clipboard`，降级到选中文本 |
| 自动滚动 | 监听滚动位置，用户上翻时不打断 |
| 中途停止 | `AbortController` |
| 移动端 | 媒体查询 + 表格横向滚动 + 输入框自适应高度 |

---

## 二、为什么手写 Markdown 解析器

用 `marked` / `markdown-it` 更省事，但本项目选择手写，原因：

1. **零依赖，离线可跑** —— 不依赖 CDN，也不需要 `npm install`
2. **知道边界在哪** —— 手写一遍才明白「渲染 Markdown 要处理哪些坑」：
   - 代码块里的 `#` 不能变成标题
   - 代码块里的 `**` 不能变成加粗
   - 一次 `read()` 可能给你半个 Markdown 片段，围栏还没闭合
   - 表格分隔行 `|---|---|` 要和普通行区分
3. **安全可控** —— 所有内容先 `escapeHtml`，链接只允许 `http/https`

> 生产项目请直接用 `react-markdown` + `remark-gfm` + `rehype-highlight`（见资料），
> 这里的实现是**教学版**，不是替代品。

---

## 三、怎么跑

### 前置：先把 Unit 7 的后端跑起来

```bash
cd ~/ai-journey/unit07-web-chat
.venv/bin/uvicorn main:app --port 8000
```

### 跑本单元的前端

方案 A：用 Python 起静态服务器（最简单）

```bash
cd ~/ai-journey/unit08-chat-ui
python3 -m http.server 3000
```

然后浏览器打开 **http://127.0.0.1:3000/**

> 必须用 HTTP 服务器打开，**不能直接双击 HTML**。
> 因为页面用了 ES Module（`import`），`file://` 协议下会被 CORS 拦截。

方案 B：用 Node

```bash
npx serve static -p 3000
```

### 离线测试解析器（不需要浏览器、不花钱）

```bash
cd ~/ai-journey/unit08-chat-ui
node test-markdown.mjs
```

应该看到 `结果：32 通过 / 0 失败`。

---

## 四、解析器的 6 个关键点

### 1. 先转义，再渲染

```javascript
function renderInline(text) {
  // 先把行内代码抠出来占位，避免代码里的 * 被当成强调
  // 然后 escapeHtml，最后才做 **粗体** 等替换
}
```

顺序错了就有 XSS 风险：如果先替换 `**x**` 再转义，攻击者可以构造
`<img src=x onerror=...>` 注入。

### 2. 链接只允许 http/https

```javascript
s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, ...)
```

如果不限制协议，`[点我](javascript:alert(1))` 就会变成可点的 XSS。

### 3. 代码块用状态机，不是正则

```javascript
let inCode = false;
if (fence) { inCode = !inCode; ... }   // 遇到 ``` 就翻转
if (inCode) { codeBuf.push(line); continue; }  // 代码块内不做任何解析
```

### 4. 未闭合的代码块不能丢

模型流式输出到一半，```` ```python ```` 后面的内容可能还没到。
如果代码块没闭合就丢弃，用户会看到内容凭空消失。

### 5. 表格要先看下一行是不是分隔行

```javascript
if (line.includes("|") && isTableSeparator(lines[i + 1])) { ... }
```

### 6. 复制按钮用事件委托

因为消息是动态插入的，给每个按钮单独绑事件会内存泄漏。
在父容器上绑一次，用 `e.target.closest(".copy-btn")` 判断即可。

---

## 五、流式渲染的性能问题

模型逐字返回，如果每个字符都 `innerHTML = renderMarkdown(raw)`，会非常卡。

本单元的做法：
- 每收到一个 delta 就重渲染整段（简单可靠）
- 用 `requestAnimationFrame` 节流滚动，避免每字符都触发重排

**优化方向（留给以后）**：
- 只在「结构性 token」（`\n`、```` ``` ````）到达时才重渲染
- 或用虚拟 DOM diff

实测：1024 token 的回答在 Chrome 里没有明显卡顿。

---

## 六、完成标准

- [ ] `node test-markdown.mjs` → 32 通过 / 0 失败
- [ ] 浏览器打开页面，发一条会返回 Markdown 的问题
- [ ] 代码块有语言标签、有高亮、点「复制」能复制到剪贴板
- [ ] 表格能正常渲染（不会撑破页面）
- [ ] 生成过程中点「停止」能中断
- [ ] 手机上打开，输入框和消息不溢出
- [ ] 用「长回答」测试，滚动条自动到底

---

## 七、资料

- [react-markdown](https://github.com/remarkjs/react-markdown) —— 生产环境用这个
- [remark-gfm](https://github.com/remarkjs/remark-gfm) —— 支持表格、删除线等 GFM 扩展
- [rehype-highlight](https://github.com/rehypejs/rehype-highlight) —— 代码高亮
- [MDN：AbortController](https://developer.mozilla.org/zh-CN/docs/Web/API/AbortController)
- [MDN：Clipboard API](https://developer.mozilla.org/zh-CN/docs/Web/API/Clipboard_API)
- [CommonMark 规范](https://spec.commonmark.org/) —— 想知道「标准 Markdown 有多复杂」的话
