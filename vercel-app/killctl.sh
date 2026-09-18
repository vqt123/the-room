#!/bin/sh
# Kill or Save from the shell. Usage:
#   ./killctl.sh state [sess]              the shared state
#   ./killctl.sh reset [sess]              wipe the game (players, words, panels); the hero photo stays
#   ./killctl.sh hero photo.png [sess]     the main character's photo; "hero clear" drops it
#   ./killctl.sh join NAME VOTER [sess]    join as a player (VOTER = 6-12 lowercase letters/digits)
#   ./killctl.sh word "banana" VOTER [sess]
#   ./killctl.sh go [sess]                 press Go (close the pot into one four-panel comic)
#   ./killctl.sh advance [sess]            publish a finished comic
set -e
cd "$(dirname "$0")"
APP="${APP:-https://steal-the-moves-tau.vercel.app}"
PW=""   # Kill or Save takes no password
PY=../build/.venv/bin/python
post() { curl -s -X POST "$APP/api/kill" -H 'Content-Type: application/json' -d "$1"; echo; }
case "$1" in
  state) curl -s "$APP/api/kill?sess=${2:-main}&password=$PW" | $PY -c "
import sys,json;d=json.load(sys.stdin)
if d.get('error'): print('error:',d['error']); raise SystemExit(1)
print('round   :',d['round'],'| players:',d['players'],'| hero:',bool(d['hero']))
print('pot     :',[f['word'] for f in d['feed']])
print('drawing :',d['drawing'] and ('comic %s, %ss in' % (d['drawing']['round'], (d['now']-d['drawing']['ms'])//1000)))
for c in d['comics']:
    print('  comic',c['round'],c['state'],'|',c.get('error') or '')
    for i,cap in enumerate(c.get('captions') or []): print('     ',i+1,cap)" ;;
  reset) post "{\"action\":\"reset\",\"sess\":\"${2:-main}\",\"password\":\"$PW\"}" ;;
  hero)  if [ "$2" = "clear" ]; then post "{\"action\":\"hero\",\"clear\":true,\"sess\":\"${3:-main}\",\"password\":\"$PW\"}"
         else $PY -c "import json,base64,sys;print(json.dumps({'action':'hero','sess':sys.argv[2],'password':sys.argv[3],'image':base64.b64encode(open(sys.argv[1],'rb').read()).decode()}))" "$2" "${3:-main}" "$PW" \
              | curl -s -X POST "$APP/api/kill" -H 'Content-Type: application/json' --data-binary @-; echo; fi ;;
  join)  post "{\"action\":\"join\",\"name\":\"$2\",\"voter\":\"$3\",\"sess\":\"${4:-main}\",\"password\":\"$PW\"}" ;;
  word)  post "{\"action\":\"word\",\"text\":\"$2\",\"voter\":\"$3\",\"sess\":\"${4:-main}\",\"password\":\"$PW\"}" ;;
  go)    post "{\"action\":\"start\",\"submit\":true,\"sess\":\"${2:-main}\",\"password\":\"$PW\"}" ;;
  advance) post "{\"action\":\"advance\",\"sess\":\"${2:-main}\",\"password\":\"$PW\"}" ;;
  *) sed -n '2,10p' "$0" ;;
esac
