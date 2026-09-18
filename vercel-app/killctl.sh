#!/bin/sh
# Kill or Save from the shell (no password). Usage:
#   ./killctl.sh state [sess]                  the shared state
#   ./killctl.sh reset [sess]                  wipe the game (players, answers, setup, pages); hero + settings stay
#   ./killctl.sh hero photo.png [sess]         the hero's photo; "hero clear" drops it
#   ./killctl.sh join NAME VOTER [sess]        join as a player (VOTER = 6-12 lowercase letters/digits)
#   ./killctl.sh say "a falling piano" VOTER [sess]   answer this round
#   ./killctl.sh voice sample.mp3 [sess]       clone Yoland's voice from audio; or pass a voice id
#   ./killctl.sh go [sess]                     press Go: smash, then render
#   ./killctl.sh advance [sess]                publish a finished page
#   ./killctl.sh config ROUNDS RENDER VERDICT [sess]   e.g. config 2 '"page"' null  (RENDER: "page"|"pro"|"panels"; VERDICT: "lives"|"dies"|null)
set -e
cd "$(dirname "$0")"
APP="${APP:-http://127.0.0.1:8791}"
PY=../build/.venv/bin/python
post() { curl -s -m 300 -X POST "$APP/api/kill" -H 'Content-Type: application/json' -d "$1"; echo; }
case "$1" in
  state) curl -s "$APP/api/kill?sess=${2:-main}" | $PY -c "
import sys,json;d=json.load(sys.stdin)
if d.get('error'): print('error:',d['error']); raise SystemExit(1)
print('round   :',d['round'],'of',d['total_rounds'],'| next:',d['next_verdict'],'(',d['verdict_override'],') | render:',d['render'],'| players:',d['players'],'| hero:',bool(d['hero']))
print('so far  :',d['so_far'][:110])
print('pot     :',d['ideas'],'|',d['answers'],'answered')
print('drawing :',d['drawing'] and ('page %s, %ss in' % (d['drawing']['round'], (d['now']-d['drawing']['ms'])//1000)))
for c in d['pages']:
    print('  page',c['round'],c['state'],c['verdict'],'|',c.get('title'),'|',c.get('error') or c.get('image') or c.get('images'))" ;;
  reset) post "{\"action\":\"reset\",\"sess\":\"${2:-main}\"}" ;;
  hero)  if [ "$2" = "clear" ]; then post "{\"action\":\"hero\",\"clear\":true,\"sess\":\"${3:-main}\"}"
         else $PY -c "import json,base64,sys;print(json.dumps({'action':'hero','sess':sys.argv[2],'image':base64.b64encode(open(sys.argv[1],'rb').read()).decode()}))" "$2" "${3:-main}" \
              | curl -s -X POST "$APP/api/kill" -H 'Content-Type: application/json' --data-binary @-; echo; fi ;;
  join)  post "{\"action\":\"join\",\"name\":\"$2\",\"voter\":\"$3\",\"sess\":\"${4:-main}\"}" ;;
  say)   post "{\"action\":\"say\",\"idea\":\"$2\",\"voter\":\"$3\",\"sess\":\"${4:-main}\"}" ;;
  voice) if [ -f "$2" ]; then
           $PY -c "import json,base64,sys;print(json.dumps({'action':'voice','sess':sys.argv[2],'name':'Yoland','audio':[{'name':sys.argv[1].split('/')[-1],'data':base64.b64encode(open(sys.argv[1],'rb').read()).decode()}]}))" "$2" "${3:-main}" \
             | curl -s -m 300 -X POST "$APP/api/kill" -H 'Content-Type: application/json' --data-binary @-; echo
         else post "{\"action\":\"voice\",\"voice\":\"$2\",\"sess\":\"${3:-main}\"}"; fi ;;
  go)    post "{\"action\":\"start\",\"submit\":true,\"sess\":\"${2:-main}\"}" ;;
  advance) post "{\"action\":\"advance\",\"sess\":\"${2:-main}\"}" ;;
  config) post "{\"action\":\"config\",\"sess\":\"${5:-main}\",\"rounds\":${2:-null},\"render\":${3:-null},\"verdict\":${4:-null}}" ;;
  *) sed -n '2,14p' "$0" ;;
esac
