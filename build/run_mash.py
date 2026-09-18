"""Time one mash-up round from the shell: every word in one picture, no hiding."""
import os, sys, time
from comfy_sdk import Comfy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_find_workflow as m


def api_key():
    k = os.environ.get("COMFY_API_KEY")
    if k:
        return k.strip()
    for p in ("~/.config/comfy/staging_api_key", "~/.config/comfy/api_key"):
        f = os.path.expanduser(p)
        if os.path.exists(f):
            return open(f).read().strip()
    raise SystemExit("no API key")


words = sys.argv[1:] or ["a rubber duck", "coke can", "blue horse", "a fire extinguisher", "a disco ball"]
os.environ["COMFY_BASE_URL"] = open(os.path.join(HERE, "endpoint.txt")).read().strip()
c = Comfy(api_key=api_key(), timeout=60)
g, prompt, base = m.build_mash(words, seed=int(time.time()) % 100000)
print("base words:", base)
print("prompt:", prompt[:160], "...")
t0 = time.time()
job = c.submit(c.workflows.from_json(g))
while True:
    j = c.jobs.get(job.id)
    if j.status in ("succeeded", "completed", "success"):
        break
    if j.status in ("failed", "cancelled", "canceled", "error", "expired"):
        raise SystemExit(f"{j.status}: {j.error}")
    time.sleep(2)
os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
path = os.path.join(HERE, "results", f"mash_{job.id[:8]}.png")
open(path, "wb").write(c.jobs.get(job.id).get_outputs("31")[0].to_bytes())
print(f"took {time.time()-t0:.0f}s -> {path}")
