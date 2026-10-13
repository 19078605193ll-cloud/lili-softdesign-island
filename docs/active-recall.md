# 主动回忆首版

## 2026-10-13：回填持久化与语义判分

会话增加 `slot_presentations`，以空位标识保存本轮用户提交的原始答案和 `correct/incorrect` 状态。继续下一空、刷新或返回训练时恢复回填；同一空重答时遮住它的旧错答，三次错跳过后保留最近一次错答。开始无提示复测时后端清空全部旧回填，复测中新结果重新累积，完成页保留本轮结果。数据库迁移为 `20261013_0021`：首次训练、待复测和完成会话从最新提交恢复回填，旧的正在复测会话保持空集合，避免无法区分轮次的旧记录泄露答案。

判分先沿用规范化及标准答案、可接受答案匹配，命中不调用模型。未命中时用 `AI_RECALL_MODEL`（留空回退 `AI_TEXT_MODEL`）结合当前版本正文和目标空位进行语义复核；允许确切等价表达，拒绝否定、限定条件缺失、公式含义变化和方向颠倒。判分提示词由独立服务维护，不复用内容生成指导词。输入作为资料处理，不执行用户答案中的指令。

`RECALL_SEMANTIC_GRADING_ENABLED=true` 默认启用；关闭时恢复原规则判分。`RECALL_GRADING_TIMEOUT_SECONDS=10` 为总请求期限，配置范围大于 0 且不超过 15 秒，SDK 不自动重试。配置缺失、无法确定、无效输出及调用失败返回 `503 / RECALL_GRADING_UNAVAILABLE`；不写作答、不计错、不推进进度。学习端保留输入与幂等键，允许重试原提交。标准答案仍仅在第三次错误反馈时展示，历史成绩不会重算。

本地 `AI_RECALL_MODEL`、`AI_TEXT_MODEL` 使用 `qwen3.8-flash`。`AI_RECALL_ENABLE_THINKING=false` 与 `AI_TEXT_ENABLE_THINKING=false` 默认关闭思考，Qwen 请求通过 `extra_body.enable_thinking` 显式传参；其他模型不接收该专用参数。生成、语义判分、文本整理、Markdown 辅助及教学 Qwen 回退均使用模型适配。原 `AI_RECALL_REASONING_EFFORT`、`AI_TEXT_REASONING_EFFORT` 没有配置字段，不再使用。修改环境变量需重建容器。

日志记录规则/语义判分、模型、耗时、结果或失败原因，不记录完整用户答案或密钥。生成指导词要求补充上下文中准确等价的常见表达，已发布内容无需重新生成。

验证记录：后端隔离回归 109 项通过，新增模型调用、超时和取消等单元测试组 64 项通过（与回归组部分重合），语义判分开关补充场景在隔离数据库通过；前端 36 项单测及构建通过。Chromium/WebKit 共 6 条浏览器流程覆盖三种布局、错答重试、刷新/返回恢复和复测隐藏旧答案。真实 DMX 的 10 个判分样例及内容生成通过；生成可接受答案保留必要比较程度。业务库已升级至 0021，备份独立恢复验证通过，已有题库与作答数量保留；真实 RISC/CISC 页恢复 5 个回填并通过刷新检查。运行验证与截图存于 `.local-runtime/recall-live-verification.json`、`.local-runtime/recall-live-mobile.png` 和 `.local-runtime/recall-live-desktop.png`。

## 2026-10-13：生成提示词与正文编辑

主动回忆生成指导词由 `Prompt/AI主动回忆生成Prompt.md` 提供（本机完整路径为 `D:\softdesign-island\Prompt\AI主动回忆生成Prompt.md`）。此文件初始为空，请用 UTF-8 填写自己的提示词；支持 UTF-8 BOM。文件缺失、空白或无法读取时返回 `RECALL_PROMPT_NOT_CONFIGURED`，不调用模型，也不回退旧提示词。格式、证据引用、空位一致性和版本兼容契约由程序追加，不必写进教学提示词。

经实际生成链路核对，保留三个可选占位符：`{{knowledge_point}}` 是单项生成的范围锚点，`{{memory_type}}` 是要点填空／表格对比／关系推演的结构约束（自动模式会明确说明由模型选择），`{{extra_requirements}}` 是管理员对整个批次的可选编辑偏好，未填写时为“无额外要求”。三者在界面和后端都有真实数据来源；完整 node 与 evidence 仍会随用户消息传入，占位符用于让可维护的系统指导词明确当前范围，不代替证据。程序只进行一次精确替换，不执行模板代码。来源记录同时保留原始模板及其摘要（`prompt_template`、`prompt_template_sha256`）和替换后的实际指导词及摘要，并在 `provenance.input` 中记录请求类型与补充要求。

本地 Compose 挂载了 `Prompt` 目录，保存文件后新任务自动读取，不必重建镜像。任务首次执行时固定快照，包含网络重试在内的同一任务沿用原快照；要应用新提示词请新建生成任务。草稿来源记录 `prompt_snapshot`、`prompt_sha256`、`contract_version`，已有草稿与已发布内容不会自动改写。其他部署如将提示词打包在镜像内，需更新镜像或部署时挂载文件。

