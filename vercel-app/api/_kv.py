"""Upstash Redis over its REST API, with nothing but the standard library.

Vercel functions keep nothing between requests, so the room's shared state lives here.
Redis is worth the dependency for two commands in particular: HSETNX settles who tapped
first with no read-then-write to race in, and SET NX PX gives the screen a real lock.
"""
import json, os, urllib.error, urllib.request


def _conf():
    url = os.environ.get("KV_REST_API_URL", "").strip().rstrip("/")
    tok = os.environ.get("KV_REST_API_TOKEN", "").strip()
    if not url or not tok:
        raise RuntimeError("server is missing KV_REST_API_URL or KV_REST_API_TOKEN")
    return url, tok


def _open(req, timeout):
    """urlopen with one retry on a transport error (this Mac's network drops the odd TLS
    handshake); HTTP error responses are not retried."""
    import socket, ssl, urllib.error
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except (socket.timeout, ssl.SSLError, ConnectionError, urllib.error.URLError) as e:
        if isinstance(e, urllib.error.HTTPError):
            raise
        return urllib.request.urlopen(req, timeout=timeout)


def _post(path, payload, timeout=15):
    url, tok = _conf()
    req = urllib.request.Request(url + path, method="POST",
                                 headers={"Authorization": "Bearer " + tok,
                                          "Content-Type": "application/json"},
                                 data=json.dumps(payload).encode())
    with _open(req, timeout) as r:
        return json.loads(r.read())


def cmd(*parts):
    """One command. Every argument is sent as a string."""
    got = _post("", [str(p) for p in parts])
    if isinstance(got, dict) and got.get("error"):
        raise RuntimeError(got["error"])
    return got.get("result")


def pipe(commands):
    """Several commands in one round trip. Returns their results in order."""
    if not commands:
        return []
    got = _post("/pipeline", [[str(p) for p in c] for c in commands])
    return [g.get("result") for g in got]


def hgetall(key):
    flat = cmd("HGETALL", key) or []
    return {flat[i]: flat[i + 1] for i in range(0, len(flat) - 1, 2)}


def scan(match, count=500):
    """Every key matching a pattern. Used only to wipe a session."""
    cursor, out = "0", []
    while True:
        cur, keys = cmd("SCAN", cursor, "MATCH", match, "COUNT", count)
        out.extend(keys or [])
        cursor = str(cur)
        if cursor == "0" or len(out) > 5000:
            return out
