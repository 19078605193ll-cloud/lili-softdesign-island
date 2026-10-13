# 软设岛服务器部署

2026-10-09 已配置正式 HTTPS 入口并修复语音来源白名单；证书、续期、DNS 切换状态和当前验收记录见 [HTTPS 部署说明](https-deployment.md)。下文 HTTP 配置与验收记录为历史部署记录。

服务器：`ubuntu@159.75.2.196`，使用本机默认 SSH 密钥。目录 `/opt/softdesign-island`，Compose 项目名 `softdesign-island`。

用户端：`http://159.75.2.196/h5/`；管理员端：`http://159.75.2.196/admin/imports`。当前公网入口为 HTTP 80；不需要开放 8080。其他站点及 `/opt/canteen-review` 保留。

## 运行配置

采用 `compose.http.yml`，宿主机 Nginx 使用 `deploy/nginx-http.conf`。API 仅发布 `127.0.0.1:18000`，数据库和 Redis 不发布公网端口。生产模式保留认证、权限及 CSRF；仅本次显式设置 `ALLOW_INSECURE_HTTP=true`，允许 HTTP 和非 Secure Cookie。HTTP 不提供传输加密。

`deploy/production.env` 保存运行账号、网站 AI 和语音 Provider 配置；服务器 `deploy/compose.env` 保存 `ISLAND_IMAGE`、`VOICE_IMAGE` 和 `POSTGRES_PASSWORD`，迁移密码不注入 API/worker。配置目录权限 700，环境文件权限 600，不提交 Git。当前禁用外部图片主机白名单，上传的本地图片正常使用；开启外部图片下载前须配置容器出网限制。

worker 当前并发为 6。语音服务仅在容器网络内监听 9100，由 API 和 Nginx 代理 HTTP/WS；独立签名密钥存放在服务器 `.local-runtime/voice.env`。公网 HTTP 页面无法申请麦克风权限，页面会提示需要 HTTPS；普通文字输入正常。

Redis 的 `island_sessions` 用户需要 `~voice:*` 和 `~register:*` 键权限，见 `deploy/redis.acl.example`。服务器 ACL 文件权限为 600、属主为容器 Redis 用户；使用 broker 运维账号执行 `ACL LOAD` 可重新加载而不重启服务。保留现有密码，不输出 ACL 中的凭据。

```bash
cd /opt/softdesign-island
sudo docker compose --env-file deploy/production.env --env-file deploy/compose.env -f compose.http.yml -p softdesign-island ps
sudo docker compose --env-file deploy/production.env --env-file deploy/compose.env -f compose.http.yml -p softdesign-island logs --tail=100 api worker dispatcher voice-input
curl -fsS http://127.0.0.1/health/ready
```

数据库版本为 `20261002_0017`。管理员密码沿用本机账号，保存在本机 `.local-runtime/admin-access.json` / `admin-access.txt`，无需新建账号。历史验收账号 `deploy-check-20260930-0`、`deploy-check-20260930-1` 验收后停用；其测试学习记录保留，未改写原账号作答。

## 备份与恢复

服务器每日 03:15 运行 `/usr/local/sbin/softdesign-island-backup`，短暂停止本项目 API/worker/dispatcher 后备份数据库和素材，再恢复服务。备份位于 `/opt/softdesign-island-backups/daily`，保留约 14 天。任务同时记录磁盘使用情况，达到 85% 写入系统警告。日志在 `/var/log/softdesign-island-backup.log`。

本机计划任务 `SoftdesignIsland-DownloadBackups` 每日 09:00 执行 `deploy/download-backups.ps1`，下载缺失备份并校验 SHA-256。需要本机开机、当前用户登录且网络可达；错过时间后补运行。本机备份在 `var/backups/server-daily`，不自动删除，不代表持续在线的异地备份系统。

手工备份：`sudo /usr/local/sbin/softdesign-island-backup`。恢复必须先验证清单，在独立数据库以 `pg_restore --no-owner --no-acl` 恢复；素材以应用用户解包并验证。生产恢复前停写，成套恢复数据库与素材，按部署时的权限模型重新授权，然后验证健康、登录、题图和作答；会话不恢复。

## 发布和回退

首次镜像标签为 `softdesign-island:e19edab-http-20260930`，基于提交 `e19edab` 加本次 HTTP 部署改动，不是声称该提交本身包含这些改动。服务器 `release.json`、`source.patch` 和 `source.tar.gz` 记录可复现来源。

升级先备份，构建唯一镜像标签，使用迁移账号执行所需迁移，再重新创建应用服务。没有兼容性证明时不降级数据库。回退先保存上线新增数据，选择兼容镜像；首次部署撤回时只停止软设岛服务并移除 Nginx 的软设岛站点链接，检查配置后 reload，不停止旧项目，不删除数据卷。

## 验证记录（2026-09-30）

