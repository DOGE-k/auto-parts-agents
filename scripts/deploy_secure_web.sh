#!/usr/bin/env bash
# Reviewed rollout helper. Never overwrites scripts/start_app.sh or AIP configuration.
set -Eeuo pipefail
umask 077
ACTION="${1:-prepare}"
ROOT="${APP_ROOT:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)}"
STATE="${SECURE_WEB_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/auto-parts-secure-web}"
DOMAIN="${APP_DOMAIN:?Set APP_DOMAIN to the verified public DNS name}"
USER_NAME="${APP_USER:-$(id -un)}"
GROUP_NAME="$(id -gn "$USER_NAME")"
BASE="$ROOT/services/OpenMes/docker-compose.yml"
TEMPLATE="$ROOT/deployment/secure-web"
SOCKET=/run/auto-parts-api/backend.sock
COMPOSE=(docker compose -p openmes --env-file "$ROOT/services/OpenMes/.env" -f "$BASE" -f "$STATE/openmes.override.yml")
log() { printf '[secure-web] %s\n' "$*"; }
fail() { log "FAILED: $*" >&2; exit 1; }
[[ "$ROOT" = /* && "$STATE" = /* ]] || fail 'Use absolute paths'
[[ "$ROOT$STATE" != *$'\n'* && "$ROOT$STATE" != *' '* ]] || fail 'This reviewed server helper requires paths without spaces/newlines'
[[ "$DOMAIN" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ && "$DOMAIN" == *.* ]] || fail 'Invalid DNS name'
[[ -f "$BASE" && -x "$ROOT/.venv/bin/python" ]] || fail 'Expected OpenMES compose and Linux project .venv were not found'
prepare() {
    command -v docker >/dev/null; command -v curl >/dev/null; command -v npm >/dev/null
    docker compose version --short | python3 -c 'import re,sys; m=re.match(r"v?(\d+)\.(\d+)\.(\d+)",sys.stdin.read().strip()); assert m and tuple(map(int,m.groups())) >= (2,24,4), "Compose >=2.24.4 is required"'
    mkdir -p "$STATE"; chmod 700 "$STATE"
    python3 - "$TEMPLATE" "$STATE" "$ROOT" "$DOMAIN" "$USER_NAME" "$GROUP_NAME" <<'PY'
from pathlib import Path
import sys
source,state,root,domain,user,group=sys.argv[1:]
values={'@@ROOT@@':root,'@@STATE@@':state,'@@DOMAIN@@':domain,'@@USER@@':user,'@@GROUP@@':group}
for path in Path(source).glob('*.template'):
    text=path.read_text()
    for key,value in values.items():text=text.replace(key,value)
    if '@@' in text:raise SystemExit('Unresolved deployment placeholder')
    (Path(state)/path.name.removesuffix('.template')).write_text(text)
PY
    "${COMPOSE[@]}" config --quiet
    # Inspect merged ports/mounts in memory. Do not print resolved environment secrets.
    python3 - "$BASE" "$STATE" "$ROOT" <<'PY'
import json,pathlib,subprocess,sys
base,state,root=sys.argv[1:]
command=['docker','compose','-p','openmes','--env-file',str(pathlib.Path(base).parent/'.env'),'-f',base,'-f',state+'/openmes.override.yml','config','--format','json']
result=subprocess.run(command,capture_output=True,text=True)
if result.returncode:raise SystemExit('Resolved Compose validation failed; raw config withheld')
service=json.loads(result.stdout)['services']['caddy']
expected={(80,'80','0.0.0.0','tcp'),(443,'443','0.0.0.0','tcp'),(8081,'18081','127.0.0.1','tcp')}
actual={(int(p['target']),str(p['published']),p.get('host_ip'),p.get('protocol','tcp')) for p in service.get('ports',[])}
if actual!=expected:raise SystemExit('Caddy port list does not exclusively match the reviewed safe mappings')
mounts={v['target']:v for v in service['volumes']}
for target,source in {'/etc/caddy/Caddyfile':state+'/Caddyfile','/srv/auto-parts':root+'/frontend/dist','/run/auto-parts-api':'/run/auto-parts-api'}.items():
    v=mounts.get(target,{})
    if v.get('type')!='bind' or str(pathlib.Path(v.get('source','')).resolve())!=str(pathlib.Path(source).resolve()) or not v.get('read_only'):
        raise SystemExit('Caddy bind mount does not match reviewed read-only source')
if str(service.get('user','')) not in {'','0','root','0:0','root:root'}:
    raise SystemExit('Non-root Caddy requires explicit socket group mapping; not assumed safe')
if subprocess.check_output(['docker','exec','openmmes-caddy','id','-u'],text=True).strip()!='0':
    raise SystemExit('Current Caddy UID cannot be assumed to access the private socket')
print('Merged Caddy ports, read-only mounts and UID0 socket-access prerequisite verified')
PY
    # Parse the exact Caddy grammar without altering the running Caddy instance.
    docker exec -i openmmes-caddy caddy adapt --config /dev/stdin --adapter caddyfile < "$STATE/Caddyfile" > "$STATE/caddy-adapted.json"
    log "Prepared configuration in $STATE; no services changed"
}
stop_legacy() {
    python3 - "$ROOT" <<'PY'
import os,pathlib,re,signal,subprocess,sys,time
root=pathlib.Path(sys.argv[1]).resolve()
listing=subprocess.check_output(['ss','-H','-lntp'],text=True)
pids=set()
for line in listing.splitlines():
    fields=line.split()
    if len(fields)<4 or fields[3].rsplit(':',1)[-1] not in {'5173','9000'}:continue
    matches=re.findall(r'pid=(\d+)',line)
    if not matches:raise SystemExit('Cannot determine legacy listener owner; stop it manually')
    pids.update(map(int,matches))
for pid in pids:
    proc=pathlib.Path('/proc')/str(pid)
    try:
        cwd=(proc/'cwd').resolve(strict=True)
        command=(proc/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
        status=(proc/'status').read_text()
        parent=int(re.search(r'^PPid:\s+(\d+)',status,re.M).group(1))
        parent_command=(pathlib.Path('/proc')/str(parent)/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
    except FileNotFoundError:continue
    if not cwd.is_relative_to(root) or not any(x in command+' '+parent_command for x in ['uvicorn','vite']):
        raise SystemExit('Refusing to stop an unrecognized listener')
    os.kill(pid,signal.SIGTERM)
for _ in range(50):
    remaining=subprocess.check_output(['ss','-H','-lnt'],text=True)
    if not any(len(f:=line.split())>=4 and f[3].rsplit(':',1)[-1] in {'5173','9000'} for line in remaining.splitlines()):break
    time.sleep(.1)
else:raise SystemExit('Legacy listener remains; no force-kill performed')
print('Legacy project listeners stopped; no public fallback started')
PY
}
apply() {
    [[ -n "${SECURE_WEB_BACKUP_DIR:-}" && -d "$SECURE_WEB_BACKUP_DIR" ]] || fail 'Set SECURE_WEB_BACKUP_DIR to the verified private DB/config backup directory'
    [[ -f "$STATE/Caddyfile" && -f "$STATE/auto-parts-api.service" ]] || fail 'Run prepare and review its files first'
    (cd "$ROOT/frontend" && VITE_API_BASE_URL=/api VITE_OPENMES_URL=http://127.0.0.1:18081 npm run build)
    [[ -s "$ROOT/frontend/dist/index.html" ]] || fail 'Production frontend build is absent'
    # The new nullable owner column does not assign legacy sessions to arbitrary users.
    (cd "$ROOT/backend" && "$ROOT/.venv/bin/python" -m alembic upgrade head)
    sudo install -m 0644 "$STATE/auto-parts-api.service" /etc/systemd/system/auto-parts-api.service
    sudo systemctl daemon-reload
    sudo systemctl enable auto-parts-api.service
    sudo systemctl restart auto-parts-api.service
    for _ in $(seq 1 25); do
        if curl --silent --fail --max-time 2 --unix-socket "$SOCKET" http://localhost/api/health >/dev/null; then break; fi
        sleep 1
    done
    curl --silent --fail --max-time 5 --unix-socket "$SOCKET" http://localhost/api/health >/dev/null
    [[ -S "$SOCKET" ]] || fail 'Backend Unix socket is missing'
    [[ "$(stat -c '%a' /run/auto-parts-api)" == 750 ]] || fail 'Socket directory permissions must be 750'
    stop_legacy
    # Caddy's existing data/config volumes and MES data volumes stay in place.
    "${COMPOSE[@]}" up -d --no-deps caddy
    docker exec openmmes-caddy sh -c 'test -x /run/auto-parts-api && test -S /run/auto-parts-api/backend.sock'
    log 'Caddy recreation requested; waiting for HTTPS and the anonymous-access gate'
    for _ in $(seq 1 25); do
        code=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 3 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/auth/me" || true)
        [[ "$code" == 401 ]] && break
        sleep 1
    done
    verify
}
verify() {
    curl --silent --fail --max-time 5 --unix-socket "$SOCKET" http://localhost/api/health >/dev/null
    curl --silent --fail --max-time 5 http://127.0.0.1:8080/api/method/ping >/dev/null
    curl --silent --fail --max-time 5 http://127.0.0.1:18081/api/health >/dev/null
    code=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/api/real-orders/assistant/sessions")
    [[ "$code" == 401 ]] || fail "Anonymous history gate returned $code instead of 401"
    code=$(curl --silent --output /dev/null --write-out '%{http_code}' --max-time 10 --resolve "$DOMAIN:443:127.0.0.1" "https://$DOMAIN/")
    [[ "$code" == 200 ]] || fail 'HTTPS frontend did not return 200'
    python3 - <<'PY'
import json,subprocess
for name,container_port in [('erpnext-frontend-1','8080/tcp'),('openmmes-caddy','8081/tcp')]:
    d=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
    ports=d['NetworkSettings']['Ports'].get(container_port) or []
    if not ports or any(p['HostIp'] not in {'127.0.0.1','::1'} for p in ports):raise SystemExit('Management service is not bound exclusively to loopback')
for line in subprocess.check_output(['ss','-H','-lnt'],text=True).splitlines():
    f=line.split()
    if len(f)>=4 and f[3].rsplit(':',1)[-1] in {'5173','9000'}:raise SystemExit('Legacy frontend/backend TCP listener still exists')
print('Loopback management bindings and absence of 5173/9000 listeners verified')
PY
    systemctl is-enabled auto-parts-api.service >/dev/null
    systemctl is-active auto-parts-api.service >/dev/null
    log 'Local checks passed; independently verify public reachability, authenticated SSE and platform mTLS calls'
}
safe_stop() {
    [[ -f "$STATE/Caddyfile.maintenance" ]] || fail 'Prepared maintenance config is missing'
    # Do not restore the old public MES Caddyfile or old 5173/9000 listeners.
    cp "$STATE/Caddyfile.maintenance" "$STATE/Caddyfile"
    docker exec openmmes-caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
    sudo systemctl stop auto-parts-api.service
    log 'Safe maintenance rollback applied: HTTPS returns503, MES stays loopback, public legacy ports stay closed'
}
case "$ACTION" in
    prepare) prepare ;;
    apply) apply ;;
    verify) verify ;;
    safe-stop) safe_stop ;;
    *) fail 'Usage: deploy_secure_web.sh prepare|apply|verify|safe-stop' ;;
esac
