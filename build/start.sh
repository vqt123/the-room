#!/bin/sh
# Start Steal the Moves and open it in the browser. Ctrl-C stops it.
cd "$(dirname "$0")"
kill $(lsof -t -nP -iTCP:8787 -sTCP:LISTEN) 2>/dev/null
exec .venv/bin/python app.py
