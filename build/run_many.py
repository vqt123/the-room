"""Run one batched round from the shell: several things hidden in one picture, one job.

Usage: .venv/bin/python run_many.py "a rubber duck" "a taco" "a traffic cone"
Prints every box the round produced, checks them for overlap, and saves the picture.
"""
import os, re, sys, time
from comfy_sdk import Comfy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_find_workflow as m

KEY_RE = re.compile(r"hideit-x(\d+)-y(\d+)-w(\d+)-h(\d+)")


def api_key():
    k = os.environ.get("COMFY_API_KEY")
    if k:
        return k.strip()
    for p in ("~/.config/comfy/staging_api_key", "~/.config/comfy/api_key"):
        f = os.path.expanduser(p)
        if os.path.exists(f):
            return open(f).read().strip()
    raise SystemExit("no API key")


def overlap(a, b):
    ax2, ay2, bx2, by2 = a["x"] + a["w"], a["y"] + a["h"], b["x"] + b["w"], b["y"] + b["h"]
    ox = max(0, min(ax2, bx2) - max(a["x"], b["x"]))
    oy = max(0, min(ay2, by2) - max(a["y"], b["y"]))
    return ox * oy


def main():
    things = sys.argv[1:] or ["a rubber duck", "a taco", "a traffic cone"]
    scene = int(time.time()) % 6
    os.environ["COMFY_BASE_URL"] = open(os.path.join(HERE, "endpoint.txt")).read().strip()
    c = Comfy(api_key=api_key(), timeout=60)
    g, thumbs = m.build(things, scene=scene, difficulty=3, seed=int(time.time()) % 100000)
    t0 = time.time()
    job = c.submit(c.workflows.from_json(g))
    print(f"job {job.id}  {len(things)} things  scene {m.SCENES[scene][0]}  {len(g)} nodes")
    while True:
        j = c.jobs.get(job.id)
        if j.status in ("succeeded", "completed", "success"):
            break
        if j.status in ("failed", "cancelled", "canceled", "error", "expired"):
            raise SystemExit(f"job {j.status}: {j.error}")
        time.sleep(4)
    took = time.time() - t0
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    out = c.jobs.get(job.id).get_outputs("31")[0]
    path = os.path.join(HERE, "results", f"many_{job.id[:8]}.png")
    open(path, "wb").write(out.to_bytes())
    boxes = []
    for thing, node in zip(things, thumbs):
        o = c.jobs.get(job.id).get_outputs(node)
        if not o:
            print(f"  MISSING thumbnail for {thing}")
            continue
        mm = KEY_RE.search(o[0].name or "")
        if not mm:
            print(f"  no key on {thing}: {o[0].name}")
            continue
        x, y, w, h = (int(v) for v in mm.groups())
        boxes.append({"thing": thing, "x": x, "y": y, "w": w, "h": h})
    print(f"took {took:.0f}s, {len(boxes)}/{len(things)} answers, picture {path}")
    for b in boxes:
        print(f"   {b['thing']:<22} x{b['x']:<4} y{b['y']:<4} {b['w']}x{b['h']}")
    bad = [(a['thing'], b['thing'], overlap(a, b)) for i, a in enumerate(boxes) for b in boxes[i + 1:]
           if overlap(a, b) > 0]
    print("overlaps:", bad or "none")


if __name__ == "__main__":
    main()
