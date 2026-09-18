"""Submit Senior Year '88 to the deployed endpoint and save the tape.

Usage: .venv/bin/python run_tape.py [personA.png] [personB.png]
Reads endpoint.txt; auth from COMFY_API_KEY or ~/.config/comfy/staging_api_key.
"""
import os, sys, time, json
from comfy_sdk import Comfy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def api_key():
    k = os.environ.get("COMFY_API_KEY")
    if k:
        return k.strip()
    for p in ("~/.config/comfy/staging_api_key", "~/.config/comfy/api_key"):
        f = os.path.expanduser(p)
        if os.path.exists(f):
            return open(f).read().strip()
    raise SystemExit("no API key: set COMFY_API_KEY or write ~/.config/comfy/staging_api_key")


def main():
    a = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "results/profile_vinh_github.png")
    b = sys.argv[2] if len(sys.argv) > 2 else None
    couple = b is not None
    ep = open(os.path.join(HERE, "endpoint.txt")).read().strip()
    os.environ["COMFY_BASE_URL"] = ep
    c = Comfy(api_key=api_key(), timeout=60)
    wf = c.workflows.from_file(os.path.join(HERE, "workflow_tape_couple.json" if couple else "workflow_tape.json"))
    wf.set_input("1", "image", c.assets.from_file(a))
    if couple:
        wf.set_input("2", "image", c.assets.from_file(b))
    job = c.submit(wf)
    print(f"job {job.id} -> {ep}", flush=True)
    t0 = time.time()
    while time.time() - t0 < 900:
        j = c.jobs.get(job.id)
        if j.status in ("succeeded", "completed", "failed", "cancelled", "canceled", "error", "expired"):
            break
        time.sleep(5)
    dt = time.time() - t0
    if j.status not in ("succeeded", "completed"):
        print(f"FAILED after {dt:.0f}s: {j.status} {j.error}")
        raise SystemExit(1)
    outs = j.get_outputs("91")
    if not outs:
        print("succeeded but node 91 returned nothing"); raise SystemExit(1)
    out = os.path.join(HERE, "results", f"tape_{int(time.time())}.mp4")
    data = outs[0].to_bytes()
    open(out, "wb").write(data)
    print(f"ok {dt:.0f}s {len(data)//1024} KB -> {out}")


if __name__ == "__main__":
    main()
