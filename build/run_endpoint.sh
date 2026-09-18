#!/bin/sh
# Submit the real graph (private YouTubeFrame inside the cloud) to the deployment and download the result.
# Usage: ./run_endpoint.sh [profile.png] [youtube url] [timestamp]
set -e
cd "$(dirname "$0")"
if [ "${ENV:-staging}" = "prod" ]; then export COMFY_DEPLOY_URL="${COMFY_DEPLOY_URL:-https://platformapi.comfy.org/deploy}"; else export COMFY_DEPLOY_URL="${COMFY_DEPLOY_URL:-https://stagingplatformapi.comfy.org/deploy}"; fi
if [ "${ENV:-staging}" = "prod" ]; then export COMFY_BUILDER_URL="${COMFY_BUILDER_URL:-https://platformapi.comfy.org/builder}"; else export COMFY_BUILDER_URL="${COMFY_BUILDER_URL:-https://stagingplatformapi.comfy.org/builder}"; fi
PROFILE="${1:-results/profile_vinh_github.png}"; URL="${2:-https://www.youtube.com/watch?v=i0OQmw5WSIQ}"; TS="${3:-2:10}"
mkdir -p run_inputs outputs && cp "$PROFILE" run_inputs/profile.png
.venv/bin/python - "$URL" "$TS" <<'PY'
import json, sys
g = json.load(open("workflow_api.json"))
g["1"]["inputs"]["url"], g["1"]["inputs"]["timestamp"] = sys.argv[1], sys.argv[2]
json.dump(g, open("run_inputs/workflow_run.json", "w"), indent=2)
PY
.venv/bin/comfy deploy run build_spec.json --workflow run_inputs/workflow_run.json --asset-root run_inputs --output-dir outputs --timeout 900
