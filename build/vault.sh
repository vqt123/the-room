#!/bin/sh
# The vault shot: what the platform knows about the private pack, and what the public registry knows (nothing).
# Usage: ./vault.sh   (ENV=prod for the prod builder)
set -e
cd "$(dirname "$0")"
if [ "${ENV:-staging}" = "prod" ]; then B=https://platformapi.comfy.org/builder; else B=https://stagingplatformapi.comfy.org/builder; fi
export COMFY_BUILDER_URL="${COMFY_BUILDER_URL:-$B}"
BUILD=$(.venv/bin/python -c "import json;print(json.load(open('build_spec.json'))['id'])")
REL=$(.venv/bin/comfy build release ls build_spec.json | .venv/bin/python -c "
import sys,json;rs=json.load(sys.stdin)['data']['releases'];rs.sort(key=lambda r:r['createdAt']);print(rs[-1]['id'])")

echo "== the pack, as the Build records it"
.venv/bin/python -c "
import json;d=json.load(open('build_spec.json'))['definition']['customNodes'][0]
print(json.dumps({k:d[k] for k in ('name','source','blobId','localSizeBytes','repository','gitRef')}, indent=2))"

echo
echo "== the release manifest ($REL)"
.venv/bin/comfy build release manifest "$REL" --id "$BUILD" > .manifest.json 2>/dev/null || true
.venv/bin/python -c "
import json
try: d=json.load(open('.manifest.json'))
except Exception as e: print('  (manifest unavailable:', e, ')'); raise SystemExit
s=json.dumps(d)
import re
for m in set(re.findall(r'ytmove[^\"]*', s)): print('  ', m)
print('  bytes in manifest:', len(s))"

echo
echo "== what registry.comfy.org has under that name"
curl -s "https://api.comfy.org/nodes?search=ytmove" | .venv/bin/python -c "
import sys,json
try: d=json.load(sys.stdin)
except Exception: print('  (registry unreachable)'); raise SystemExit
hits=[n for n in d.get('nodes',[]) if n.get('id')=='ytmove' or n.get('name')=='ytmove']
print('  exact matches for \'ytmove\':', len(hits))
print('  (the search returned', len(d.get('nodes',[])), 'unrelated fuzzy hits)')"
echo
echo "== the binaries that shipped inside it"
ls -la ytmove_pack/bin/ytframe ytmove_pack/bin/vhstape ytmove_pack/bin/hideit 2>/dev/null | awk '{print "  ", $NF, $5, "bytes"}'
echo "  none of them is on GitHub, PyPI or the registry: the pack reached the Build as an uploaded zip."
echo
echo "== the nodes they back"
echo "   YouTubeFrame  a link and a timestamp -> that frame"
echo "   VHSTape       stills -> one VHS tape with an advancing date stamp"
echo "   HideIt        a scene and a thing -> the thing hidden, and the box it went into"
