# 本机新版后台

语音输入新增独立容器：首次运行 `python scripts/init_voice.py`，在根目录 `.env` 填写 `ASR_API_KEY`、`POLISH_API_KEY`；配置、启用和验证步骤见 [语音输入说明](voice-input.md)。Key 留空不影响普通文字提问。

入口：<http://127.0.0.1:18000/admin/imports>

H5学习端：<http://127.0.0.1:18000/h5/>，使用已有账号登录。

账号为 `admin`，随机生成的密码保存在项目根目录 `.local-runtime/admin-access.txt`。
该目录和 `deploy/local.env` 已加入 Git/Docker 忽略规则，不提交登录信息。
请使用上述完整地址，避免将 `127.0.0.1` 换成 `localhost` 导致同源校验不一致。

## 当前运行方式

2026-10-13：本机 Windows 保留端口范围包含 8000，导致 API 容器无法绑定端口。Docker 本地入口和 Vite 默认代理已统一改为 `http://127.0.0.1:18000`（容器内部仍为 8000）。数据库已为 `20261013_0021`。启动后可用 `Invoke-RestMethod http://127.0.0.1:18000/health/ready` 检查，应返回 `status: ready`。

主动回忆本轮答案已支持跨空、刷新及返回恢复，开始复测时清空旧回填。规则未匹配的答案由 Qwen 结合上下文复核，判分失败可重试且不计错。本地 `AI_RECALL_MODEL`、`AI_TEXT_MODEL` 均为 `qwen3.8-flash`，对应思考开关为 `false`。已完成独立副本恢复和迁移验证、业务库迁移、镜像构建及应用容器重建，健康检查和 Celery ping 通过。升级前配套备份为 `var/backups/before_recall_0021_20261013_195735/`，原 485 道小问、8 份试卷、44 个题图、162 条正式作答和 30 条主动回忆提交数量一致。功能和回退配置见 [主动回忆说明](active-recall.md)。

开发当前前端源码时，先启动下文 Docker 服务，再另开 PowerShell：

```powershell
cd D:\softdesign-island\web\h5
npm run dev
```

访问 <http://127.0.0.1:5173/h5/>。保留该终端；如未安装前端依赖，先执行 `npm ci`。18000 端口的 H5 页面使用镜像构建时的前端资源，修改前端源码后请使用 5173 开发入口或重新构建镜像。

2026-10-02注册与用户管理已部署：数据库版本为0017，支持 H5 自助注册、用户名或邮箱登录，以及管理员查看和启停用户。详见 [注册说明](user-registration.md)。用户名编辑和头像裁剪保留，历史章节中的0015、0016要求仅对应当时版本，当前健康检查要求0017。

API、Celery worker、dispatcher 和 Redis 由 Docker Compose 管理，项目名 `softdesign-island-app`。
沿用 `softdesign-island-db-1` 内原有 `software_designer` 数据库及 `var/imports` 素材。
API 只监听本机 `127.0.0.1:18000`；原 Windows Python API 已停止，不要再同时运行 `python -m app.server`。
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

2026-09-29：备份至 `var/backups/before_h5_0014_20260929_171418/`，完成独立副本恢复与升级验证，再将业务库升级至0014。迁移前后原有109道小问、2份试卷、44个题图和8个导入批次保持一致。新版镜像包含H5资源及两份Prompt，API/worker/dispatcher已重建，模型配置已生效。

真实题库判分、错题重练解除、模型引导、图表追问、教学变式题校验与跨用户复用均已验收。两个专用验收账号已停用，原管理员学情不受影响。详见 [H5验证记录](h5-verification.md)。

修改 `.env` 模型配置后，须重新创建容器使环境变量生效，而不是仅执行restart：

```powershell
docker compose -f compose.local.yml -p softdesign-island-app up -d --force-recreate api worker dispatcher
```

健康检查、管理员登录、7 个 Markdown 批次读取和 Celery ping 均已验证。
本次未新建演示题、未重新导入或发布原题。此配置供本机使用，不代表公网生产部署。

## 2026-09-30 已部署0015

已按用户授权完成：停写配套备份、独立PostgreSQL恢复和迁移验证、业务库0015迁移、镜像构建及API/worker/dispatcher重建。
备份目录：`var/backups/before_h5_0015_20260930_122339/`，包含数据库、素材归档与SHA-256清单。素材逐文件内容校验通过。
迁移前后183道小问、3份试卷、44个题图、45条正式作答及其他已有学习记录数量一致。
健康检查及Celery ping通过，Chromium/WebKit页面和真实续练/类似题接口通过。验收账号已重新停用并撤销会话，正式作答数保持45。
报告与截图：`var/h5-0015-acceptance/`。真机键盘与安全区仍需手机体验确认。

以下为本次升级要求，现已执行：

移动端固定布局、续练和统一类似题入口需要增量迁移 `20260930_0015`，不能只重启应用。
开发阶段在隔离数据库验证迁移与业务测试，随后完成本机业务库升级及镜像重建。

发布顺序：保存数据库/素材配套备份并验证恢复 → 停止写进程 → 执行 `alembic upgrade head` → 构建新版镜像并重建 API、worker、dispatcher → 检查 `/health/ready` 和 `/h5/`。
镜像构建会打包 H5 静态资源；只运行本地 `npm run build` 不会更新现有容器中的前端文件。新版健康检查要求0015，迁移不会删除旧作答或题库。

## 2026-09-30 管理员试卷管理已部署

已新增已发布试卷逻辑删除、已删除列表恢复及标题编辑同步。复用既有状态，无新增迁移，数据库版本仍为0015。
完成镜像构建和API/worker/dispatcher重建，健康检查与Celery ping通过。部署前后3份真实试卷标题/状态及51条正式作答保持一致。
部署后只读浏览器确认3条已发布记录均有删除按钮、标题弹窗回填正常、已删除列表可访问，无pageerror；未对真实试卷试删或改名。
隔离测试通过46项回归、8项前端单测；最终布局调整后再次通过Chromium/WebKit管理及H5流程。
详见 [管理员试卷管理说明](admin-paper-management.md)。运行环境截图及报告位于 `var/admin-paper-acceptance/`。
