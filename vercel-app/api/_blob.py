"""Vercel Blob, used for the one thing that is too big for Redis: the pictures.

Each finished round's picture is copied out of the Comfy endpoint and put here, so the
room loads it from a CDN by a stable URL instead of every phone pulling a signed URL off
the GPU. The room's own state lives in Redis (see _kv.py).
"""
import json, os, urllib.error, urllib.parse, urllib.request

API = "https://blob.vercel-storage.com"


def _token():
    tok = os.environ.get("BLOB_READ_WRITE_TOKEN", "").strip()
    if not tok:
        raise RuntimeError("server is missing BLOB_READ_WRITE_TOKEN")
    return tok


def base_url():
    """The public host the pictures are served from, derived from the token's store id."""
    tok = _token()
    store = tok.split("_")[3] if tok.count("_") >= 3 else ""
    return f"https://{store.lower()}.public.blob.vercel-storage.com"


def _call(method, url, data=None, headers=None, timeout=25):
    h = {"authorization": "Bearer " + _token(), "x-api-version": "7"}
    h.update(headers or {})
    req = urllib.request.Request(url, method=method, headers=h, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw or b"{}")


def put(pathname, data=b"", content_type="application/octet-stream", max_age=31536000):
    return _call("PUT", f"{API}/{pathname}", data=data, headers={
        "x-content-type": content_type,
        "x-add-random-suffix": "0",
        "x-allow-overwrite": "1",
        "x-cache-control-max-age": str(max_age),
    })



def listing(prefix, limit=1000):
    out, cursor = [], None
    while True:
        url = f"{API}?prefix={urllib.parse.quote(prefix)}&limit={limit}"
        if cursor:
            url += "&cursor=" + urllib.parse.quote(cursor)
        got = _call("GET", url)
        out.extend(got.get("blobs") or [])
        cursor = got.get("cursor")
        if not got.get("hasMore") or not cursor or len(out) >= 4000:
            return out



def delete(urls):
    if not urls:
        return
    _call("POST", f"{API}/delete", data=json.dumps({"urls": urls}).encode(),
          headers={"content-type": "application/json"})
