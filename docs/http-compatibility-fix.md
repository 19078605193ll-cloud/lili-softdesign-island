# 公网 HTTP 作答故障修复（2026-10-01）

## 原因和证据

公网 HTTP 的浏览器上下文不是 secure context。实测 `crypto.randomUUID` 为 undefined，`crypto.getRandomValues` 仍可用。

Session 页面生成提交标识时抛出 TypeError，导致 Idempotency-Key 未设置；点击选项后的 POST /api/v2/learning/attempts 返回 422，响应指出 header/idempotency-key 必填。这是两张报错截图的同一条故障链，不是数据库不可用。

## 修复

- H5 统一 UUID 工具：支持时使用原生 randomUUID，否则用 getRandomValues 生成符合版本位和变体位要求的 UUID v4。不使用 Math.random，不降低请求幂等性要求。
- 作答、AI 创建与追问、类似题、Mermaid 图表全部使用兼容工具；管理员模板中的上传及任务请求也修复。
- 保留同一作答失败重试时的标识复用规则，不改变后端 API、数据库、账号或线上 Prompt。
- API 已发布镜像 softdesign-island:http-uuid-20261001；基于原生产镜像，仅覆盖 H5 dist 与管理员 base.html。worker/dispatcher 的后端代码不变。

## 验证

- 12 项前端测试通过，TypeScript/Vite 构建通过。
- 公网 Chromium 复现旧版错误：secure=false、randomUUID=undefined、提交缺少幂等请求头并返回 422。
- 新版同环境作答返回 201；模拟网络失败后重试复用同一标识，刷新恢复作答结果，无页面脚本错误。
- 公网浏览器真实 AI 初始对话、追问及 Mermaid SVG 渲染通过；类似题入口返回 200 并展示相关真题。该场景返回真题，不把它误记为新 AI 变式题生成。
- Chromium/WebKit 均验证管理员模板在没有原生 randomUUID 时生成有效 UUID。
- 已给学习 E2E 添加禁用 randomUUID 的初始化，避免 localhost 的安全上下文掩盖公网 HTTP 问题；本次未执行整套隔离 E2E，实际验证采用上述公网专用账号流程。
- 专用验收账号 http-compat-20261001 验收后停用，测试记录保留，不修改真实用户的作答。

## 使用与回退

地址仍为 http://159.75.2.196/h5/。手机关闭旧页面后重新打开；如内置浏览器仍使用旧页面，刷新或清除此站点缓存。不能仅在旧页面点“重试”来加载新版代码。

服务器回退配置已保存为 deploy/compose.env.before-uuid-20261001。回退仅重新创建 API，不操作数据库；旧版本含本次已知 HTTP 故障，不作为长期解决办法。
