# H5 数据字典与迁移

迁移：`20260929_0014`，基于 `20260927_0013`。新增表，不删旧题库或作答；`jobs.batch_id` 改为可空以承载学习任务。旧导入任务仍保留批次外键。

| 表 | 主键与主要字段 | 约束/用途 |
| --- | --- | --- |
| user_learning_preferences | user_id；subject_id、motto | 每用户一份配置 |
| user_exam_plans | user_id + subject_id；title、start_date、exam_date | 每科目一份考期 |
| learning_sessions | UUID id；user_id、subject_id、source、title、question_ids、position、drafts、attempts | 题序固定；JSON 草稿按整题ID索引，保存版本和小问答案；attempts映射本次整题到作答ID |
| user_question_marks | user_id + question_id + kind；active、attempt_id、resolved_at | kind为wrong/favorite/hesitant；解除只更新活动状态；用户/活动/类型索引 |
| tutor_sessions | UUID id；user_id、question_id、attempt_id、context、system_prompt、prompt_version | 私有上下文和提示词快照；版本为两份提示词及输出约束的SHA-256 |
| tutor_messages | UUID id；session_id、role、content、job_id、variant_id | 消息按时间和ID排序；同任务同角色唯一；教学题消息只引用变式题ID |
| teaching_variants | UUID id；subject_id、node_id、match_key、content_hash、status、content、model、prompt_version、validation | 哈希唯一；结构化题干/选项/答案/解析/考查目标/难度/题型；approved/quarantined/rejected |
| teaching_variant_answers | user_id + variant_id；answer、correct | 独立教学作答，不写正式 learning_attempts |
| teaching_variant_reports | UUID id；user_id、variant_id、reason | 问题反馈，触发共享题复核 |

新表均保存 created_at、updated_at。跨用户共享的只有已校验教学题内容，不包含原用户答案、会话记录或提示词中的私人数据。

迁移将旧作答中曾经答错的整题回填为活跃 wrong 标记，并关联最近错误作答。首次升级前没有手动解除功能，所以没有旧解除状态需要推断；迁移后由状态表控制，不再每次查询重新派生“曾答错”。

任务类型新增 tutor、variant、variant_wait、variant_review。生成共享题的任务不包含私有聊天；每个请求拥有自己的等待任务，轮询仍按本人授权。现有 outbox、generation fencing、租约回收继续工作。导入管理员任务路由不能读取无批次的学习任务。

迁移不在应用启动时执行。损失学习记录的 downgrade 被拒绝；回退使用数据库/素材配套备份或兼容的新镜像关闭 H5 开关。

## 2026-09-30 续练与统一类似题

增量迁移 `20260930_0015` 基于0014：

- `learning_sessions.source_id`：可空UUID，标识试卷、章节或来源题；新增 `(user_id, subject_id, source, source_id)` 查找索引。迁移只回填能由完整题目集合确定的试卷及单题来源，旧章节不按标题猜测。
- `related_practice_requests`：UUID主键；本人 `user_id`、原 `session_id`、`question_id`、`request_key`、JSON `result` 和时间戳。用户/请求键唯一，用户/原会话/题目索引用于恢复；结果引用正式练习会话、教学题或生成任务。
- 续练合并最新正式作答引用而不重写或删除原作答；保留原题序，新增可练题追加到末尾。最新有答案、草稿或非首题位置的会话优先于空会话。
- 原有来源无法可靠确定时保留该会话；新会话仍可从用户正式作答恢复答案及最近作答位置。不可用未答题保留在存储题序中，通过 `available_positions` 跳过。
