"""The comic page drawn by fal, through Comfy's Router.

Deep Mehta pointed at the Router's model catalogue on 2026-09-18
(docs.comfy.org/development/comfy-router/models). It carries the same Nano Banana we already
use, served by fal instead of Vertex:

    POST /v2/models/fal/fal-nano-banana-pro     one synchronous call -> the finished image URL

Measured the same day, 16:9 at 1K, with the hero photo as a reference: 18.4 s and 18.8 s,
against roughly 25-40 s for the same picture as a Comfy job (submit, queue, poll, download).
It is the same model, so the look and the prompts do not change; only the provider and the
round trip do. A second provider also means a refusal from one is not a refusal from both.
"""
import json, os, sys, urllib.request

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from _llm import partner_key

BASE = "https://api.comfy.org/v2/models"
PRO = "fal/fal-nano-banana-pro"
FLASH = "fal/fal-nano-banana-2"


def draw(prompt, refs=(), model=PRO, aspect="16:9", resolution="1K", timeout=240):
    """One page. `refs` are public image URLs (the hero photo, then the previous page).
    Returns the URL of the finished picture."""
    key = partner_key()
    if not key:
        raise RuntimeError("server is missing COMFY_PARTNER_API_KEY (the Router needs a production key)")
    body = {"prompt": prompt, "aspect_ratio": aspect, "resolution": resolution,
            "num_images": 1, "output_format": "png"}
    refs = [r for r in refs if r]
    if refs:
        body["image_urls"] = refs
    req = urllib.request.Request(f"{BASE}/{model}", data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "X-API-KEY": key,
                                          "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"router {e.code}: {e.read().decode(errors='replace')[:300]}")
    images = got.get("images") or []
    url = (images[0] or {}).get("url") if images else None
    if not url:
        # A refusal comes back as a description with no picture; carry the reason so the retry
        # ladder can log why rather than "no image".
        raise RuntimeError("fal returned no image: " + (str(got.get("description"))[:200] or json.dumps(got)[:200]))
    return url