要点填空支持完整段落编辑；对比表格支持单元格内编辑。新建内容从空正文开始，不再预置没有正文来源的空位；保存时至少需要一个已配置完整的空位。选择连续普通文字后点击“挖空所选文字”，自动创建答案；跨段落、跨单元格及包含已有空位的选区不允许挖空。点击编号答案，在侧栏编辑完整答案、说明、可接受答案和两级提示；高级判分设置默认折叠。侧栏在窄屏显示于正文下方。取消挖空恢复答案文字；删除含空位的段落、行或列需要确认并清理对应配置。

AI 批量生成区按“选知识点 → 确认类型与去向 → 填批次要求 → 查看任务结果”展示；任务运行期间锁定当前批次，成功后显示缺少直接依据的数量并可打开草稿。内容版本使用标题、版本号和状态分层展示。新建、切换版本或离开页面前会保护未保存修改；类型切换对纯文字内容也会给出清空确认。

编号代表训练顺序，新增空位追加到末尾；“提前一空／延后一空”显式调整顺序。已有空位的标识不因答案编辑而改变；改变考查含义时应取消旧空位再重新挖空。正文预览实时更新，学生端 iframe 展示已保存版本。仍需补全答案、说明及两级提示后保存，再按原流程审核发布；无数据库迁移。

本次验证：隔离数据库下 `tests/test_recall_prompt.py`、`tests/test_recall_generation.py`、`tests/test_recall.py` 共 30 项通过；`H5_E2E=1`、`H5_E2E_SPEC=recall.spec.ts` 下 Chromium/WebKit 共 4 条浏览器流程通过，覆盖中文组合输入、纯文本粘贴、跨范围选区拒绝、长答案响应式展开、挖空与取消、顺序调整、删除清理、保存重开、审核发布和学生训练。已填写项目提示词；未调用外部模型评估生成内容质量。

## 入口与边界

- 学习端：`/h5/recalls`；首页考期下方新增主动回忆卡片。
- 管理端：`/admin/recalls`，沿用现有登录和角色；editor 编辑、reviewer 审核、publisher 发布，administrator 可执行全流程。
- 知识详情：`/h5/knowledge/{node_id}`。章节与搜索结果保留直接练题，同时可以查看知识、对应回忆和相关真题。
- `RECALL_ENABLED=false` 隐藏首页入口并关闭 Recall 学习接口；后台内容保留。`LEARNING_ENABLED=false` 同样禁止新训练及提交。
- 第一版无跨天间隔复习、拖拽连线、AI 生成任务。回忆不影响 `h5-v1` 的知识掌握、真题完成量和综合正确率。

## 关系与存储

新增 `recalls`、`recall_versions`、`recall_knowledge_links`、`recall_blanks`、`recall_sessions`、`recall_session_blanks`、`recall_submissions` 七张表。

Recall 与现有 KnowledgeNode 为版本化多对多；关联仅允许同科目的有效 topic。复用已有 `question_knowledge_assignments` 的 confirmed primary/related，不重复建模。通过小问映射整题，主关联优先、命中知识点数量次之、年份倒序，再按 ID 稳定排序。只推荐满足现有可见性规则的 official/recalled 真题。

正文是带类型的结构化 JSON；版本记录 `schema_version=1`。每个 token 恰好包含 text 或 blank_key，空位必须在正文中恰好出现一次。table 为 columns/rows，新表格的每个单元格是有序 token 数组，从而支持格内局部挖空；读取时仍兼容旧版本的单 token 单元格。sentence 为 paragraphs，relation 为 nodes/edges/steps。正文不存第二份答案，通过 blank_key 引用标准答案；训练响应不包含答案、别名列表和后续提示。

每个用户、Recall 最多一个未完成会话，由部分唯一索引与事务锁共同约束。会话绑定不可变版本。新版本需要重新学习，旧版本会话可以继续；下架后只能查看历史进度。跨版本同一含义保留空位 key，含义改变必须换 key。

## 训练规则

1. 详情显示完整知识；开始后隐藏全部目标答案，按后台空位顺序逐空提交。
2. 当前尝试第 1 错给轻提示，第 2 错给关键线索，第 3 错显示答案并转下一个空。
3. 看过任何提示后答对仍待复测。首遍结束后显式点击开始无提示复测；清除输入、旧提示和答案。
4. 复测首次答对才掌握；再次错误继续同样规则。所有空无提示答对后完成。

`round_error_count` 累计整个会话的错误，复测不重置；`pass_error_count` 只控制当前尝试的三级反馈，复测重置。历史错误由用户、Recall、稳定 blank_key 下的错误提交累计，包含当前会话。

提交需要 `Idempotency-Key` 和会话 revision。重复键同请求返回原结果，重复键不同请求或过期 revision 返回 409。空白输入不计错。草稿按当前空保存并更新 revision，恢复会话不会重置计数。

仅转换全半角兼容字符、统一换行并 trim；不使用广义 NFKC 将 `n²` 错转为 `n2`。每空可以配置大小写、内部空白、符号别名、无序集合及多个可接受答案。公式不做模糊语义判分。

