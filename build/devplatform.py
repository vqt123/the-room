"""Call the Developer Platform builder/deploy REST API with one fresh token per call.

The CLI mints a short-lived access token and rotates the refresh token on every
invocation, so two CLI calls close together (a watch loop plus anything else)
race each other and start returning 401. This helper takes a token immediately
before each request instead.

  .venv/bin/python devplatform.py get  /deploy/v1/deployments?buildId=ID
  .venv/bin/python devplatform.py wait <deployment-id> [timeout_s]
  .venv/bin/python devplatform.py post /deploy/v1/deployments '{"json": "body"}'
  .venv/bin/python devplatform.py up <release-id> [gpu] [region] [min] [max]
  .venv/bin/python devplatform.py rm <deployment-id>
"""
import json
import sys
import time
import urllib.error
import urllib.request

from comfy_cli import credentials

BASE = "https://stagingplatformapi.comfy.org"


def token():
    s = credentials.get_session(refresh=True)
    if not s or not s.access_token:
        raise SystemExit("not signed in: run `comfy cloud login`")
    return s.access_token


def call(method, path, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token())
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:400]}
    except Exception as e:  # DNS blips and resets must not end a watch
        return 0, {"error": f"{type(e).__name__}: {str(e)[:200]}"}


def deployment(dep_id):
    st, body = call("GET", f"/deploy/v1/deployments/{dep_id}")
    d = body.get("deployment", body) if isinstance(body, dict) else {}
    return st, d


def create(release_id, gpu="rtx-pro-6000-server", region="US-NE-1", mn=1, mx=1):
    body = {"releaseId": release_id,
            "computeConfig": {"gpuClass": gpu, "region": region, "min": int(mn), "max": int(mx)}}
    return call("POST", "/deploy/v1/deployments", body)


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    if cmd == "get":
        st, body = call("GET", sys.argv[2])
        print(st, json.dumps(body)[:3000])
    elif cmd == "post":
        st, body = call("POST", sys.argv[2], json.loads(sys.argv[3]))
        print(st, json.dumps(body)[:3000])
    elif cmd == "rm":
        st, body = call("DELETE", "/deploy/v1/deployments/" + sys.argv[2])
        print(st, json.dumps(body)[:300])
    elif cmd == "up":
        st, body = create(sys.argv[2], *sys.argv[3:])
        print(st, json.dumps(body)[:800])
    elif cmd == "wait":
        dep = sys.argv[2]
        limit = float(sys.argv[3]) if len(sys.argv) > 3 else 900
        t0 = time.time()
        last = None
        while time.time() - t0 < limit:
            st, d = deployment(dep)
            if st != 200:
                print(f"{time.time()-t0:5.0f}s poll {st} {str(d)[:80]}", flush=True)
                time.sleep(15)
                continue
            state = (d.get("status"), d.get("endpointUrl"))
            if state != last:
                print(f"{time.time()-t0:5.0f}s {st} {state[0]} {state[1] or ''}", flush=True)
                last = state
            if d.get("status") in ("ready", "running", "active"):
                print("READY", d.get("endpointUrl"))
                return
            if d.get("status") in ("failed", "error", "deleted"):
                print("BAD", json.dumps(d)[:600])
                raise SystemExit(1)
            time.sleep(15)
        raise SystemExit("timeout waiting for " + dep)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
