"""Play one round of Find It from the shell.

Usage: .venv/bin/python run_find.py ["a red soda can"] [difficulty] [scene-index]
Saves the puzzle to results/ and prints the answer box (which the page never sees).
"""
import os, re, sys, time, json
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


def main():
    thing = sys.argv[1] if len(sys.argv) > 1 else "a red soda can"
    difficulty = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    scene_idx = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    ep = open(os.path.join(HERE, "endpoint.txt")).read().strip()
    os.environ["COMFY_BASE_URL"] = ep
    c = Comfy(api_key=api_key(), timeout=60)
    wf = c.workflows.from_file(os.path.join(HERE, "workflow_find.json"))
    wf.set_input("10", "prompt", m.scene_prompt(scene_idx))
    wf.set_input("20", "prompt", m.product_prompt(thing))
    seed = int(time.time()) % 100000
    wf.set_input("15", "seed", seed)
    wf.set_input("25", "seed", seed + 7)
    wf.set_input("30", "difficulty", difficulty)
    wf.set_input("30", "seed", seed + 99)
    job = c.submit(wf)
    print(f"job {job.id} -> {ep}  ({m.SCENES[scene_idx % len(m.SCENES)][0]}, hiding: {thing})", flush=True)
    t0 = time.time()
    while time.time() - t0 < 900:
        j = c.jobs.get(job.id)
        if j.status in ("succeeded", "completed", "failed", "cancelled", "canceled", "error", "expired"):
            break
        time.sleep(4)
    dt = time.time() - t0
    if j.status not in ("succeeded", "completed"):
        print(f"FAILED after {dt:.0f}s: {j.status} {j.error}")
        raise SystemExit(1)
    out = j.get_outputs("31")[0]
    box = KEY_RE.search(out.name or "")
    path = os.path.join(HERE, "results", f"find_{seed}.png")
    open(path, "wb").write(out.to_bytes())
    print(f"ok {dt:.0f}s -> {path}")
    print(f"asset name: {out.name}")
    print("answer box (per mille):", dict(zip("xywh", (int(v) for v in box.groups()))) if box else "NOT FOUND IN NAME")


if __name__ == "__main__":
    main()
