# HTTPS 域名部署与语音来源检查

正式入口：`https://app.liuli2026.top/h5/`；管理员入口：`https://app.liuli2026.top/admin/imports`。

服务器使用宿主机 Nginx 和 `compose.http.yml`，API 仅发布 `127.0.0.1:18000`。Compose 文件名保留历史名称，公网入口已经使用 HTTPS。其他站点、业务数据库和现有镜像不变。

## 本次修复（2026-10-09）

域名切换后网站容器已经使用 HTTPS `PUBLIC_ORIGIN`，语音容器却仍保留旧 HTTP IP 的 `ALLOWED_ORIGINS`。语音服务 healthy，但令牌接口拒绝新 Origin 并返回 403。通过服务器现有两份环境文件重新创建语音容器后，两者均为 `https://app.liuli2026.top`；真实 HTTPS 上传、WSS 流式转写和浏览器 AudioWorklet 录音回填通过，均 `polish_status=applied`、`degraded=false`。

源站 Cloudflare Origin CA 证书已替换为 Let's Encrypt 公网证书，证书链和域名校验通过，首次证书到期日为 2027-01-07。源站配置对应 `deploy/nginx-https.conf`，包括 HTTP 308 跳转、ACME 挑战例外、静态资源 gzip、语音流 900 秒及上传 150 秒超时。HTML 的 `no-cache`、内容哈希静态文件的长期缓存沿用应用响应头。

**DNS 切换状态：2026-10-09 17:20 左右已切换为 DNS only，公网直连验收失败；17:37 用户确认恢复 Cloudflare 代理，公共 A 记录已恢复为 Cloudflare 地址，但公网仍间歇返回 525，回退尚未恢复稳定访问。** 手机真实 Wi-Fi、5G 和微信内访问结果尚待用户反馈，移动尺寸 Chromium 模拟不能替代真实运营商网络。

现有回归：12 项语音/HTTPS Cookie 后端测试、14 项语音浏览器测试、7 项部署来源检查测试通过。首次源站直连的桌面及移动尺寸 Chromium 各 10 次冷加载成功，页面可操作时间 P95 分别为 1813 ms、1631 ms；这些数据来自当前测试网络，不代表真实手机 5G 测量。

### DNS 切换后的公网 TLS 异常

- 服务器自身正常解析正式域名后，HTTPS 返回 200，健康请求约几十毫秒，证书剩余约 90 天；来源一致性检查继续通过。
- 外部 Windows 的 Chromium、Schannel、Python/OpenSSL 都在带正式域名的 TLS 握手中断开；TLS 1.2 与默认协议均失败。SSL Labs 外部检测在 `159.75.2.196` 上报告 `No certificates found`。
- 同一外部路径不携带 SNI 的 IP 连接能够取得证书，随后正常报告证书域名与 IP 不匹配；没有绕过证书校验来声称验收通过。
- 源站抓包显示正式域名的 ClientHello 到达后出现重置；该流客户端 SYN/数据 TTL 为 113，而重置包 TTL 为 250，支持源站外中间设备注入重置的判断。源站 Nginx 没有发出该重置。不带 SNI 的对照流收到服务器 TLS 响应。
- 腾讯云域名备案/接入拦截是待核查因素，并非已确认的最终原因。需核对 `liuli2026.top` 的 ICP 备案和腾讯云接入状态；腾讯云说明见 https://cloud.tencent.com/document/faq/243/20220 。不通过修改 TLS、关闭鉴权或绕过平台限制来掩盖问题。

这次外部公网复验失败，不能把早先的浏览器成功、源站自检成功或已启动的持续观察写成 DNS 切换后验收通过。恢复代理后必须重新确认公共 DNS、外部 HTTPS、真实语音和手机网络；要再次直连，应先解决域名接入或网络拦截原因。

### 恢复代理后的检查（17:37–17:43）

- 公共 DNS 已恢复 `104.21.18.31`、`172.67.179.233`，代理恢复已确认。SSL 模式应继续保持 Full (strict)。
- 当前电脑明确连接 Cloudflare 地址后，LAX 节点连续 5 次返回 525；服务器经 AMS 节点的 5 次复测中，2 次 200、3 次 525。不是单纯旧 DNS 缓存造成的失败。
- 源站本机分别使用 TLS 1.2、TLS 1.3 和正常证书校验访问均为 200；语音来源检查通过，另一项目健康接口仍为 200。
- 回源抓包显示 Cloudflare TCP/ClientHello 包 TTL 为 50，紧随其后的 RST 包 TTL 为 251，源站没有主动发送该 RST。支持回源链路中间设备干预的判断，不能据此确定具体运营商或云平台，也不能直接认定未备案。
- 第一段持续观察结束，共 59 个样本，50 次成功、9 次失败；包含切换前后不同 DNS 状态，不能视为上线后连续 30 分钟健康。后续观察还出现 525，正式验收未通过。
- 用户表示备案情况“不确定”。下一步在腾讯云 ICP 备案控制台核对主域名备案、接入和阻断状态，再由腾讯云支持核查入站 TLS/SNI 重置；可使用 [故障工单材料](https-network-incident.md)。没有自动向任何服务方发送工单。

