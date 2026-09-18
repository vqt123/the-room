"""Run the Vercel app on this machine: static pages from public/, and every api/<name>.py
handler at /api/<name>. Same code, same env vars (pull them with `vercel env pull`), so the
same Redis, Blob store and endpoint. Meant to sit behind a tunnel (cloudflared / ngrok) when
Vercel itself is the thing that is down.

    python local_server.py --env prod.env --port 8787
"""
import argparse, importlib, os, sys
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(HERE, "public")
API = os.path.join(HERE, "api")
sys.path.append(API)   # appended, not prepended: api/queue.py would shadow the stdlib's queue


def load_env(path):
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        os.environ.setdefault(k.strip(), v)


class Dispatch(SimpleHTTPRequestHandler):
    """/api/<name> becomes api/<name>.py's `handler`; everything else is a file in public/."""

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=PUBLIC, **kw)

    def _api(self):
        name = urlparse(self.path).path[len("/api/"):].split("/")[0]
        if not name.replace("_", "").isalnum() or name.startswith("_"):
            return None
        try:
            return importlib.import_module(name).handler
        except ModuleNotFoundError:
            return None

    def _route(self, verb):
        if urlparse(self.path).path.startswith("/api/"):
            cls = self._api()
            if cls is None or not hasattr(cls, verb):
                self.send_error(404)
                return
            # Become the function's handler for this one request; it reads self.path,
            # self.headers and self.rfile and writes with self.wfile like on Vercel. A
            # keep-alive connection reuses this instance for its next request, so switch back.
            own = self.__class__
            self.__class__ = cls
            try:
                getattr(self, verb)()
            finally:
                self.__class__ = own
            return
        if verb != "do_GET":
            self.send_error(405)
            return
        SimpleHTTPRequestHandler.do_GET(self)

    def do_GET(self):
        self._route("do_GET")

    def do_POST(self):
        self._route("do_POST")

    def end_headers(self):
        # The pages are edited live; never let a phone cache one.
        if self.path.endswith(".html") or self.path in ("/", ""):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=None)
    ap.add_argument("--port", type=int, default=8787)
    a = ap.parse_args()
    if a.env:
        load_env(a.env)
    BaseHTTPRequestHandler.protocol_version = "HTTP/1.1"
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Dispatch)
    print(f"serving {PUBLIC} and api/ on http://127.0.0.1:{a.port}", flush=True)
    srv.serve_forever()
