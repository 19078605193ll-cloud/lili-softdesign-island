# 软件设计师真题题库

本服务面向“软件设计师基础知识”单选题。管理员上传已整理的 UTF-8 Markdown，系统保存原文并提取题干、选项、答案、解析和图片，然后逐小问分类、整题审核。共用题干的 2～5 个小问作为一道组合题展示与提交。来源标记为考生回忆版；允许缺题和题号不连续。

## 启动

环境：Python 3.12/3.13、PostgreSQL 17。安装和启动：

```powershell
python -m pip install -e ".[dev]"
docker compose up -d db
python -m alembic upgrade head
python -m app.knowledge.sync
python -m app.server
```

打开 <http://127.0.0.1:8000/admin/imports> 或 <http://127.0.0.1:8000/docs>。管理界面目前未接入身份认证，仅在本机或可信内网使用。

## 导入和审核

1. 上传一份 `.md`，或 ZIP（内含一份 `.md` 与图片目录）。填写年份、期次和批次编码；再次使用同一组合会补充原试卷。
2. 规则解析创建草稿。格式歧义题块可调用文本模型辅助提取；辅助结果仍需人工核对。上传并不会发布题目。
3. 图片复制到 `IMPORT_STORAGE_ROOT`（默认 `var/imports`），数据库保存相对路径和素材关联。网络图片下载失败时可重试或上传替代图。原资料目录之后可以移走。
4. 审核每道普通题或组合题：共享材料只展示一次，每个小问有独立题号、选项、答案、解析与主知识点。可整题排除并注明原因；发布其余已审核题目。
5. 补充同套题时，同内容跳过；不同内容要先查看差异，再选择保留或替换。替换保留旧版本记录。

如需先核对本地六份文档，不写入数据库：

```powershell
python -X utf8 -m app.imports.markdown_cli 'D:\软考真题' --dry-run --report var/markdown-dry-run.json
```

正式导入待审核草稿：

```powershell
python -X utf8 -m app.imports.markdown_cli 'D:\软考真题' --report var/markdown-import-report.json
```

相同文件哈希再次执行会跳过。导入命令按文件名推断年份和期次，不能推断时要求通过管理页面上传。推荐在管理页面复核元信息后再发布。图片为本地绝对路径的 Markdown 应包含其对应图片目录；本地导入命令也会在指定资料目录中按路径后缀查找。

## 模型配置

规则解析不依赖模型。格式不规范的题块可以配置文本辅助，知识点推荐使用已有知识树：

```dotenv
AI_BASE_URL=https://兼容服务/v1
AI_API_KEY=你的密钥
AI_TEXT_MODEL=文本提取模型
AI_CLASSIFICATION_MODEL=知识点分类模型
```

`AI_TEXT_MODEL` 留空时暂用分类模型。无法配置或调用失败时，题块和原文仍保留，管理员可以手动处理。模型不能创建新知识点，也不能替管理员批准或发布。

## 读取接口

- `GET /api/v1/papers/{paper_id}/practice-questions`：逐页获取整题，包含来源与小问，不含答案。
- `GET /api/v1/practice-questions/{id}`：单道普通题或组合题。
- `POST /api/v1/practice-questions/{id}/check`：一次提交全部小问的选项，逐小问判分。
- `GET /api/v1/practice-questions/{id}/solution`：答案与解析。
- `GET /api/v1/question-assets/{id}`：已发布题目的图片。

旧 PDF/DOCX 上传、选页和视觉解析入口已停用；历史数据迁移后仍可读取。

## 测试

集成测试只接受数据库名以 `_test` 结尾的 PostgreSQL 数据库，会重建其 `public` schema：

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://postgres:postgres@localhost:5432/software_designer_test"
python -X utf8 -m pytest -q
```
