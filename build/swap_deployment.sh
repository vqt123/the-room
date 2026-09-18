#!/bin/sh
# Move the live endpoint onto a release, without the CLI's token race.
# Usage: ./swap_deployment.sh <release-id>
set -e
cd "$(dirname "$0")"
REL="$1"
[ -n "$REL" ] || { echo "usage: ./swap_deployment.sh <release-id>"; exit 1; }

OLD=$(cat deployment.txt 2>/dev/null || true)
if [ -n "$OLD" ]; then
  echo "== deleting $OLD"
  .venv/bin/python devplatform.py rm "$OLD" || echo "  (could not delete; continuing)"
fi

# The delete is accepted immediately but the slot comes back a little later, so
# a create right after it fails on the workspace limit of 3.
echo "== creating a deployment on $REL"
n=0
while :; do
  OUT=$(.venv/bin/python devplatform.py up "$REL")
  case "$OUT" in
    *CONCURRENCY_LIMIT*) n=$((n+1)); [ $n -gt 20 ] && { echo "$OUT"; exit 1; }
      echo "  slot still busy, waiting ($n)"; sleep 15;;
    *) break;;
  esac
done
echo "$OUT"
DEP=$(echo "$OUT" | grep -o 'dep-[0-9a-f-]*' | head -1)
[ -n "$DEP" ] || { echo "no deployment id came back"; exit 1; }
echo "$DEP" > deployment.txt

.venv/bin/python devplatform.py wait "$DEP" 1200
echo "https://$DEP.stg.run.comfy.app" > endpoint.txt
echo "endpoint.txt -> $(cat endpoint.txt)"
