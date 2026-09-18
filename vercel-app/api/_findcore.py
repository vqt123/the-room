"""Find It: a product hidden in a busy picture, and a click to find it.

The scene and the product are drawn by Qwen-Image-Edit on a Comfy Dev Platform
endpoint. The private HideIt node inside that endpoint does the hiding and
returns the exact box, which rides out as the saved file's NAME. This file reads
that name and keeps it. The browser is sent the picture and nothing else, so a
click can only be scored here.
"""
import json, os, random, re, sys

# Appended, not prepended: api/queue.py would otherwise shadow the standard
# library's queue module for anything these handlers import.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from comfy_sdk import Comfy
from _findgraph import (TYPED, UPLOAD, SCENES, SCENE_NAMES, build, build_mash, build_vn,
                        scene_index, product_prompt, refine_prompt)
from _core import check_password  # noqa: F401

KEY_RE = re.compile(r"hideit-x(\d+)-y(\d+)-w(\d+)-h(\d+)")
# A tap is forgiving by this much of the picture, so a finger on a phone counts.
TAP_PAD = 18  # per mille


def client():
    base = os.environ.get("COMFY_BASE_URL", "").strip()
    key = os.environ.get("COMFY_API_KEY", "").strip()
    if not base or not key:
        raise RuntimeError("server is missing COMFY_BASE_URL or COMFY_API_KEY")
    return Comfy(api_key=key, timeout=50.0, client_info="find-it-vercel")


def submit(thing=None, photo=None, photo_name=None, difficulty=3, scene=None, seed=0):
    """A typed product draws its own picture; an uploaded photo is used as it is."""
    seed = int(seed) or random.randrange(1, 2**31)
    rng = random.Random(seed)
    idx = next((i for i, s in enumerate(SCENES) if s["name"] == scene), rng.randrange(len(SCENES)))
    c = client()
    upload = photo is not None and len(photo) > 0
    g = json.loads(json.dumps(UPLOAD if upload else TYPED))
    wf = c.workflows.from_json(g)
    wf.set_input("10", "prompt", SCENES[idx]["prompt"])
    wf.set_input("15", "seed", seed % 0xFFFFFFFF)
    if upload:
        wf.set_input("2", "image", c.assets.from_bytes(photo, filename=photo_name or "product.png"))
    else:
        wf.set_input("20", "prompt", product_prompt(thing))
        wf.set_input("25", "seed", (seed + 4242) % 0xFFFFFFFF)
    # The redraw pass has to be told what the thing is, or it redraws something else.
    wf.set_input("47", "prompt", refine_prompt(thing if not upload else "object"))
    wf.set_input("53", "seed", (seed + 777) % 0xFFFFFFFF)
    wf.set_input("30", "difficulty", max(1, min(5, int(difficulty))))
    wf.set_input("30", "seed", (seed + 99) % 0xFFFFFFFF)
    job = c.submit(wf)
    return {"id": job.id, "scene": SCENES[idx]["name"], "seed": seed,
            "thing": (thing or "").strip() if not upload else "your photo"}


def submit_many(things, scene=None, difficulty=3, seed=0):
    """One job that hides every thing in `things` in a single picture.

    Each thing gets its own HideIt node, and each of those writes its own answer onto its
    own thumbnail's filename. So N answers ride out of one job the same way one did.
    Returns the job id plus, per thing, the output node its answer will arrive on.
    """
    seed = int(seed) or random.randrange(1, 2 ** 31)
    rng = random.Random(seed)
    idx = scene_index(scene, rng)
    graph, thumbs = build(things, scene=idx, difficulty=difficulty, seed=seed)
    c = client()
    job = c.submit(c.workflows.from_json(graph))
    return {"id": job.id, "scene": SCENE_NAMES[idx], "seed": seed,
            "things": [{"text": t, "node": n} for t, n in zip(things, thumbs)]}


def submit_mash(words, seed=0):
    """The mash-up round: every word the room typed in one window, in one picture, no hiding.
    A blank canvas plus one sampler pass, so it costs about 11 s whatever the word count."""
    seed = int(seed) or random.randrange(1, 2 ** 31)
    graph, prompt, base = build_mash(words, seed=seed)
    c = client()
    job = c.submit(c.workflows.from_json(graph))
    return {"id": job.id, "seed": seed, "prompt": prompt, "scene": " ".join(base)}


