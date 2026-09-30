# H5 验证与已知环境边界

## 本轮结果（2026-09-29）

- 后端完整隔离回归：113 passed、1 skipped；跳过项为默认关闭的浏览器入口。
- 单独开启H5_E2E后，Chromium和WebKit完整流程均通过；包含教学题刷新恢复和普通单选立即提交。
- Vue TypeScript检查与Vite生产构建通过；Vitest请求层3项通过。
- Docker镜像 `softdesign-island:h5-validation` 构建通过；禁网容器烟测确认H5静态资源、两份提示词、应用导入可用。
- Ruff新增代码未使用变量/导入检查通过，Git diff空白检查通过。
- 旧任务测试的立即执行假设在容器时钟调整时会读到尚未到期的queued状态；测试助手现在按队列语义短暂重试，生产仍由dispatcher按数据库时钟调度。

## 可重复验证

```powershell
cd web/h5
npm ci
npm run build
npm test
npx playwright install chromium webkit
cd ../..
$env:H5_E2E='1'
uv run python scripts/test_isolated.py -q --tb=short
```

后端脚本创建独立PostgreSQL和Redis容器，测试完成后只清理该随机项目。浏览器测试使用真实FastAPI、真实登录、数据库和任务状态；模型回复由 `tests/h5_browser_server.py` 提供明确标注的隔离夹具，不进入生产镜像或正式页面。

浏览器覆盖：登录、考期保存、组合题草稿刷新、错题反馈、AI追问、Mermaid结构图、教学变式题及刷新恢复、错题本双解除、犹豫手动解除、收藏取消、普通单选立即提交、退出。

截图位于 `web/h5/test-results/` 的各浏览器目录，包含home、practice、incorrect、correct、single-correct、notebook。布局检查覆盖320、375、390、414、430、768px；Chromium和WebKit是桌面环境的移动视口测试，不替代真机微信浏览器和软键盘验收。

## 当前环境边界

- 2026-09-29已恢复原PostgreSQL容器，并按用户授权完成业务部署。真实数据库从0013升级至0014；API、worker、dispatcher均已重新创建并读取AI_TUTOR_MODEL。
- 升级前停止写入，备份数据库及题图至 `var/backups/before_h5_0014_20260929_171418/`。在独立PostgreSQL容器中恢复备份、执行迁移并对比数量，确认后才迁移业务库。
- 原有109道小问、2份试卷、44个题图素材、8个导入批次、1个原账号保持；验收另建两个无管理权限账号，验收完成后已停用并撤销登录。原管理员学情未写入验收作答。
- 真实模型引导、追问、Mermaid图、教学题生成及独立答案复核通过；第二用户复用同一变式题命中数据库，私有AI会话访问返回404。
- 实际部署的Chromium/WebKit页面验证通过，375/390/430宽度无横向溢出、无pageerror，实际Mermaid SVG成功渲染。截图及结果位于 `var/h5-acceptance/`。
- 实测发现模型常用的标签换行 `<br/>` 被原规则拒绝；现仅将该标签转换为纯文本分隔，保留脚本、外部链接和指令过滤，禁用Mermaid HTML标签。新增5项测试通过，前端现共8项单测通过。
- 真机微信、实际iOS/Android软键盘和安全区尚未验收，桌面WebKit移动视口不能替代真机检查。
- Mermaid按需加载，构建会提示其部分图表分块较大；首页主包不包含这些图表模块。真实首屏网络性能和大规模作答统计负载需在目标部署环境测量。

## 视觉调整

依据用户确认，保留原图布局和青绿/雾蓝/暖白风格，机器人使用轻量组件、图标使用Lucide；不声称背景插画和图标像素级一致。题干摘要、解除按钮和真实空状态优先于原图示例。

关键文字对比度：辅助文字/暖白5.06:1，错误文字/浅红4.95:1，白字/主按钮5.08:1，深绿/浅绿6.93:1。错误与正确还同时使用文字、图标和边框，避免仅靠颜色区分。

## 2026-09-30 移动端与续练修订（源码验收）

- `npm run build`：Vue类型检查与Vite构建通过；沿用的Mermaid大分块提示仍存在。
- `npm test`：8项前端单测通过。
- `H5_E2E=1 .venv/Scripts/python.exe scripts/test_isolated.py -q tests/test_h5.py tests/test_h5_browser.py tests/test_platform.py --maxfail=1`：21项通过，包含Chromium及WebKit两条真实API浏览器流程。使用独立容器数据库、Redis及各浏览器独立账号，未修改本机业务学情。
- 新增20题试卷接口验收：提交19题后恢复索引18，回看第8题后恢复索引7；整套完成仍复用原会话和20份作答引用。
- 续练验证覆盖旧来源推断、忽略较新的空会话、答案合并、回看位置、独立重练和本人权限；类似题覆盖未答真题优先、原请求重放、未完成任务恢复、生成及入库、无模型配置时缓存命中、90天前已答教学题仍排除。
- 固定导航和答题框架检查320/375/390/414/768px，AI输入支持显式换行及136px高度封顶。答对轻量入口、答错AI面板及固定输入区截图已人工查看，位于 `web/h5/test-results/learning-real-API-learning-30f08-ok-tutor-and-mobile-layouts-{chromium,webkit}/`。
- 生成测试使用隔离模型夹具；本轮没有调用真实付费模型。实际手机软键盘、安全区和微信浏览器仍未真机验收。
- 本轮只交付源码、构建产物和增量迁移0015；本机运行中的镜像及业务数据库未升级。部署步骤见 `docs/local-startup.md` 的2026-09-30章节。

### 2026-09-30 后续部署验收

用户授权后已完成本机部署0015。配套备份位于 `var/backups/before_h5_0015_20260930_122339/`；独立数据库恢复、升级及表记录计数校验通过，素材归档逐文件校验通过。
运行环境 `/health/ready` 与Celery ping通过。Chromium/WebKit在375/390/430px验证导航、固定操作栏、多行输入及历史AI图表，pageerror为空。
验收账号真实接口验证：paper续练复用同一会话、恢复历史答案与位置；类似题命中未答真题，重复点击复用未完成会话。未新增正式答题，业务库正式作答仍为45条；两个验收账号已停用并撤销登录。
结果保存到 `var/h5-0015-acceptance/browser-result.json`、`api-result.json` 及同目录截图。本次部署未再次触发付费模型生成；生成分支已在隔离回归中验证。真机软键盘仍待用户检查。
