#!/bin/sh
# One command for the whole cycle: push the pack (new blob) -> wait for the release to bake -> delete the previous
# deployment (staging allows 3 active) -> create a deployment on the new release -> write endpoint.txt / deployment.txt
# -> run one direct-URL job through the private node as a smoke test -> scale to zero warm workers.
# Env: KEEP_WARM=1 to leave one warm worker (for the showcase). GPU / REGION as in deploy.sh.
set -e
cd "$(dirname "$0")"
if [ "${ENV:-staging}" = "prod" ]; then export COMFY_BUILDER_URL="${COMFY_BUILDER_URL:-https://platformapi.comfy.org/builder}"; else export COMFY_BUILDER_URL="${COMFY_BUILDER_URL:-https://stagingplatformapi.comfy.org/builder}"; fi
if [ "${ENV:-staging}" = "prod" ]; then export COMFY_DEPLOY_URL="${COMFY_DEPLOY_URL:-https://platformapi.comfy.org/deploy}"; else export COMFY_DEPLOY_URL="${COMFY_DEPLOY_URL:-https://stagingplatformapi.comfy.org/deploy}"; fi
GPU="${GPU:-rtx-pro-6000-server}"; REGION="${REGION:-US-NE-1}"
BUILD=$(.venv/bin/python -c "import json;print(json.load(open('build_spec.json'))['id'])")

echo "== 1/5 push (uploads the pack zip if its bytes changed) and cut a release"
.venv/bin/comfy build push build_spec.json --custom-nodes-dir . --release --target linux/nvidia > .push.json
REL=$(.venv/bin/python -c "import json;print(json.load(open('.push.json'))['data']['release']['releaseId'])")
echo "release $REL"

echo "== 2/5 wait for the release artifact (usually ~8 min)"
n=0; until .venv/bin/comfy build release show "$REL" --id "$BUILD" | grep -qE '"pending": 0'; do n=$((n+1)); [ $n -gt 60 ] && { echo "still building after 30 min, giving up"; exit 1; }; sleep 30; done
.venv/bin/comfy build release show "$REL" --id "$BUILD" | grep -o '"artifactCounts": {[^}]*}'
if .venv/bin/comfy build release show "$REL" --id "$BUILD" | grep -q '"failed": [1-9]'; then echo "artifact FAILED; logs:"; .venv/bin/comfy build release logs "$REL" --id "$BUILD" | tail -40; exit 1; fi

echo "== 3/5 delete the previous deployment (if any) to stay under the cap of 3"
if [ -s deployment.txt ]; then
  OLD=$(cat deployment.txt)
  .venv/bin/comfy deploy delete build_spec.json --deployment "$OLD" --yes >/dev/null 2>&1 && echo "deleted $OLD" || echo "no previous deployment to delete"
  : > deployment.txt
fi

echo "== 4/5 create a deployment on $REL and wait until ready (usually ~7 min)"
n=0; until .venv/bin/comfy deploy up build_spec.json --release "$REL" --gpu "$GPU" --region "$REGION" --min 1 --max 1 --watch > .deploy.json 2>&1 && grep -q '"ok": true' .deploy.json; do n=$((n+1)); [ $n -gt 12 ] && { cat .deploy.json; exit 1; }; sleep 20; done
DEP=$(grep -o '"id": "dep-[^"]*"' .deploy.json | head -1 | cut -d'"' -f4)
echo "$DEP" > deployment.txt
.venv/bin/comfy deploy show --deployment "$DEP" | grep -o '"endpointUrl": "[^"]*"' | cut -d'"' -f4 > endpoint.txt
echo "deployment $DEP -> $(cat endpoint.txt)"

echo "== 5/5 smoke test: one tape through the private node (and the older move swap)"
.venv/bin/python run_tape.py results/profile_vinh_github.png || echo "TAPE SMOKE TEST FAILED"
./run_endpoint.sh results/profile_vinh_github.png "https://archive.org/download/BigBuckBunny_124/Content/big_buck_bunny_720p_surround.mp4" "1:05" | grep -o '"status": "[a-z]*"\|"message": "[^"]*"' | head -2 || true
if [ "${KEEP_WARM:-0}" != "1" ]; then .venv/bin/comfy deploy scale build_spec.json --deployment "$DEP" --min 0 --max 1 >/dev/null && echo "scaled to 0 warm workers (KEEP_WARM=1 to keep one)"; fi
echo "done. page: .venv/bin/python app.py"
