#!/bin/sh
# Push the Build (with the private pack zip) to the builder and cut a Linux/NVIDIA release.
# Staging by default; export COMFY_BUILDER_URL=https://platformapi.comfy.org/builder for prod (needs a prod `comfy cloud login`).
set -e
cd "$(dirname "$0")"
if [ "${ENV:-staging}" = "prod" ]; then export COMFY_BUILDER_URL="${COMFY_BUILDER_URL:-https://platformapi.comfy.org/builder}"; else export COMFY_BUILDER_URL="${COMFY_BUILDER_URL:-https://stagingplatformapi.comfy.org/builder}"; fi
.venv/bin/comfy build push build_spec.json --custom-nodes-dir . --release --target linux/nvidia "$@"
