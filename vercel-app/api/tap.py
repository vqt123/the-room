"""Score a tap. The answer is read from the endpoint here and never sent out."""
import json
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import os, sys
# Appended, not prepended: api/queue.py would otherwise shadow the standard
# library's queue module for anything these handlers import.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _findcore import check, reveal, check_password


class handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if not check_password((q.get("password") or [""])[0]):
            return self._json({"error": "wrong password"}, 401)
        job_id = (q.get("id") or [""])[0]
        if not job_id:
            return self._json({"error": "no game id"}, 400)
        try:
            if (q.get("reveal") or [""])[0] in ("1", "true", "yes"):
                return self._json(reveal(job_id))
            x = (q.get("x") or [""])[0]; y = (q.get("y") or [""])[0]
            if x == "" or y == "":
                return self._json({"error": "no tap"}, 400)
            self._json(check(job_id, float(x), float(y)))
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)[:400]}, 500)