def submit_vn(verb, noun, seed=0):
    """The verb-and-noun round: whatever the room voted for, as one picture."""
    seed = int(seed) or random.randrange(1, 2 ** 31)
    graph, prompt, subject, base = build_vn(verb, noun, seed=seed)
    c = client()
    job = c.submit(c.workflows.from_json(graph))
    return {"id": job.id, "seed": seed, "prompt": prompt, "subject": subject,
            "scene": " ".join(base)}


TERMINAL_OK = ("succeeded", "completed", "success")
TERMINAL_BAD = ("failed", "cancelled", "canceled", "error", "expired")


def _output(job, node="31"):
    outs = job.get_outputs(node)
    return outs[0] if outs else None


_ANSWERS = {}   # job id -> box, for the life of one warm function instance


def answer(job_id):
    """The box, read off the output's name. Never returned to the browser."""
    hit = _ANSWERS.get(job_id)
    if hit is not None:
        return hit
    c = client()
    job = c.jobs.get(job_id)
    if job.status not in TERMINAL_OK:
        return None
    out = _output(job)
    if out is None:
        return None
    m = KEY_RE.search(out.name or "")
    if not m:
        return None
    x, y, w, h = (int(v) for v in m.groups())
    box = {"x": x, "y": y, "w": w, "h": h}
    # A tap costs a round trip to the endpoint otherwise, and the answer for a
    # finished job never changes.
    if len(_ANSWERS) > 200:
        _ANSWERS.clear()
    _ANSWERS[job_id] = box
    return box


def answers_many(job_id, nodes):
    """Read one box per thing off the thumbnails' names, with the thumbnail URL beside it."""
    c = client()
    job = c.jobs.get(job_id)
    if job.status not in TERMINAL_OK:
        return None
    out = []
    for node in nodes:
        got = job.get_outputs(node)
        if not got:
            out.append(None)
            continue
        m = KEY_RE.search(got[0].name or "")
        if not m:
            out.append(None)
            continue
        x, y, w, h = (int(v) for v in m.groups())
        out.append({"box": {"x": x, "y": y, "w": w, "h": h},
                    "thumb_url": str(got[0].get_download_url().url)})
    return out


def puzzle_url(job_id):
    """Just the picture, for a round whose answers are read separately."""
    c = client()
    job = c.jobs.get(job_id)
    out = _output(job, "31")
    return str(out.get_download_url().url) if out else None


def poll(job_id):
    c = client()
    job = c.jobs.get(job_id)
    st = job.status
    if st in TERMINAL_OK:
        out = _output(job)
        if out is None:
            return {"state": "error", "error": "the job finished without a picture"}
        link = out.get_download_url()
        result = {"state": "done", "image_url": str(link.url), "size_kb": out.size_bytes // 1024}
        thing = _output(job, "32")   # what to look for; never where it is
        if thing is not None:
            result["thing_url"] = str(thing.get_download_url().url)
        return result
    if st in TERMINAL_BAD:
        err = job.error
        msg = err.get("message") if isinstance(err, dict) else (str(err) if err else None)
        return {"state": "error", "error": msg or st}
    import datetime as dt
    m = job._model
    qp = getattr(m, "queue_position", None)
    created = getattr(m, "created_at", None)
    age = None
    if created:
        try:
            age = (dt.datetime.now(dt.timezone.utc) - created).total_seconds()
        except Exception:
            age = None
    if qp:
        human = f"waiting in the queue, position {qp}"
    elif age is not None and age > 90:
        human = (f"running for {int(age)} s: the worker is loading about 20 GB of models, which only happens on the "
                 "first job after it wakes up. It is not stuck.")
    elif age is not None and age > 12:
        human = "hiding it"
    else:
        human = "drawing the scene"
    return {"state": "running", "status": human, "raw": st, "queue_position": qp}


def check(job_id, x, y):
    """Score a tap in per-mille picture coordinates."""
    box = answer(job_id)
    if box is None:
        return {"error": "that game is not ready"}
    x, y = float(x), float(y)
    inside = (box["x"] - TAP_PAD <= x <= box["x"] + box["w"] + TAP_PAD
              and box["y"] - TAP_PAD <= y <= box["y"] + box["h"] + TAP_PAD)
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    dist = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5
    out = {"hit": inside, "distance": round(dist)}
    if inside:
        out["box"] = box
    else:
        out["hint"] = "boiling" if dist < 80 else "warm" if dist < 200 else "cold" if dist < 400 else "freezing"
    return out


def reveal(job_id):
    box = answer(job_id)
    return {"box": box} if box else {"error": "that game is not ready"}
