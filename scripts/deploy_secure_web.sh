#!/usr/bin/env bash
# Historical helper retired at the user's request. Never change live services here.
set -euo pipefail
printf '%s\n' '本轮正式 HTTPS/UDS 迁移已撤回，此历史入口不再执行部署。' >&2
printf '%s\n' '恢复原比赛演示方式请查 deployment/secure-web/README.md。' >&2
exit 2
