#!/usr/bin/env bash
set -euo pipefail

ARCHIVE="${1:-/tmp/material-extractor.tar.gz}"
APP_DIR="/opt/material-extractor"
STAGE_DIR="/opt/material-extractor.stage"
SERVICE_FILE="/etc/systemd/system/material-extractor.service"
SNIPPET_FILE="/etc/nginx/snippets/material-extractor.location.conf"
NGINX_SITE="/etc/nginx/sites-available/lawsuit.conf"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="/opt/material-extractor.backup-${STAMP}"
NGINX_BACKUP="${NGINX_SITE}.backup-${STAMP}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-https://legal.example.com}"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "请使用 root 运行本脚本" >&2
    exit 1
fi
if [[ ! -f "$ARCHIVE" ]]; then
    echo "部署包不存在：$ARCHIVE" >&2
    exit 1
fi
if [[ ! -f "$NGINX_SITE" ]]; then
    echo "未找到现有律书 Nginx 配置：$NGINX_SITE" >&2
    exit 1
fi

missing_packages=()
command -v python3 >/dev/null 2>&1 || missing_packages+=(python3)
command -v tesseract >/dev/null 2>&1 || missing_packages+=(tesseract-ocr)
command -v pdftoppm >/dev/null 2>&1 || missing_packages+=(poppler-utils)
if command -v tesseract >/dev/null 2>&1 && ! tesseract --list-langs 2>/dev/null | grep -qx 'chi_sim'; then
    missing_packages+=(tesseract-ocr-chi-sim)
fi
if ((${#missing_packages[@]})); then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing_packages[@]}" tesseract-ocr-chi-sim
fi

if ! id materialextractor >/dev/null 2>&1; then
    useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin materialextractor
fi

rm -rf "$STAGE_DIR"
install -d -m 0750 -o root -g materialextractor "$STAGE_DIR"
tar -xzf "$ARCHIVE" -C "$STAGE_DIR"
install -d -m 0700 -o materialextractor -g materialextractor "$STAGE_DIR/outputs"
if [[ -d "$APP_DIR/outputs" ]]; then
    cp -a "$APP_DIR/outputs/." "$STAGE_DIR/outputs/"
fi
chown -R root:materialextractor "$STAGE_DIR"
chown -R materialextractor:materialextractor "$STAGE_DIR/outputs"
find "$STAGE_DIR" -type d -not -path "$STAGE_DIR/outputs*" -exec chmod 0750 {} +
find "$STAGE_DIR" -type f -not -path "$STAGE_DIR/outputs/*" -exec chmod 0640 {} +

if [[ -d "$APP_DIR" ]]; then
    mv "$APP_DIR" "$BACKUP_DIR"
fi
mv "$STAGE_DIR" "$APP_DIR"

install -m 0644 "$APP_DIR/deploy/material-extractor.service" "$SERVICE_FILE"
install -m 0644 "$APP_DIR/deploy/material-extractor.location.conf" "$SNIPPET_FILE"

cp -a "$NGINX_SITE" "$NGINX_BACKUP"
if ! grep -Fq 'include /etc/nginx/snippets/material-extractor.location.conf;' "$NGINX_SITE"; then
    sed -i '/^[[:space:]]*location \/ {/i\    include /etc/nginx/snippets/material-extractor.location.conf;\n' "$NGINX_SITE"
fi
if ! nginx -t; then
    cp -a "$NGINX_BACKUP" "$NGINX_SITE"
    nginx -t
    echo "Nginx 校验失败，已恢复原配置" >&2
    exit 1
fi

systemctl daemon-reload
systemctl enable --now material-extractor.service
systemctl restart material-extractor.service
for _ in {1..20}; do
    if curl --fail --silent http://127.0.0.1:8777/api/health >/dev/null; then
        break
    fi
    sleep 1
done
curl --fail --silent http://127.0.0.1:8777/api/health >/dev/null
systemctl reload nginx

echo "部署完成：${PUBLIC_BASE_URL%/}/extractor/"
echo "原律书服务仍使用 127.0.0.1:8765；材料提取工具使用 127.0.0.1:8777。"
