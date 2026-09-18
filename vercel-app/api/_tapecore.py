"""Senior Year '88: one selfie in, one VHS tape out.

Five scenes are generated from the same photo by Qwen-Image-Edit-2511 on a Comfy
Dev Platform serverless endpoint, then the private VHSTape node inside that
endpoint turns them into a tape. Nothing is generated here; this file only
submits the graph and reports on the job.
"""
import json, os, sys

# Appended, not prepended: api/queue.py would otherwise shadow the standard
# library's queue module for anything these handlers import.
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from comfy_sdk import Comfy
from _tapegraph import SOLO, COUPLE, SCENE_TITLES
from _core import check_password  # noqa: F401

SECONDS_PER_SCENE = 2.6


def client():
    base = os.environ.get("COMFY_BASE_URL", "").strip()
    key = os.environ.get("COMFY_API_KEY", "").strip()
    if not base or not key:
        raise RuntimeError("server is missing COMFY_BASE_URL or COMFY_API_KEY")
    return Comfy(api_key=key, timeout=50.0, client_info="senior-year-88-vercel")


def submit(photo_a, name_a, photo_b=None, name_b=None, portrait=False, seed=0):
    """photo_a/photo_b are bytes. A second photo switches to the couple tape."""
    if not photo_a:
        raise ValueError("no photo")
    couple = bool(photo_b)
    c = client()
    wf = c.workflows.from_json(json.loads(json.dumps(COUPLE if couple else SOLO)))
    wf.set_input("1", "image", c.assets.from_bytes(photo_a, filename=name_a or "personA.png"))
    if couple:
        # The solo graph has no second LoadImage at all: two references of one face
        # put two of the person in the frame.
        wf.set_input("2", "image", c.assets.from_bytes(photo_b, filename=name_b or "personB.png"))
    wf.set_input("90", "portrait", bool(portrait))
    if seed:
        wf.set_input("90", "seed", int(seed) % 0xFFFFFFFF)
        for i in range(1, len(SCENE_TITLES) + 1):
            wf.set_input(str(60 + i), "seed", (int(seed) + i * 101) % 0xFFFFFFFF)
    job = c.submit(wf)
    return job.id


TERMINAL_OK = ("succeeded", "completed", "success")
TERMINAL_BAD = ("failed", "cancelled", "canceled", "error", "expired")


def poll(job_id):
    c = client()
    job = c.jobs.get(job_id)
    st = job.status
    if st in TERMINAL_OK:
        outs = job.get_outputs("91")
        if not outs:
            return {"state": "error", "error": "the job finished without a tape"}
        link = outs[0].get_download_url()
        return {"state": "done", "video_url": str(link.url), "size_kb": outs[0].size_bytes // 1024,
                "scenes": SCENE_TITLES}
    if st in TERMINAL_BAD:
        err = job.error
        msg = err.get("message") if isinstance(err, dict) else (str(err) if err else None)
        node = err.get("class_type") if isinstance(err, dict) else None
        if node == "VHSTape":
            msg = "the tape deck failed: " + (msg or "no detail")
        return {"state": "error", "error": msg or st}

    m = job._model
    qp = getattr(m, "queue_position", None)
    prog = getattr(m, "progress", None)
    if isinstance(prog, dict):
        prog = prog.get("value") or prog.get("percent")
    import datetime as dt
    created = getattr(m, "created_at", None)
    age = None
    if created:
        try:
            age = (dt.datetime.now(dt.timezone.utc) - created).total_seconds()
        except Exception:
            age = None
    n = len(SCENE_TITLES)
    if qp:
        human = f"waiting in the queue, position {qp}"
    elif age is None:
        human = "shooting the tape"
    elif age > 120:
        human = (f"running for {int(age)} s: the worker is loading about 20 GB of models, which only happens on the "
                 "first job after it wakes up. It is not stuck.")
    else:
        # Five generations then the tape; a warm worker spends roughly 8 s a scene.
        i = min(n, int(age // 8) + 1)
        human = f"shooting scene {i} of {n} ({SCENE_TITLES[i - 1]})" if age < n * 8 else "splicing the tape"
    return {"state": "running", "status": human, "raw": st, "queue_position": qp, "progress": prog}


# The queue panel is the same listing the other page uses.
from _core import queue  # noqa: E402,F401
