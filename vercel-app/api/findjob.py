import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import os, sys
# Appended, not prepended: api/queue.py would otherwise shadow the standard
# library's queue module for anything these handlers import.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _findcore import poll, check_password


class handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if not check_password((q.get("password") or [""])[0]):
            return self._json({"state": "error", "error": "wrong password"}, 401)
        job_id = (q.get("id") or [""])[0]
        if not job_id:
            return self._json({"state": "error", "error": "no game id"}, 400)
        try:
            self._json(poll(job_id))
        except Exception as e:  # noqa: BLE001
            self._json({"state": "error", "error": str(e)[:400]}, 500)
