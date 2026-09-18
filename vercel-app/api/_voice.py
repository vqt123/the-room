"""Yoland's voice, through Comfy's Router.

api.comfy.org proxies ElevenLabs the same way it proxies Anthropic, so the game can turn each
panel's first-person line into speech without a node, a rebuild or a second vendor key: the
production Comfy key pays for it. Two routes are used:

    POST /proxy/elevenlabs/v1/voices/add            clone a voice from reference audio  -> voice_id
    POST /proxy/elevenlabs/v1/text-to-speech/<id>   one line of speech                  -> mp3

Until someone clones Yoland, a stock male voice reads the lines, so the demo is never mute.
"""
import json, mimetypes, os, secrets, sys, urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _llm import ROUTER, partner_key

BASE = ROUTER.rsplit("/proxy/", 1)[0] + "/proxy/elevenlabs/v1"
STOCK_VOICE = "nPczCjzI2devNBz1zQrb"      # Brian, male american: the placeholder until Yoland is cloned
TTS_MODEL = "eleven_multilingual_v2"
MAX_CHARS = 380


def _auth():
    key = partner_key()
    if not key:
        raise RuntimeError("server is missing COMFY_PARTNER_API_KEY (the Router needs a production key)")
    return {"X-API-KEY": key, "Authorization": f"Bearer {key}"}


def say(text, voice=STOCK_VOICE, timeout=60):
    """One line of speech. Returns mp3 bytes."""
    text = " ".join(str(text or "").split())[:MAX_CHARS]
    if not text:
        return None
    body = json.dumps({"text": text, "model_id": TTS_MODEL,
                       "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "style": 0.35}}).encode()
    req = urllib.request.Request(f"{BASE}/text-to-speech/{voice}", data=body, method="POST",
                                 headers=dict(_auth(), **{"Content-Type": "application/json"}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"elevenlabs {e.code}: {e.read().decode(errors='replace')[:200]}")


def say_all(lines, voice=STOCK_VOICE):
    """Every panel's line at once. The four calls run together, so a round costs one call's wait.
    A line that fails comes back as None rather than losing the whole round."""
    def one(t):
        try:
            return say(t, voice)
        except Exception:  # noqa: BLE001
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(one, lines))


def _multipart(fields, files):
    """A multipart body by hand: the Router wants a real form and the stdlib has no builder.
    `files` is a list of (field, filename, bytes); the same field name may repeat, which is how
    several voice samples are sent."""
    b = "----comfy" + secrets.token_hex(12)
    out = bytearray()
    for k, v in fields.items():
        out += f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    for name, fname, data in files:
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        out += (f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{fname}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode()
        out += data + b"\r\n"
    out += f"--{b}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={b}"


def clone(name, samples, description="Hackathon hero voice", timeout=180):
    """Clone a voice from reference audio. `samples` is [(filename, bytes), ...]; one clean
    minute is plenty. Returns the new voice id."""
    body, ctype = _multipart({"name": name, "description": description},
                             [("files", fn, data) for fn, data in samples])
    req = urllib.request.Request(f"{BASE}/voices/add", data=body, method="POST",
                                 headers=dict(_auth(), **{"Content-Type": ctype}))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"elevenlabs {e.code}: {e.read().decode(errors='replace')[:300]}")
    vid = got.get("voice_id")
    if not vid:
        raise RuntimeError(f"no voice_id came back: {json.dumps(got)[:200]}")
    return vid
