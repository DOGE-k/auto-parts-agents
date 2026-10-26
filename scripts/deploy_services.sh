#!/usr/bin/env bash
# 一键部署外部真实系统：OpenMES + ERPNext（各自独立 compose project，Linux/macOS）
# 用法：bash scripts/deploy_services.sh
# 幂等：已存在的配置文件不覆盖；重复运行安全。
# 与 scripts/deploy_services.ps1 等价；设计说明见该文件头部注释。
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
openmesDir="$root/services/OpenMes"
frappeDir="$root/services/frappe_docker"

if ! docker version --format 'ok' >/dev/null 2>&1; then
  echo "[失败] Docker 不可用，请先安装并启动 Docker" >&2
  exit 1
fi

missing=0
if [ ! -d "$openmesDir" ]; then
  echo "[失败] 缺少 services/OpenMes。请先克隆上游仓库：" >&2
  echo "       git clone https://github.com/Mes-Open/OpenMes.git services/OpenMes" >&2
  missing=1
fi
if [ ! -d "$frappeDir" ]; then
  echo "[失败] 缺少 services/frappe_docker。请先克隆上游仓库：" >&2
  echo "       git clone https://github.com/frappe/frappe_docker.git services/frappe_docker" >&2
  missing=1
fi
[ "$missing" -eq 0 ] || exit 1

if [ ! -f "$openmesDir/.env" ]; then
  cp "$openmesDir/.env.example" "$openmesDir/.env"
  echo "[待办] 已生成 services/OpenMes/.env——请设置 ADMIN_PASSWORD 等项（OpenMES 管理员密码）。"
fi

if [ ! -f "$frappeDir/.env" ]; then
  cp "$root/scripts/templates/erpnext.env.example" "$frappeDir/.env"
  echo "[待办] 已生成 services/frappe_docker/.env——请设置 DB_PASSWORD（MariaDB root 密码）。"
fi

secureState="${SECURE_WEB_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/auto-parts-secure-web}"
secureOverride="$secureState/openmes.override.yml"
openmesCompose=(docker compose --project-name openmes --env-file "$openmesDir/.env"
  -f "$openmesDir/docker-compose.yml")
openmesUi="http://127.0.0.1/"
if [[ -f "$secureOverride" ]]; then
  openmesCompose+=(-f "$secureOverride")
  openmesUi="http://127.0.0.1:18081/"
  echo "[部署] OpenMES 使用现有正式网页安全 override，管理入口保持回环访问。"
elif [[ -d "$secureState" || -f /etc/systemd/system/auto-parts-api.service ]]; then
  echo "[失败] 检测到正式部署状态但安全 override 缺失；拒绝回退为公网 OpenMES。请核对 SECURE_WEB_STATE。" >&2
  exit 1
else
  echo "[部署] 启动本地 OpenMES（project: openmes，端口 80/443）..."
fi
"${openmesCompose[@]}" up -d

echo "[部署] 启动 ERPNext（project: erpnext，端口 8080，首次会拉取镜像）..."
docker compose --project-name erpnext \
  -f "$frappeDir/compose.yaml" \
  -f "$frappeDir/overrides/compose.mariadb.yaml" \
  -f "$frappeDir/overrides/compose.redis.yaml" \
  -f "$frappeDir/overrides/compose.noproxy.yaml" \
  --env-file "$frappeDir/.env" up -d

echo ""
echo "两个系统已启动。首次部署还需手工初始化（详见 README.md 第 3 节）："
echo "  OpenMES: 管理员按 services/OpenMes/.env 的 ADMIN_USERNAME/ADMIN_PASSWORD 自动创建；登录页 $openmesUi"
echo "  ERPNext 建站（一次性，站点名必须叫 localhost）："
echo "    docker compose --project-name erpnext exec backend bench new-site localhost --mariadb-user-host-login-scope=% --db-root-password <DB_PASSWORD> --admin-password <管理员密码> --install-app erpnext"
echo "  之后回到 README.md 第 4 节继续。"
