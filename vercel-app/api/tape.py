import json
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler
import os, sys
# Appended, not prepended: api/queue.py would otherwise shadow the standard
# library's queue module for anything these handlers import.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _tapecore import submit, check_password


class handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = (b"Content-Type: " + self.headers["Content-Type"].encode()
                   + b"\r\nMIME-Version: 1.0\r\n\r\n" + self.rfile.read(length))
            msg = BytesParser(policy=HTTP).parsebytes(raw)
            fields, files = {}, {}
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                fn = part.get_filename(); payload = part.get_payload(decode=True) or b""
                if fn:
                    files[name] = (fn, payload)
                else:
                    fields[name] = payload.decode("utf-8", "replace")
            if not check_password(fields.get("password")):
                return self._json({"error": "wrong password"}, 401)
            na, da = files.get("photoA", ("personA.png", b""))
            nb, db = files.get("photoB", ("personB.png", b""))
            if not da:
                return self._json({"error": "no photo"}, 400)
            portrait = (fields.get("portrait", "") or "").lower() in ("1", "true", "on", "yes")
            seed = 0
            try:
                seed = int(fields.get("seed", "0") or 0)
            except ValueError:
                seed = 0
            job_id = submit(da, na, db or None, nb, portrait=portrait, seed=seed)
            self._json({"id": job_id, "couple": bool(db)})
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)[:400]}, 500)
