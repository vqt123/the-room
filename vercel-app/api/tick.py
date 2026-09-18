"""The room's clock.

QStash calls this endpoint; it does the room's work and then books its own
successor, and it books that successor even when the work failed, so a bad round cannot stop
the clock. A flag in Redis is the on/off switch: a tick that finds the flag clear does not
book a successor and the chain ends.

The delay is not a fixed 30 seconds. A drawing round is due when it should be finished and a
picture that is up is due when its window runs out, so the tick comes back at the moment
something actually happens. That is one message per event instead of polling, which matters
on a free plan of 500 messages a day.

The projector page runs the same work from the browser. Both are safe to have at once: the
lock inside `start` means only one of them can open a round.
"""
import json, os, sys
from http.server import BaseHTTPRequestHandler

# Appended, not prepended: api/queue.py would otherwise shadow the standard
# library's queue module for anything these handlers import.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import _kv
import _qstash
from _room import check_password, conduct, state

EVERY_S = 30      # the longest gap between ticks
MIN_S = 2


def flag(sess):
    return f"rm:{sess}:ticking"


def when_next(sess):
    """Come back when something is actually due, rather than every 30 s regardless.

    A round that is drawing is due when it should be finished; a picture that is up is due
    when its window runs out. Both are known, so the chain spends one message per event
    instead of polling, which matters on a free plan of 500 messages a day.
    """
    try:
        st = state(sess)
    except Exception:  # noqa: BLE001
        return EVERY_S
    due = []
    for r in st["drawing"]:
        due.append(r.get("eta_ms", 0) / 1000)
    if st["ready"] and st["current"]:
        due.append(st["show_left"] / 1000)
    if not due:
        return MIN_S
    return max(MIN_S, min(EVERY_S, min(due)))


def book(sess, url, delay=EVERY_S):
    """Book the next tick and remember its id, so it can be called off."""
    mid = _qstash.publish(url, {"sess": sess, "tick": True,
                                "password": os.environ.get("APP_PASSWORD", "")}, delay)
    _kv.cmd("SET", f"rm:{sess}:tickid", mid or "", "EX", 3600)
    return mid


class handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _url(self):
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or ""
        return f"https://{host}/api/tick"

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            return self._json({"error": "bad body"}, 400)
        if not check_password(body.get("password")):
            return self._json({"error": "wrong password"}, 401)
        sess = "".join(c for c in (body.get("sess") or "main").lower()[:24]
                       if c.isalnum() or c == "-") or "main"
        action = body.get("action")
        try:
            if action == "on":
                _kv.cmd("SET", flag(sess), "1", "EX", 21600)
                return self._json({"ticking": True, "every_s": EVERY_S,
                                   "message": book(sess, self._url(), 1)})
            if action == "off":
                mid = _kv.cmd("GET", f"rm:{sess}:tickid")
                _kv.cmd("DEL", flag(sess))
                return self._json({"ticking": False, "cancelled": _qstash.cancel(mid)})
            if action == "status":
                return self._json({"ticking": bool(_kv.cmd("GET", flag(sess))),
                                   "next_message": _kv.cmd("GET", f"rm:{sess}:tickid")})
            # A delivery from QStash: book the successor first, then do the work.
            # The work is allowed to fail; the chain is not. A round that throws must not
            # be able to stop the clock, so the successor is booked either way.
            try:
                did = conduct(sess)
            except Exception as e:  # noqa: BLE001
                did = {"did": [], "error": str(e)[:200]}
            booked, delay = None, None
            if _kv.cmd("GET", flag(sess)):
                delay = when_next(sess)
                booked = book(sess, self._url(), delay)
            self._json({"booked": booked, "next_in_s": delay, **did})
        except Exception as e:  # noqa: BLE001
            self._json({"error": str(e)[:400]}, 500)
