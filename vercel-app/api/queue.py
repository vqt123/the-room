import json, os, sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
# Appended, not prepended: api/queue.py would otherwise shadow the standard
# library's queue module for anything these handlers import.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _core import queue, check_password


class handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if not check_password((q.get("password") or [""])[0]):
            return self._json({"error": "wrong password"}, 401)
        try:
            self._json(queue((q.get("id") or [""])[0] or None))
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)[:300]}, 500)
