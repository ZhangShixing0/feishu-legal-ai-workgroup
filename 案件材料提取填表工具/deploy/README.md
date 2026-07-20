# 服务器部署说明

## 隔离边界

- 现有律书：`/opt/lawsuit-app`、`lawsuit.service`、`127.0.0.1:8765`。
- 材料提取工具：`/opt/material-extractor`、`material-extractor.service`、`127.0.0.1:8777`。
- 公网入口示例：`https://legal.example.com/extractor/`，部署时替换为正式域名。
- 新入口沿用现有 HTTPS 和 Basic Auth，不新增公网端口。

## Linux OCR

服务端自动使用 Tesseract 的 `chi_sim+eng` 语言包。扫描版 PDF 先由 `pdftoppm` 转为图片；默认每个 PDF 最多 OCR 30 页。Windows 本地运行仍使用原来的 Windows 离线 OCR。

从 1.2.1 起，上传接口只负责创建后台任务，页面通过任务状态接口显示真实的文件和 OCR 页码进度。服务使用单个分析工作线程按顺序处理任务，避免同一台小型服务器同时运行多个 Tesseract 进程；相同 ZIP 与 XLSX 重复提交时会恢复原任务，不会重复启动 OCR。刷新当前页面后可从浏览器会话中恢复任务进度。

## 安装文件

- `material-extractor.service`：独立 systemd 服务。
- `material-extractor.location.conf`：仅处理 `/extractor/` 的 Nginx 片段。
- `install-server.sh`：创建独立用户、安装缺少的 OCR 组件、部署服务、备份并校验 Nginx 配置。

安装脚本会先执行 `nginx -t`，失败时恢复原 Nginx 配置；不会修改 `lawsuit.service` 或占用 8765 端口。
