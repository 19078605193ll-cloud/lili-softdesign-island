# AI 回答公式显示问题排查与修复

排查日期：2026-10-02。

## 原因与证据

AI 回答使用 `web/h5/src/components/AiText.vue` 渲染，与普通题目使用的后端 Markdown/MathML 渲染链路不同。原组件只调用普通 `markdown-it`，没有数学公式解析器。

数据路径为：模型输出 → `app/h5/ai_worker.py` 的 `TutorMessage.content` → `app/h5/tutor.py` 返回 `m.content` → `Tutor.vue` 传入 `AiText` → Markdown → DOMPurify → 页面。所检查的后端代码直接保存和返回原文，没有转换公式。

已用相同公式语法复现：

| 输入原文 | 修复前 Markdown 输出 | 页面结果 |
| --- | --- | --- |
| `\(2^{12}\)` | `(2^{12})` | 指数显示为源码 |
| `\[2^{12}=4096\text{ 字节}\]` | `[2^{12}=4096\text{ 字节}]` | 方括号和 LaTeX 命令直接显示 |
| `$2^{20}$` | `$2^{20}$` | 美元符号和指数源码直接显示 |

Markdown 的反斜杠转义处理会消耗括号前的反斜杠，与用户截图表现一致。并非 Vue 的 `v-html` 自动支持公式，也不能在普通 Markdown 转换后才依赖 `\(...\)`、`\[...\]` 识别公式，因为届时标记已经丢失。

本次使用了代码追踪和公式样例复现；没有取得截图对应会话的数据库原文或接口响应，因此不能确认该会话的每一个原始字符。

## 已实现的解决方法

1. 新增 `web/h5/src/aiMarkdown.ts`，在 Markdown 解析阶段使用 `@mdit/plugin-katex`，设置 `delimiters: "all"`，识别两种行内与两种独立公式标记。
2. `AiText.vue` 使用此解析器，加载本地 KaTeX CSS 和字体，随后进行 DOMPurify 清洗。保留原有 Mermaid 渲染流程。
3. `main.css` 为独立公式添加横向滚动，长公式在手机端的回答容器内滚动。
4. 错误公式保留可读源码，其余回答继续显示；代码块和等宽文本中的公式标记保留原样。

选择与项目 Markdown 14 和现有 KaTeX 0.16 依赖兼容的 `@mdit/plugin-katex@0.25.2`。最新插件版本的 Markdown 主版本要求不同，本次没有升级原有 Markdown 解析器。`katex` 已声明为直接依赖，避免依赖 Mermaid 的间接安装。

参考：[插件的公式分隔符与 CSS 文档](https://mdit-plugins.github.io/katex.html)、[KaTeX 配置](https://katex.org/docs/options.html)、[Markdown 反斜杠转义规则](https://spec.commonmark.org/0.31.2/#backslash-escapes)。

## 验证与生效

- 修复前，公式回归测试中的 5 项失败；修复后 8 项公式测试全部通过。
- 前端全量单元测试 20 项通过，TypeScript 检查和生产构建通过。
- Chromium 与 WebKit 共 4 项浏览器测试通过，检查 DOMPurify 后的公式、实际 CSS、回答更新、Mermaid、错误公式，以及 320px 屏幕的长公式滚动。
- 浏览器测试使用独立 Vue 组件测试页，无需登录或调用 AI；没有执行生产环境验收。

本地构建：

```powershell
cd D:\softdesign-island\web\h5
npm ci
npm test
npm run build
```

浏览器回归：在一个终端执行 `npm run dev -- --port 5175 --strictPort`，在另一个终端执行：

```powershell
$env:H5_BASE_URL = 'http://127.0.0.1:5175'
npm run test:e2e -- ai-text.spec.ts
```

已生成本地 `web/h5/dist`，并使用 `compose.local.yml` 重建应用镜像、更新本地 API、worker 与 dispatcher 容器。`127.0.0.1:8000` 已返回包含公式解析器的新前端，公式样式与字体均可正常加载，健康检查通过。浏览器刷新页面即可重新渲染历史回答，无需重新调用模型，前提是原文仍保存着完整公式标记。没有修改线上服务器。

## 截图中的其他现象

末尾的“тамам”是文本内容问题。已检查的渲染链路没有追加该字符串的逻辑。应对比对应接口的 `messages[].content` 与模型原文：若原文含有它，再调整生成提示词或模型输出质量。本次没有按语言过滤回答，也没有自动删除字符。

提示词文件使用 `.md` 扩展名，`app/h5/tutor.py` 的 `prompts()` 已统一读取 `AI引导思考Prompt.md` 与 `AI画图讲解Prompt.md`。已验证本地和重建的 Linux 镜像均能完整加载两份文件，且版本哈希与合并后的提示词一致。没有修改提示词内容。
