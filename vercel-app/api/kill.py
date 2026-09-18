"""Kill or Save's one endpoint. GET reads the shared state, POST changes it. No password:
the room is whoever has the link (Vinh, 2026-09-18: "remove the password requirement")."""
import base64, json, os, sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# Appended, not prepended: api/queue.py would otherwise shadow the standard
# library's queue module for anything these handlers import.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _kill import advance, config, join, publish, reset, say, set_hero, start, state

SESS_OK = set("abcdefghijklmnopqrstuvwxyz0123456789-")


def _sess(raw):
    s = (raw or "main").strip().lower()[:24]
    s = "".join(c for c in s if c in SESS_OK)
    return s or "main"


class handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        try:
            self._json(state(_sess((q.get("sess") or [""])[0]), (q.get("voter") or [None])[0]))
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)[:400]}, 500)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json({"error": "bad body"}, 400)
        sess = _sess(body.get("sess"))
        action = body.get("action")
        try:
            if action == "join":
                return self._json(join(sess, body.get("voter"), body.get("name")))
            if action == "say":
                return self._json(say(sess, body.get("voter"), body.get("noun"), body.get("verb")))
            if action == "config":
                return self._json(config(sess, body.get("rounds"), body.get("render"), body.get("verdict")))
            if action == "start":
                # Only the Go button on the screen page closes a round; nothing runs on its own.
                if not body.get("submit"):
                    return self._json({"skipped": "only the Go button on the screen page starts a round"})
                return self._json(start(sess))
            if action == "publish":
                return self._json(publish(sess, body.get("round")))
            if action == "advance":
                return self._json(advance(sess))
            if action == "hero":
                raw = base64.b64decode(body.get("image") or "") if body.get("image") else None
                return self._json(set_hero(sess, raw, clear=bool(body.get("clear"))))
            if action == "reset":
                return self._json(reset(sess))
            self._json({"error": "unknown action"}, 400)
        except ValueError as e:
            self._json({"error": str(e)[:200]}, 400)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)[:400]}, 500)
