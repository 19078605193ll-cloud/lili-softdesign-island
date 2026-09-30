# H5 接口与页面映射

所有学习接口位于 `/api/v2/learning`，身份来自同源会话。写请求要求 Origin 和 X-CSRF-Token；`/auth` 仍使用 v1。接口完整字段类型以运行时 `/openapi.json` 为准。

| 页面/能力 | 接口 | 输入与响应重点 | 数据来源 |
| --- | --- | --- | --- |
| 登录/退出 | v1 auth/csrf、login、me、logout | 既有 Cookie/CSRF，不另建 token 体系 | users、Redis |
| 科目 | GET /practice/subjects | items: id、code、name | exam_subjects |
| 励志语/科目设置 | GET/PATCH /learning/preferences | subject_id、motto（最多200字） | user_learning_preferences |
| 考期 | GET/PUT /learning/exam-plan | subject_id、title、start_date、exam_date；返回 today、days_remaining、progress | user_exam_plans |
| 首页 | GET /learning/dashboard | subject_id；rule_version、total、completed、accuracy、mastery、chapters、recent | 正式作答、可练题库、知识归属、会话 |
| 章节 | GET /learning/chapters | subject_id；items: id、name、mastery、state、score_share、sample_label | 同一统计服务 |
| 真题列表 | GET /practice/papers | subject_id、year可选、cursor、limit；题量、完成量、resume_position（从0计，无记录为null） | 已发布整题、本人作答、续练会话 |
| 搜索 | GET /practice/search | subject_id、q、cursor、limit；章节/知识点 id、name、type | 名称、别名、关键词 |
| 节点整题 | GET /practice/nodes/{id}/questions | cursor、limit；完整题目 | 本节点及后代的已确认主知识点 |
| 整题/解析 | GET /practice/questions/{id}、/{id}/solution | 既有字段增加 material_html、stem_html、content_html、explanation_html | 共享安全渲染器 |
| 练习会话 | POST /learning/sessions | subject_id、source、source_id、mode=new/resume（默认new）；返回 id、source_id、question_ids、position、drafts、attempts | paper/node 的 resume 复用会话并关联历史答案；重练保持独立 |
| 恢复/草稿 | GET/PATCH /learning/sessions/{id} | position；或 question_id、content_version、answers；GET补充available_positions与notice | 本人会话；草稿校验题目成员及版本；不可用未答题跳过，历史快照保留 |
| 提交 | POST /learning/attempts | 兼容新增可选 session_id；Idempotency-Key 必填 | 正式作答及标记在同一事务更新 |
| 作答历史 | GET /learning/attempts/{id} | snapshot 包含安全 HTML，历史图片改写为本人专用路径 | learning_attempts、learning_attempt_assets |
| 错题本 | GET /learning/notebook | subject_id、type=wrong/favorite/hesitant、cursor、limit | 活跃标记及当前题/历史快照 |
| 标记 | GET /learning/questions/{id}/marks | items: 活跃标记类型 | user_question_marks |
| 收藏/犹豫 | PUT /learning/questions/{id}/marks/{kind} | kind=favorite/hesitant；可选 attempt_id 必须属于本人和本题 | 同上 |
| 解除 | DELETE /learning/questions/{id}/marks/{kind} | kind=wrong/favorite/hesitant；历史不删除 | 同上 |
| 类似题 | GET /practice/questions/{id}/similar | 同科目、相同已确认主知识点，最多5题，排除本题 | 已发布正式题库 |
| 统一类似题入口 | POST /practice/questions/{id}/related-practice | Idempotency-Key；session_id；返回kind=question与session_id，或kind=variant与variant_id，或kind=job与job_id | 本人原会话验证；未完成结果优先恢复，其次未答真题→未答合格变式池→自动生成 |
| 创建 AI | POST /learning/tutor/sessions | Idempotency-Key；question_id，作答后给 attempt_id，作答前给 content_version | 私有会话及任务 |
| AI 历史 | GET /learning/tutor/sessions?question_id= | 本人最近10个会话 | tutor_sessions |
| AI 消息 | POST /learning/tutor/sessions/{id}/messages | Idempotency-Key；text 1–2000字 | tutor_messages、jobs、outbox |
| AI 轮询 | GET /learning/tutor/sessions/{id} | messages、job、variant_job、directions、prompt_version | 本人会话；不返回内部提示词/密钥 |
| 学习任务 | GET /learning/tutor/jobs/{id} | 只允许本人学习任务，返回状态/结果/通用错误 | jobs |
| 教学变式题 | POST /learning/variants | Idempotency-Key；question_id、difficulty、可选 tutor_session_id | 共享池命中或持久生成任务 |
| 教学题读取 | GET /learning/variants/{id} | 未作答不含答案，作答后含本人结果及解析 | 已通过校验的共享教学题 |
| 教学题回答 | POST /learning/variants/{id}/answers | answer=A/B/C/D；本人首次回答幂等保留 | 独立教学作答表 |
| 教学题反馈 | POST /learning/variants/{id}/reports | reason 1–1000字；暂停复用并创建复核任务 | 反馈表、共享题状态、jobs |

新列表的 cursor 是非负整数偏移，默认0，默认20或30条，最多100条；响应以 `next_cursor=null` 标识结束。既有试卷内整题列表保留原有不透明游标，不混用两者。

错题本条目返回 `practice_question_id,stem_excerpt,chapter_name,attempt_id,content_version,can_practice`。不存在的本人资源返回404，未登录401，CSRF失败403，题目版本/重复会话作答/AI忙碌冲突409，频率限制429，功能关闭或模型未配置503。错误对象沿用 `detail,code,request_id`。

source 支持 paper、node、question、wrong、hesitant、favorite、similar。wrong/hesitant/favorite 会校验对应活跃标记；只有前两种会话整题全对后自动双解除。普通作答不能通过提交自报入口解除记录。

类似题检索排除全部历史已答题（教学题不再只排除近30天）。命中数据库不要求模型在线；生成仍走异步校验及入库，失败重试使用新的请求键，网络重试保持原键。客户端独立路由 `/h5/related/{question_id}?parent={session_id}` 负责等待及教学题作答，真题转入普通练习会话并保存返回原会话的引用。

管理员试卷逻辑删除后，练习会话GET返回 `read_only=true` 及历史查看提示；新作答、类似题和AI创建/追问返回409 `PAPER_DELETED`。历史答案、图片与已有AI对话仍可按本人权限读取。普通试卷会话和最近学习记录标题动态读取正式试卷最新标题。管理接口与恢复行为详见 [试卷管理](admin-paper-management.md)。
