"""The Room's one endpoint. GET reads the shared state, POST changes it."""
import json, os, sys
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# Appended, not prepended: api/queue.py would otherwise shadow the standard
# library's queue module for anything these handlers import.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _room import (add, advance, check_password, conduct, found, publish, reset, set_mode, show,
                   start, state, vote)

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
        if not check_password((q.get("password") or [""])[0]):
            return self._json({"error": "wrong password"}, 401)
        try:
            self._json(state(_sess((q.get("sess") or [""])[0])))
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)[:400]}, 500)

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json({"error": "bad body"}, 400)
        if not check_password(body.get("password")):
            return self._json({"error": "wrong password"}, 401)
        sess = _sess(body.get("sess"))
        action = body.get("action")
        try:
            if action == "add":
                return self._json(add(sess, body.get("text"), body.get("name"), body.get("voter"),
                                     body.get("kind"), body.get("verb"), body.get("noun")))
            if action == "vote":
                return self._json(vote(sess, body.get("item"), body.get("voter")))
            if action == "start":
                return self._json(start(sess, force=bool(body.get("force"))))
            if action == "show":
                return self._json(show(sess, body.get("round")))
            if action == "publish":
                return self._json(publish(sess, body.get("round")))
            if action == "found":
                return self._json(found(sess, body.get("round"), body.get("x"), body.get("y"),
                                        body.get("voter"), body.get("name")))
            if action == "mode":
                return self._json(set_mode(sess, body.get("mode")))
            if action == "advance":
                return self._json(advance(sess))
            if action == "conduct":
                return self._json(conduct(sess))
            if action == "reset":
                return self._json(reset(sess))
            self._json({"error": "unknown action"}, 400)
        except ValueError as e:
            self._json({"error": str(e)[:200]}, 400)
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)[:400]}, 500)
