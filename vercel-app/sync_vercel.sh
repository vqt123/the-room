#!/bin/sh
# Point the shared page at the current deployment (../build/endpoint.txt), refresh the debug metadata, redeploy.
# Env: SCOPE (default vqt123s-projects). Run after ./release.sh or any deployment change.
set -e
cd "$(dirname "$0")"
SCOPE="${SCOPE:-vqt123s-projects}"
EP=$(cat ../build/endpoint.txt); [ -n "$EP" ] || { echo "no endpoint in ../build/endpoint.txt"; exit 1; }
vercel env rm COMFY_BASE_URL production --scope "$SCOPE" --yes >/dev/null 2>&1 || true
printf '%s' "$EP" | vercel env add COMFY_BASE_URL production --scope "$SCOPE" >/dev/null
../build/.venv/bin/python sync_meta.py 2>/dev/null | tail -1
vercel deploy --prod --yes --scope "$SCOPE" 2>&1 | grep -i "Production:" | tail -1
sleep 12
curl -s "https://steal-the-moves-tau.vercel.app/api/debug?password=$(cat ../build/vercel_password.txt)" | ../build/.venv/bin/python -c "import sys,json;d=json.load(sys.stdin);print('page ->', d['endpoint']['url'], d['endpoint'].get('health'))"