## 内容管理与试点

后台先点击“准备三份试点草稿”，可重复点击而不重复创建。默认不发布：

- 六种排序算法：最好/平均/最坏时间、辅助空间、稳定性，共 30 空。明确冒泡提前退出、归并实现和快速排序栈空间假设。[校对参考](https://algs4.cs.princeton.edu/cheatsheet/)
- 事务 ACID：四种性质名称，共 4 空，中英文答案均可接受。[校对参考](https://www.postgresql.org/docs/18/tutorial-transactions.html)
- Armstrong：三个基本公理的规则和结论，共 6 空。[校对参考](https://courses.cs.washington.edu/courses/cse344/26sp/lectures/lec11_fd.pdf)

管理员编辑后保存草稿，可直接执行“审核通过”，再单独发布。分工角色仍使用“editor 提交审核 → reviewer 审核通过 → publisher 发布”。已保存内容修改时另存为新版本，知识关联、答案和提示都重新审核。审核绑定版本哈希；同一管理员可审核发布，不强制双人。来源字段支持 manual/ai，provenance 记录输入知识快照、证据、字段引用、模型、Prompt 版本、任务 ID 和 AI 补充项。AI 只创建草稿，不能自动审核或发布。

结构编辑中先配置空位，再在单元格、文本片段、节点或边中选择该空位。修改空位 key 后应重新选择引用；服务端阻止悬空、重复和未使用的空位。

## 接口

- `GET /api/v2/learning/recalls?subject_id=&type=&node_id=`：已发布列表及个人摘要。
- `GET /api/v2/learning/recalls/{id}`：完整知识。
- `GET /api/v2/learning/recalls/{id}/questions?version_id=&cursor=&limit=`：相关真题。
- `POST /api/v2/learning/recall-sessions`：`{recall_id}`，创建或恢复。
- `GET/PATCH /api/v2/learning/recall-sessions/{id}`：读取或保存 `{revision,blank_key,answer}` 草稿。
- `POST .../{id}/answers`：同上字段，另传幂等头；返回 feedback 和最新 session。
- `POST .../{id}/retest`：`{revision}`，进入独立复测。
- `GET /api/v2/learning/nodes/{id}`：聚合知识详情、下级节点、回忆和相关真题。
- 现有真题会话增加 `source=recall`（source_id 为内容版本）与 `source=knowledge`（主/相关联合匹配）。既有 `source=node` 及统计保持原口径。
- 管理端 `/api/v2/admin/recalls` 支持列表、新建、`seed-pilots`、`/{id}/versions` 和 `/versions/{id}`；版本操作为 submit/approve/reject/publish/unpublish。拥有 edit 与 review 权限的管理员可直接将 draft approve，其他角色仍按 pending 流程协作。
- 另存版本使用 `If-Match: <最新版本ID>:<revision>`，工作流操作提交 `{revision,note}`。

## 验证与部署

```powershell
.venv\Scripts\python.exe -X utf8 scripts/test_isolated.py -q tests/test_recall.py tests/test_h5.py tests/test_platform.py
$env:H5_E2E='1'
.venv\Scripts\python.exe -X utf8 scripts/test_isolated.py -q tests/test_h5_browser.py
# 在 web/h5 目录
npm.cmd test
npm.cmd run build
```

首版 Recall 表迁移为 `20261012_0018`；`20261012_0019` 曾增加逐项补充审核元数据，`20261012_0020` 删除该元数据并改用整份草稿审核。健康检查要求同步升级至最新版本。迁移不改变生成正文、provenance、题库与用户作答。

## AI 批量生成

管理端可一次选择最多 20 个同科目知识点，每个知识点独立创建 `recall_generate` 后台任务。已有内容必须明确选择生成新版本或另建内容；生成结果只保存为草稿，不自动提交审核或发布。

生成模型优先读取 `AI_RECALL_MODEL`，为空时回退 `AI_TEXT_MODEL`；`RECALL_AI_GENERATION_ENABLED=false` 可只关闭生成入口。模型输入由知识点元数据和最多 12 道已确认关联真题组成。未提供有效依据引用的答案、提示或整体内容会进入 `provenance.supplements`；管理端仅显示数量摘要，管理员结合完整编辑内容和真实预览进行整份审核，不再逐项确认。

任务接口为 `POST /api/v2/admin/recalls/generations`、`GET /api/v2/admin/recalls/generations/{request_id}` 和单项重试接口。草稿预览通过受管理权限保护的 H5 路由复用正式 Recall 组件，不创建学习会话或作答记录。

部署沿用项目现有流程：数据库及素材配套备份并验证恢复 → 暂停写进程 → 对目标环境执行 Alembic upgrade head → 构建 H5 并重建 API/worker/dispatcher 镜像 → 检查 health/ready、学习端和管理端 → 后台准备三份草稿并由人审核发布。

本次开发测试使用独立容器；不自动迁移业务数据库，不替用户完成内容的人工审核。关闭功能回退应保留新表和历史记录；存在 Recall 数据时拒绝有损 downgrade。
