# AI 输入框语音输入

发送按钮前的麦克风用于口述提问：点击开始，再次点击停止。服务按 ASR → 文本 LLM 整理返回最终文字，插入开始录音时的光标或替换选区，由用户修改后手动发送。

录音、排队和转写期间锁定文字编辑、发送和快捷提问；切题、离开页面或页面隐藏会取消录音。草稿继续保存到当前题目的 sessionStorage。超过 2000 字的结果单独显示并允许复制，不截断或覆盖草稿。整理失败会保留 ASR 原文并提示。

## 本机启用

根目录 `.env` 中填入 `ASR_API_KEY` 和 `POLISH_API_KEY`。默认 ASR 为 DashScope 的 `qwen-audio-3.0-asr-flash-filetrans`，整理使用 OpenAI-compatible `gpt-4.1-mini`；可修改对应 Provider、Base URL 和 Model。它们与教学模型独立，不自动借用已有密钥。

Qwen 文本整理默认关闭思考模式，环境配置模板及 Compose 均设置 `POLISH_ENABLE_THINKING=false`，服务端未配置此值时也默认关闭。Qwen 请求通过 OpenAI SDK 的 `extra_body={"enable_thinking": false}` 发送关闭参数；DeepSeek 保留 `thinking.type=disabled`。其他模型不发送这两种厂商专用参数。无需修改提示词来关闭思考。

`POLISH_API_KEY` 必须属于 `POLISH_BASE_URL` 对应的服务商。DMX 密钥不能用于 `api.openai.com`，即使接口格式兼容也会返回 `401 / invalid_api_key`。本机 DMX 整理示例（密钥单独填写，不提交）：

```dotenv
POLISH_ENABLED=true
POLISH_BASE_URL=https://www.dmxapi.cn/v1
POLISH_MODEL=qwen3.8-flash
POLISH_ENABLE_THINKING=false
```

```powershell
python scripts/init_voice.py
docker compose -f compose.local.yml -p softdesign-island-app build api voice-input
docker compose -f compose.local.yml -p softdesign-island-app up -d --no-deps --force-recreate api voice-input
```

初始化脚本幂等，生成的签名密钥存放于 `.local-runtime/voice.env`，不会打印密钥，目录已忽略提交及 Docker 构建上下文。保留此文件，常规重启不会轮换密钥。

更新语音 Provider 配置后，重建容器读取新环境：

```powershell
docker compose -f compose.local.yml -p softdesign-island-app up -d --no-deps --force-recreate voice-input
docker compose -f compose.local.yml -p softdesign-island-app logs --tail=50 voice-input
```

只修改 `.env` 后须重新创建容器，普通 `restart` 不会加载新的环境变量。修改服务端代码或默认值时，先执行 `docker compose -f compose.local.yml -p softdesign-island-app build voice-input`，再重新创建语音容器。readiness 只检查必要配置存在，不代表上游密钥已通过鉴权；还需实际调用验证。整理失败可在日志 `POLISH_PROVIDER event=failure` 中查看 `reason`、`status_code`、`provider_code` 和请求 ID，日志不输出密钥或转写内容。

浏览器访问 `http://127.0.0.1:8000/h5/` 或 Vite 的 `http://127.0.0.1:5173/h5/`。语音容器不发布宿主机端口，网站后端代理 HTTP 与 WebSocket，AudioWorklet 随 H5 打包为本地静态资源。Vite 已启用 WebSocket 代理。

Key 留空时容器仍可启动，但语音 readiness 为 503。网站和普通提问可用，点击麦克风提示配置未就绪。`VOICE_ENABLED=false` 可关闭新语音会话。

## 接口与认证

| 接口 | 行为 |
|---|---|
| `POST /api/v2/voice/session` | 现有 Cookie 登录、Origin 和 CSRF 校验；检查独立服务 readiness；返回 `{token, expires_in}` |
| `WS /api/v2/voice/transcriptions/stream` | 校验有效登录与 Origin；检查 `start.auth_token` 属于当前用户；转发音频和上游事件 |
| `POST /api/v2/voice/transcriptions` | 校验有效登录、Origin、短期 Bearer 令牌归属；转发 multipart 录音及结果 |