- HTTP 配置/认证及平台回归 16 项通过；导入审核发布及试卷管理回归 34 项通过。第一次平台运行有一项租约恢复测试失败，单独及整组复跑均通过，未修改其业务逻辑。
- 迁移时 44 张表记录数核对，172 个素材文件逐个 SHA-256 核对通过；原有 3 个账号、52 条作答、44 个题图保留。
- 服务器真实 AI 引导、追问、变式题生成与复用通过，使用专用验收账号。
- 经 SSH 隧道访问服务器的 Chromium 移动端登录/刷题页刷新和桌面管理员登录通过，无页面脚本错误。浏览器使用正式 URL，以验收工具把网络请求转发至隧道，未关闭服务端来源校验。
- 旧项目健康检查保持 200；两个项目并行运行时观测可用内存约 821 MB。
- 公网 8080 尚待云平台入站规则放行；放行后还须直接公网验收，不能以隧道结果代替。
- 首份线上备份 `20260930_203652` 已下载到本机，整份 SHA-256 校验通过；独立 PostgreSQL 恢复后 44 张表记录数一致，53 个引用素材成功解包恢复。每日下载脚本已实测，优先使用本机 WSL Ubuntu 的 rsync 差量传输，临时密钥副本在退出时删除。
- **尚未完成正式数据切换**：首次迁移后，本机作答从 52 增至 54，并新增相关 AI 会话、任务和标记。服务器保留首次迁移数据及专用验收记录，未覆盖本机新增数据。公网放行后应暂停本机写入、再次成套备份，先备份服务器，再同步最终本机数据；正式开放前完成最终核对，避免两边同时写入。

## 2026-09-30 ????? 80 ??

??????????? HTTP 80???? Nginx ???? 8080?PUBLIC_ORIGIN ?????????? API ? Chromium ????????CSRF?H5 ???????????????? 8080 ????????????????????????????????????????

???????????? 44 ??????? 3 ????59 ????20 ? AI ???172 ????????????????????? API/worker/dispatcher ?????????????????????????????? island_before_port80?????? daily ?????????? var/backups/cutover_*?

???????????????????? Prompt Markdown ?????????????????????

## 2026-10-04 最新代码部署

- 固定提交：`ce881ae3580d5164f9367e50923f96995547b6d4`。发布目录：`/opt/softdesign-island-releases/ce881ae-20261004_114328`。
- 网站镜像：`softdesign-island:ce881ae-20261004_114328`；语音镜像：`softdesign-island-voice:ce881ae-20261004_114328`。API、worker、dispatcher 使用同一网站镜像。
- 本机构建后通过 SSH 传输并核对 SHA-256。服务器相对固定提交的 Compose 调整记录在发布目录 `deployment.patch`：worker 并发 6，语音镜像读取 `VOICE_IMAGE`。
- 数据库由 0016 升级至 `20261002_0017`。独立恢复及迁移验证通过；正式迁移前后 44 张表记录数和数据指纹一致，172 个素材文件逐一校验通过。未导入本地业务数据库。
- 最终停写备份位于发布目录 `final-backup`；本机副本位于 `.local-runtime/redeploy/ce881ae-20261004_114328/server-backup`，包含数据库、素材和 SHA-256 清单。
- 本机已有素材归档与最终备份的 SHA-256 完全一致，复用该归档并下载最新数据库及发布记录；本机完整配套备份逐文件 SHA-256 校验通过。
- 发布前 73 项后端、36 项前端、14 项语音浏览器、2 项真实容器模拟 HTTP/WS 链路测试通过。
- 公网 Chromium 完成学习账号登录、7 份已发布试卷读取、专用账号作答、HTTP 语音提示及普通文字输入验收；原管理员账号登录与用户管理通过，无页面脚本错误。
- 经 SSH 隧道及仅用于验收的本地转发，在 localhost 安全上下文使用合成中文录音完成真实 AudioWorklet → WebSocket → ASR → 整理；HTTP 录音上传也通过。转发使用正式 Origin，未关闭服务端 CSRF、登录或来源校验。两条链路均 `polish_status=applied`、`degraded=false`。
- 公网注册、自动登录、邮箱登录及学习用户无法访问管理员接口通过。测试账号验收后停用并撤销会话，保留测试记录；原用户作答未改写。
- 连续观察 600 秒，readiness 持续正常，无 OOM 或容器重启；观察结束时可用内存约 432 MiB。发布清单、迁移验证、数据指纹、浏览器验收及观察数据保存在发布目录。
- 首次正式迁移启动因 Docker 原始 env-file 不解析 Compose 引号而失败，数据库保持 0016 并恢复原服务；改用 Compose 启动迁移容器后成功。旧 Redis ACL 缺少语音和注册键权限，已补齐并验证。
- 公网仍为 HTTP；本次不配置 HTTPS。旧镜像 readiness 要求 0016，不能直接回退到旧镜像或执行有损数据库降级。恢复前须停写并保存上线新增数据，验证兼容方案后再操作。
