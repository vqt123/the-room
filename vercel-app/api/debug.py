import json, os, sys, time
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
# Appended, not prepended: api/queue.py would otherwise shadow the standard
# library's queue module for anything these handlers import.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _core import queue, check_password, client
import httpx


class handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        if not check_password((q.get("password") or [""])[0]):
            return self._json({"error": "wrong password"}, 401)
        out = {"meta": None, "endpoint": {}, "queue": None, "job": None, "vercel": {"region": os.environ.get("VERCEL_REGION"), "deployment": os.environ.get("VERCEL_DEPLOYMENT_ID", "")[:12], "url": os.environ.get("VERCEL_URL")}}
        try:
            out["meta"] = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_meta.json")))
        except Exception as e:  # noqa: BLE001
            out["meta"] = {"error": str(e)[:100]}
        base = os.environ.get("COMFY_BASE_URL", "").strip(); key = os.environ.get("COMFY_API_KEY", "").strip()
        out["endpoint"]["url"] = base; out["endpoint"]["key"] = (key[:15] + "...") if key else "MISSING"
        try:
            t0 = time.time(); r = httpx.get(base.rstrip("/") + "/api/v2/jobs?limit=1", headers={"Authorization": "Bearer " + key}, timeout=15)
            out["endpoint"]["health"] = {"status": r.status_code, "ms": int((time.time() - t0) * 1000)}
        except Exception as e:  # noqa: BLE001
            out["endpoint"]["health"] = {"error": str(e)[:120]}
        try:
            out["queue"] = queue((q.get("id") or [""])[0] or None)
        except Exception as e:  # noqa: BLE001
            out["queue"] = {"error": str(e)[:120]}
        jid = (q.get("id") or [""])[0]
        if jid:
            try:
                m = client().jobs.get(jid)._model
                d = m.model_dump() if hasattr(m, "model_dump") else dict(m.__dict__)
                d.pop("outputs", None); d.pop("urls", None)
                out["job"] = json.loads(json.dumps(d, default=str))
            except Exception as e:  # noqa: BLE001
                out["job"] = {"error": str(e)[:200]}
        self._json(out)
