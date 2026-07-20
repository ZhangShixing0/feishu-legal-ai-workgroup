# 部署配置模板

本目录保存律书生成服务与飞书工作群编排服务的 systemd / Nginx 模板；材料提取服务的模板位于 `案件材料提取填表工具/deploy/`。

默认拓扑：

- `/opt/lawsuit-app`：应用目录；
- `lawsuit.service`：`127.0.0.1:8765`；
- `feishu-workgroup.service`：`127.0.0.1:8788`；
- Nginx：唯一公网入口，负责 HTTPS 和访问认证。

使用前必须：

1. 把 `legal.example.com` 替换为正式域名；
2. 复核 systemd 的用户、目录、Python 路径和可写目录；
3. 为 `.env.feishu` 设置 `0600` 权限；
4. 配置 TLS 和 Basic Auth 或组织统一身份认证；
5. 运行 `nginx -t` 和三个本机健康检查。

不要在云防火墙开放 8765、8777、8788。完整步骤见 [`../docs/deployment.md`](../docs/deployment.md)。
