# 部署指南

## 推荐拓扑

生产环境建议使用一台受控 Linux 服务器运行三个独立 systemd 服务，由同一个 Nginx HTTPS 入口代理；不直接开放 Python 服务端口。

```text
Internet / Feishu
       |
   HTTPS :443
       |
     Nginx
  ├─ /              -> 127.0.0.1:8765  lawsuit.service
  ├─ /extractor/    -> 127.0.0.1:8777  material-extractor.service
  └─ /ai-workgroup/ -> 127.0.0.1:8788  feishu-workgroup.service
```

仓库中的 `deploy/` 与 `案件材料提取填表工具/deploy/` 提供 systemd 和 Nginx 模板。部署前必须替换示例域名、目录、用户和访问认证配置。

## 系统依赖

- Python 3.10+
- Nginx
- Tesseract OCR 与 `chi_sim`、`eng` 语言包
- Poppler（`pdftoppm`）
- 可选：Certbot 或组织统一 TLS 终止服务

Ubuntu/Debian 示例：

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv nginx tesseract-ocr tesseract-ocr-chi-sim poppler-utils
```

## Python 环境

```bash
git clone https://github.com/ZhangShixing0/feishu-legal-ai-workgroup.git /opt/lawsuit-app
cd /opt/lawsuit-app
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.feishu.example .env.feishu
chmod 600 .env.feishu
```

编辑 `.env.feishu`：

- 设置两个飞书应用的 App ID 与 App Secret；
- 将 `FEISHU_WORKGROUP_PUBLIC_URL` 指向 `/ai-workgroup`；
- 将 `FEISHU_EXTRACTOR_REVIEW_URL` 指向 `/extractor`；
- 设置 `FEISHU_ALLOWED_CHAT_IDS`；
- 生产环境保持 `FEISHU_WORKGROUP_LOCAL_MODE=0`。

## systemd

模板默认使用 `/usr/bin/python3`。若采用项目虚拟环境，应把 `ExecStart` 改成 `/opt/lawsuit-app/.venv/bin/python`，并复核 `User`、`Group`、`WorkingDirectory` 和 `ReadWritePaths`。

```bash
sudo cp deploy/lawsuit.service /etc/systemd/system/
sudo cp deploy/feishu-workgroup.service /etc/systemd/system/
sudo cp 案件材料提取填表工具/deploy/material-extractor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lawsuit material-extractor feishu-workgroup
```

## Nginx 与 TLS

1. 把 `deploy/lawsuit.nginx.conf` 中的 `legal.example.com` 替换为正式域名。
2. 在同一个 `server` 块中包含材料提取和工作群 location 片段。
3. 配置 HTTPS、Basic Auth 或组织统一身份认证。
4. 运行 `nginx -t` 后再 reload。

不要在云防火墙开放 8765、8777、8788。

## 健康检查

```bash
curl --fail http://127.0.0.1:8765/api/schema >/dev/null
curl --fail http://127.0.0.1:8777/api/health >/dev/null
curl --fail http://127.0.0.1:8788/health >/dev/null
systemctl --no-pager --full status lawsuit material-extractor feishu-workgroup
```

上线前再执行：

```bash
.venv/bin/python tools/run_checks.py --require-node
```

如果服务器不安装 Node.js，可先在 CI 或开发机完成前端测试，再在服务器运行 Python 测试与安全审计。

## 运维建议

- 为 `outputs/` 和工作群数据目录设置明确的留存与清理周期。
- 监控磁盘空间、OCR 耗时、飞书 API 错误率、失败案件数和服务重启次数。
- 升级前备份环境配置和必要的 SQLite 数据；不要把备份放回 Git 仓库。
- 每次升级先运行测试，再滚动重启服务并执行健康检查。
- App Secret 轮换后同步更新 EnvironmentFile 并重启工作群服务。
