"""Upstash QStash: the thing that calls us back in 30 seconds.

Vercel has no timer that fires twice a minute. Its cron is one-minute granularity at best,
and nothing on the platform stays running between requests. QStash is a queue that takes an
HTTP request now and delivers it later, so the room's clock is a chain: every tick schedules
the next one before it does any work, and a flag in Redis is what stops the chain.

Free plan: 500 messages a day, which at one every 30 s is about four hours of room time.
"""
import json, os, urllib.error, urllib.request

API = "https://qstash.upstash.io/v2"


def _token():
    tok = os.environ.get("QSTASH_TOKEN", "").strip()
    if not tok:
        raise RuntimeError("server is missing QSTASH_TOKEN")
    return tok


def publish(url, body, delay_seconds, retries=1):
    """Ask QStash to POST `body` to `url` after a delay. Returns the message id."""
    # The destination goes on the path as-is: percent-encoding it makes QStash read the
    # scheme as part of the host and reject it ("endpoint has invalid scheme").
    req = urllib.request.Request(f"{API}/publish/{url}", method="POST",
                                 headers={"Authorization": "Bearer " + _token(),
                                          "Content-Type": "application/json",
                                          "Upstash-Delay": f"{int(delay_seconds)}s",
                                          "Upstash-Retries": str(retries)},
                                 data=json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=15) as r:
        got = json.loads(r.read() or b"{}")
    return got.get("messageId") if isinstance(got, dict) else None


def cancel(message_id):
    if not message_id:
        return False
    req = urllib.request.Request(f"{API}/messages/{message_id}", method="DELETE",
                                 headers={"Authorization": "Bearer " + _token()})
    try:
        with urllib.request.urlopen(req, timeout=15):
            return True
    except urllib.error.HTTPError:
        return False   # already delivered, which is fine: the flag stops the chain
