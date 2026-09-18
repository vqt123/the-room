#!/bin/sh
# Run the room from the shell. Usage:
#   ./roomctl.sh state [sess]      what the room agrees on right now
#   ./roomctl.sh reset [sess]      wipe a session (queue, votes, pictures, winners)
#   ./roomctl.sh add  "a taco" [sess]
#   ./roomctl.sh tick on|off|status [sess]   the server-side clock (QStash)
#   ./roomctl.sh mode find|mash|vn|vnfind [sess]
#        find   - things you type get hidden in one picture
#        mash   - every word typed this window goes into one picture
#        vn     - one vote each on verbs and nouns; the winning pair gets drawn
#        vnfind - the winning pair gets drawn AND hidden, and the room has to tap it
#        story  - like pairs, but an LLM node inside the job writes a 4-panel comic from the lines; one picture per panel
#   ./roomctl.sh say verb|noun "dancing" [sess]  submit into a pool (vn mode)
#   ./roomctl.sh hero photo.png [sess]   the comic's main character (story mode); "hero clear" drops it
set -e
cd "$(dirname "$0")"
APP="${APP:-https://steal-the-moves-tau.vercel.app}"
PW=$(cat ../build/vercel_password.txt)
PY=../build/.venv/bin/python
case "$1" in
  state) curl -s "$APP/api/room?sess=${2:-main}&password=$PW" | $PY -c "
import sys,json;d=json.load(sys.stdin)
if d.get('error'): print('error:',d['error']); raise SystemExit(1)
c=d['current']
print('onscreen:', (str(c['found'])+'/'+str(c['count'])+' found, '+str(d['show_left']//1000)+'s left') if c else 'nothing')
if c:
    for t in c['things']: print('   ',t['text'],'<-',t['by'],'->',t['found_by'] or 'open')
print('drawing :',[[t['text'] for t in r['things']] for r in d['drawing']])
print('ready   :',[[t['text'] for t in r['things']] for r in d['ready']])
if d['mode']=='vn':
    print('verbs   :',[(x['text'],x['votes'],x['by']) for x in d['verbs']])
    print('nouns   :',[(x['text'],x['votes'],x['by']) for x in d['nouns']])
else:
    print('queue   :',[(x['text'],x['votes'],x['by']) for x in d['queue']])
print('mode    :',d['mode'],'| preload ready:',bool(d['preload']))" ;;
  reset) curl -s -X POST "$APP/api/room" -H 'Content-Type: application/json' \
           -d "{\"action\":\"reset\",\"sess\":\"${2:-main}\",\"password\":\"$PW\"}" ; echo ;;
  add)   curl -s -X POST "$APP/api/room" -H 'Content-Type: application/json' \
           -d "{\"action\":\"add\",\"sess\":\"${3:-main}\",\"password\":\"$PW\",\"text\":\"$2\",\"name\":\"the shell\",\"voter\":\"shellaaaa1\"}" ; echo ;;
  hero)  if [ "$2" = "clear" ]; then
           curl -s -X POST "$APP/api/room" -H 'Content-Type: application/json' \
             -d "{\"action\":\"hero\",\"clear\":true,\"sess\":\"${3:-main}\",\"password\":\"$PW\"}"
         else
           $PY -c "import json,base64,sys;print(json.dumps({'action':'hero','sess':sys.argv[2],'password':sys.argv[3],'image':base64.b64encode(open(sys.argv[1],'rb').read()).decode()}))" "$2" "${3:-main}" "$PW" \
             | curl -s -X POST "$APP/api/room" -H 'Content-Type: application/json' --data-binary @-
         fi; echo ;;
  mode)  curl -s -X POST "$APP/api/room" -H 'Content-Type: application/json' \
           -d "{\"action\":\"mode\",\"mode\":\"${2:-find}\",\"sess\":\"${3:-main}\",\"password\":\"$PW\"}" ; echo ;;
  say)   curl -s -X POST "$APP/api/room" -H 'Content-Type: application/json' \
           -d "{\"action\":\"add\",\"kind\":\"$2\",\"text\":\"$3\",\"sess\":\"${4:-main}\",\"password\":\"$PW\",\"name\":\"the shell\",\"voter\":\"shellaaaa1\"}" ; echo ;;
  tick)  curl -s -X POST "$APP/api/tick" -H 'Content-Type: application/json' \
           -d "{\"action\":\"${2:-status}\",\"sess\":\"${3:-main}\",\"password\":\"$PW\"}" ; echo ;;
  *) echo "usage: $0 state|reset|add|say|tick|mode [args]"; exit 1 ;;
esac
