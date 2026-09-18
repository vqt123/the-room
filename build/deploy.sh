#!/bin/sh
# Create (or reconcile) the serverless deployment for the pushed release and wait for it. One always-warm worker.
set -e
cd "$(dirname "$0")"
if [ "${ENV:-staging}" = "prod" ]; then export COMFY_DEPLOY_URL="${COMFY_DEPLOY_URL:-https://platformapi.comfy.org/deploy}"; else export COMFY_DEPLOY_URL="${COMFY_DEPLOY_URL:-https://stagingplatformapi.comfy.org/deploy}"; fi
if [ "${ENV:-staging}" = "prod" ]; then export COMFY_BUILDER_URL="${COMFY_BUILDER_URL:-https://platformapi.comfy.org/builder}"; else export COMFY_BUILDER_URL="${COMFY_BUILDER_URL:-https://stagingplatformapi.comfy.org/builder}"; fi
GPU="${GPU:-rtx-pro-6000-server}"; REGION="${REGION:-US-NE-1}"
.venv/bin/comfy deploy up build_spec.json --gpu "$GPU" --region "$REGION" --min 1 --max 1 --watch "$@"