令牌有效期 15 分钟，覆盖上游最多 10 分钟录音及结束后的转写/备用上传。网站 Redis 仅记录令牌摘要与用户归属；语音服务独立使用本地容量限制，不继承网站 Redis 配置。每用户每分钟最多初始化 10 次。普通网站写请求仍要求原有 CSRF；SDK 上传使用经过 CSRF 初始化的短期令牌，不能使用其他用户的令牌或过期会话。

WS 初始化等待 8 秒，连接总时限 800 秒；备用上传上游超时 125 秒，上传大小上限 25 MiB。断线会关闭上游连接和转发任务。容量拒绝及重试信息透传，SDK 保留一次 HTTP 降级。日志不记录音频、识别文字或令牌。

## 生产部署

`compose.production.yml` 和 `compose.http.yml` 同样扩展语音服务。先执行初始化脚本，再使用 `--env-file deploy/production.env` 让 Compose 读取语音 Provider 配置与 `PUBLIC_ORIGIN`。服务只接收显式列出的语音变量及自己的签名密钥。

公网浏览器必须通过 HTTPS/WSS 才能申请麦克风；纯 HTTP 公网页面会提示不支持，修改 `ALLOW_INSECURE_HTTP` 不能绕过浏览器限制。Caddy 自动支持 WebSocket，Nginx 模板已包含 Upgrade 转发。初版采用单个语音实例，不增加数据库迁移。

## 验证

```powershell
cd web/h5
npm test
npm run build
npx playwright test e2e/voice-input.spec.ts
cd ../..
uv run python scripts/test_isolated.py tests/test_voice.py tests/test_http_settings.py tests/test_registration.py tests/test_profile.py -q
uv run python scripts/verify_voice.py
```

最后一项临时启动模拟 ASR/整理的语音容器，并在隔离数据库中验证真实 HTTP/WS 协议、音频转换及整理流程；不会使用 Provider Key 或业务库。前置条件是已构建 `softdesign-island-voice:0.1.0`。

Chromium 浏览器测试使用真实 AudioContext/AudioWorklet 和合成音频源。Windows Playwright WebKit 不提供 WebAudio/getUserMedia，相关交互使用仅存在于测试中的音频模拟，并单独验证不支持语音时仍可文字输入。真实 Safari/手机权限体验及真实 ASR＋LLM 识别质量，需配置 Key 后另行验证。

上游固定提交和局部适配记录见 `vendor/lili-voice-input/PROVENANCE.md`。

## 本次验证记录（2026-10-03）

36 项前端单测、33 项后端及账户回归、14 项语音浏览器用例、2 项独立容器真实 HTTP/WS 模拟 Provider 链路通过；现有 H5 全部浏览器回归通过，两种镜像构建成功。已更新本机 API 和语音容器，应用 `/health/ready` 与 `/h5/` 返回 200，AudioWorklet 静态资源返回 JavaScript MIME。

两个 Provider Key 按要求留空，语音 readiness 当前为 503，仅报告缺少 ASR/整理 Key；填 Key 后重新创建语音容器即可。真实 Provider 验收尚未执行。运行验收摘要位于忽略提交的 `.local-runtime/voice-acceptance.json`。

## 整理鉴权与思考配置修复（2026-10-03）

上述缺少密钥记录属于首次接入时的验证。本次已配置真实 Provider：ASR 使用 DashScope，整理使用 DMX 的 `qwen3.8-flash`，并显式关闭思考。15:24:23 的整理失败由 DMX 密钥请求 OpenAI 地址导致，日志记录 `401 / invalid_api_key`；已修正本机地址并重新创建语音容器。

真实整理调用未返回 `reasoning_content`；使用本机合成中文录音，经网站认证网关完成 HTTP 和 WebSocket 的真实 ASR → 整理调用，两者均返回 `polish_status=applied`、`degraded=false`。脱敏验收数据位于 `.local-runtime/voice-polish-live-acceptance.json`。

Provider 参数和失败回退回归使用真实 SDK 加 MockTransport，测试文件位于 `vendor/lili-voice-input/server/tests/test_polishing.py`，可在语音服务环境执行 `python -m unittest discover -s tests`。另执行 `uv run python scripts/verify_voice.py` 验证隔离容器的 HTTP/WS 协议。
