# 软设岛 0.3 运行与发布

当前电脑已启动的本地环境与登录方式见 [本机启动说明](local-startup.md)。本地使用 `compose.local.yml`，不要用测试 Compose 启动业务数据。

本工程为模块化单体：API、Celery worker 和 outbox dispatcher 使用同一代码镜像。
Redis 仅承载会话及消息，PostgreSQL 保存任务与作答事实。H5 与后台使用同源 Cookie 会话，不需要 JWT。

## 本地与测试

安装：`uv sync --frozen --extra dev`。测试：`uv run python scripts/test_isolated.py -q --tb=short`。
测试入口创建随机项目和账号，校验数据库内部运行标记，然后才重建专用 schema。
每用例清理专用 DB 与 Redis；测试不读取 `.env`，DNS 防护阻断外部模型请求。
Windows 使用 SelectorEventLoop；生产 worker 必须运行在 Linux 容器。

禁止直接对已有业务库执行测试 fixture；不要用现有 `software_designer_test` 替代随机测试环境。

## 升级顺序

1. 固定旧应用版本，停写并制作数据库与素材配套备份，在副本验证恢复。
2. 准备 `deploy/production.env` 和 `deploy/redis.acl`，使用示例格式但替换全部口令，文件权限仅允许运维读取。
3. `docker compose --env-file deploy/production.env -f compose.production.yml build`。发布时使用唯一镜像标签，并记录解析后的镜像 digest。
4. 启动 DB/Redis；迁移使用独立 `island_migrator` 凭据，通过临时进程环境提供 DATABASE_URL，执行 `python -m alembic upgrade head`，不把迁移口令保存到 API 的 env 文件。
5. 使用迁移账号创建 `island_app LOGIN`，授予现有 public schema USAGE 和业务表 SELECT/INSERT/UPDATE/DELETE、序列 USAGE/SELECT，设置迁移账号的 DEFAULT PRIVILEGES。运行账号不授予建表/改表权限。`audit_events` 撤销 UPDATE/DELETE，仅保留 SELECT/INSERT。
6. 首次安装执行知识树同步；已有库运行 `python -m app.core.maintenance backfill-catalog`，仅 checksum 完全匹配的 release 回填。旧快照无法重建时保留人工审核，不允许新的 AI 分类使用错误目录。
7. `python -m app.core.accounts create 管理员账号 --roles administrator`，交互输入密码；考生不带 roles。命令均在安全运维会话执行。
8. 确認 PUBLIC_ORIGIN、HTTPS、备份存储和出网规则，启动 API/worker/dispatcher/Caddy。

现有业务 PostgreSQL 17、UUID、素材路径和 0001–0009 保持；新迁移 0010–0013 为新增模型/字段和约束。
应用启动不自动迁移。0008/0009 及平台迁移均不支持有损 downgrade。

## 出网和文件边界

外部图片只接受 `ALLOWED_IMAGE_HOSTS` JSON 列表内的 HTTPS 主机，每次跳转重复检查，并连接经过校验的 IP、保留原始 Host/TLS SNI，避免连接时二次 DNS 解析。生产关闭 198.18/15 开发代理例外。
生产宿主机必须额外限制容器出网，阻断回环、私网、链路本地和保留地址；API/worker 访问 DB/Redis 仅经 backend 网络。
应用层地址绑定不能代替网络隔离。未验证出网 ACL 前，不开放外部图片导入。
生产 Uvicorn 信任代理头，仅允许 Caddy 作为外部入口，严禁另行发布 API 8000 端口；否则必须收紧 forwarded_allow_ips。
原文/图片保存在共享持久卷。`python -m app.infrastructure.storage_audit` 仅生成对账结果，绝不删除。
未引用文件须经过 24 小时保护期和人工核对才能另行清理；作答引用的题图由数据库外键保留。

## API 迁移

旧 v1 同步上传、分类、辅助、图片重试、补充及旧逐题写接口返回 410。
管理模板调用 v2 创建任务，2 秒轮询（后台标签页 8 秒），刷新通过 sessionStorage 恢复当前任务。
普通整题编辑/审核仍使用 v1，携带 `If-Match: <revision>`；缺失返回 428，陈旧返回 409。
公开 v1 题目响应保持裸 JSON；v2 practice 增加 `content_version` 与游标分页。
学习提交要求 Idempotency-Key，成功首次 201、重复 200、不同载荷复用键 409；版本冲突 409。
CSRF：先 GET `/api/v1/auth/csrf`，写请求携带 X-CSRF-Token 和同源 Origin。账号由管理命令创建，不提供自助注册。

## 任务恢复与开关

job 状态为 queued/running/retry_wait/succeeded/failed/superseded/cancelled。数据库租约和 generation 防止重复生效。
dispatcher 每 2 秒检查，未领取任务超过 60 秒可补投；租约 360 秒，Celery 硬超时 240 秒。
暂时性网络/429/5xx 重试 3 次；其他失败保留源文档及 job，人工重试按业务权限检查。
`WRITES_ENABLED=false` 阻止 API 写入、worker 领取和 dispatcher 投递；`TASKS_ENABLED=false` 暂停新任务，`LEARNING_ENABLED=false` 暂停新增作答。
环境开关需要重建相应进程；停写维护时直接停止 API、dispatcher 和 worker，并等待在途写入结束。
禁止通过关闭认证恢复可用性，也不自动回退到同步 AI 长事务。

## 监控、备份与回退

`/health/live` 检查进程，`/health/ready` 检查数据库 schema 和 Redis；worker 不影响只读 API readiness。
内部 `/internal/metrics` 受管理员认证且 Caddy 拒绝外部路由；返回任务状态、最老排队时间、过期租约及 outbox 积压。
初始告警：队列最老等待超过 5 分钟、过期租约持续 2 分钟、磁盘空闲低于 15%、Redis 内存超过 80%、备份超过 26 小时。
外部可用性探测与告警接收地址由部署方配置；本地源码不能证明线上监控已经接通。
技术日志 JSON 输出，不记录密码、会话或原始请求体；容器日志按大小轮转，长期存储最多 30 天由宿主机日志系统执行。
每日调度 `python -m app.core.maintenance prune-ai-responses` 清除 30 天前原始分类响应，保留结构化结果；审计及作答不自动删除。

配套备份工具：`python scripts/paired_backup.py --project <部署名> --env-file deploy/production.env --destination <全新目录>`。
工具暂停该项目写进程，生成 pg_dump、assets.tar 和校验清单，随后启动原服务。备份必须复制到另一主机或独立存储。
恢复先在独立环境校验清单、还原 DB 和素材、运行只读对账、抽查正式题和作答；会话不恢复，要求重新登录。
内测目标 RPO≤24h、RTO≤4h，必须由实际演练记录证明，不能仅因脚本存在即宣称达标。
应用回退使用经过兼容验证的前一镜像；不得回退到无认证或无版本检查实现。数据库恢复前保留恢复点之后新增数据副本并明确其处理方式。

## FBA 来源

参考 FBA 后端 `123a44aed02daf5ea60d2a7469625c933d3bb759` 及文档 `ad443174ef8ecea9c2ad0879198e481db4f8a346` 的分层、权限依赖、会话撤销、事务与 Celery 思路。
本实现没有整体嵌入 FBA，也未采用其 JWT、菜单/部门模型、Celery 内部 monkey patch 或响应包装。