Cloudflare 对 525 的说明是代理与源站 SSL 握手失败，见 https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-525/ 。目前不重新切换 DNS only，不降低 SSL 安全等级；证书、语音精确来源和业务数据保持现状。链路恢复后再完成外部语音、手机和 30 分钟连续观察验收。

配置备份：`/opt/softdesign-island-https-backups/20261009_165419`，目录权限 700，包含修复前 Nginx、Compose、环境及容器配置；其中含凭据，不公开或提交 Git。语音签名密钥和 Provider 密钥没有轮换。验收使用已有账号读取历史记录，没有新增作答或发送教学提问。

## DNS 切换顺序

1. 先完成源站公网证书配置，并使用下面的 `curl --resolve` 验证，不跳过证书校验。
2. Cloudflare 的 `app` A 记录指向 `159.75.2.196`，设置 DNS only，TTL 300 秒；源站没有公网 IPv6 时不配置该子域 AAAA。代理开启时查询到的 Cloudflare AAAA 可能是自动生成记录。
3. 核实云安全组开放 80、443，然后从公共 DNS 和真实手机网络确认新的 A 地址及 AAAA 状态。保留其他子域记录。
4. 手机系统浏览器在 Wi-Fi、5G 各打开及刷新 10 次，并单独检查微信内访问；录音权限体验以系统浏览器为主要验收入口。

```bash
curl --resolve app.liuli2026.top:443:159.75.2.196 \
  -fsS -o /dev/null -w 'status=%{http_code} total=%{time_total}\n' \
  https://app.liuli2026.top/h5/
```

## 发布前来源检查

只修改环境文件或执行 `restart` 不会更新已有容器环境。必须用同一组环境文件解析 Compose，并重新创建受影响容器；不要把浏览器安全上下文检查或容器 readiness 当作完整语音验收。

```bash
cd /opt/softdesign-island
sudo docker compose --env-file deploy/production.env --env-file deploy/compose.env \
  -f compose.http.yml -p softdesign-island up -d --no-deps --force-recreate voice-input
sudo python3 scripts/verify_deployment_origin.py
```

检查脚本只读比对 Compose 和运行容器的 Origin，并从 API 容器检查语音 readiness 和 CORS OPTIONS，不创建令牌，不输出密钥。HTTPS 部署要求 `ALLOW_INSECURE_HTTP=false`，语音白名单只允许正式 Origin，不允许通配符。

## 证书续期与持续检查

Certbot 使用 webroot `/var/www/letsencrypt`；证书路径为 `/etc/letsencrypt/live/app.liuli2026.top/{fullchain,privkey}.pem`。保留 Nginx 的 `/.well-known/acme-challenge/` 路由，勿将挑战路径代理给应用或要求登录。

`certbot.timer` 自动续期，成功续期时执行 `/etc/letsencrypt/renewal-hooks/deploy/softdesign-island-nginx`，先 `nginx -t` 再 reload。首次 `certbot renew --dry-run` 已通过。续期账户未登记邮箱，过期检查由下述定时任务承担。

```bash
sudo certbot renew --cert-name app.liuli2026.top --dry-run --no-random-sleep-on-renew
sudo systemctl list-timers --all | grep -E 'certbot|softdesign-island-https'
sudo journalctl -u certbot.service -u softdesign-island-https-check.service --since '1 day ago'
```

`softdesign-island-https-check.timer` 每天北京时间 09:00 后随机延迟最多 5 分钟，检查正式域名证书和 `/health/ready`。证书剩余不足 21 天或访问失败时服务退出非零并记录 systemd 日志；本次没有配置外部消息通知。Cloudflare 会拒绝 Python 默认 User-Agent，检查程序显式使用 `softdesign-island-health/1.0`。

```bash
sudo python3 /opt/softdesign-island/scripts/observe_https.py
sudo python3 /opt/softdesign-island/scripts/observe_https.py --duration 1800 \
  --report /opt/softdesign-island-https-backups/20261009_165419/post-dns-observation.jsonl
```

持续检查记录 DNS 地址、证书剩余天数及健康请求耗时，不记录账号、Cookie、语音或转写内容。30 分钟观察结果和 DNS 切换完成状态须单独记录，不能以已启动观察任务代替验收通过。**从源站自身运行的检查不能发现所有云平台边缘或运营商域名拦截，必须配合外部浏览器和真实手机验收。**

## 回退

源站或直连切换异常时，先恢复 `app` 的 Cloudflare 代理，确认对应主机 SSL 模式为 Full (strict)，待 DNS 缓存更新后验证。新 Let's Encrypt 证书同时适用于代理模式，语音 Origin 修复保留。

Nginx 变更需回退时，使用保存的配置，运行 `nginx -t` 后 reload；旧配置使用 Origin CA，只有 Cloudflare 代理恢复后才可恢复该证书。不要把直连访问切回 Origin CA，不降级数据库或删除数据卷。

若手机仍打不开，按 DNS、TCP 443、TLS、HTTP 状态、脚本加载顺序检查，并按时间对应源站访问日志；请求没有到达源站时优先检查运营商链路及 DNS，源站收到后再检查具体状态和资源。
