# 本机新版后台

入口：<http://127.0.0.1:8000/admin/imports>

账号为 `admin`，随机生成的密码保存在项目根目录 `.local-runtime/admin-access.txt`。
该目录和 `deploy/local.env` 已加入 Git/Docker 忽略规则，不提交登录信息。
请使用上述完整地址，避免将 `127.0.0.1` 换成 `localhost` 导致同源校验不一致。

## 当前运行方式

API、Celery worker、dispatcher 和 Redis 由 Docker Compose 管理，项目名 `softdesign-island-app`。
沿用 `softdesign-island-db-1` 内原有 `software_designer` 数据库及 `var/imports` 素材。
API 只监听本机 `127.0.0.1:8000`；原 Windows Python API 已停止，不要再同时运行 `python -m app.server`。
现有 `.env` 继续提供 AI 配置；容器内数据库和 Redis 地址由 `deploy/local.env` 覆盖。
本机应用数据库账号为 `island_local_app`，不使用 postgres 超级用户运行 Web 服务。

## 常用命令

在项目目录运行：

```powershell
# 启动（先启动 Docker Desktop；数据库容器需保持运行）
docker start softdesign-island-db-1
docker compose -f compose.local.yml -p softdesign-island-app up -d

# 修改 Python 代码后重启应用进程；模板刷新页面即可读取新文件
docker compose -f compose.local.yml -p softdesign-island-app restart api worker dispatcher

# 查看状态与日志
docker compose -f compose.local.yml -p softdesign-island-app ps
docker compose -f compose.local.yml -p softdesign-island-app logs --tail=100 api worker dispatcher

# 停止应用，不删除数据库或 Redis 数据
docker compose -f compose.local.yml -p softdesign-island-app stop
```

修改依赖后先构建镜像，再重新创建应用容器：

```powershell
docker compose -f compose.local.yml -p softdesign-island-app build
docker compose -f compose.local.yml -p softdesign-island-app up -d --force-recreate api worker dispatcher
```

## 本次升级记录

2026-09-27 已完成 0009→0013 新增迁移、知识目录快照回填和管理员创建。
升级前数据库与素材配套备份：`var/backups/before_platform_upgrade_20260927_160358`。
其中包含 `database.dump`、`imports.tar`、SHA-256 清单及独立副本恢复升级验证结果。
原有 1 份正式试卷、75 道正式小问和 7 个导入批次保留。

健康检查、管理员登录、7 个 Markdown 批次读取和 Celery ping 均已验证。
本次未新建演示题、未重新导入或发布原题。此配置供本机使用，不代表公网生产部署。
