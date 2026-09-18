"""Run one seamless-texture round and prove it tiles.

Usage: .venv/bin/python run_texture.py "mossy cobblestone" [max_passes] [target]
Saves the texture and the 3x3 proof, and re-measures the seam HERE, independently of the
node, so the node cannot mark its own homework.
"""
import os, re, sys, time
import numpy as np
from PIL import Image
from comfy_sdk import Comfy

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_find_workflow as m

REPORT_RE = re.compile(r"seam-p(\d+)-from(\d+)-to(\d+)-(ok|gave-up)")


def api_key():
    k = os.environ.get("COMFY_API_KEY")
    if k:
        return k.strip()
    for p in ("~/.config/comfy/staging_api_key", "~/.config/comfy/api_key"):
        f = os.path.expanduser(p)
        if os.path.exists(f):
            return open(f).read().strip()
    raise SystemExit("no API key")


def measure(path):
    """Re-measure from the saved PNG, and report the raw numbers too.

    The ratio on its own has a loophole: a repair that roughs up the interior raises the
    denominator and so flatters the score. So the absolute wrap difference is printed beside
    it, in 0-255 units, next to the picture's own average neighbour difference. A texture
    that really tiles has a wrap difference no bigger than its neighbour difference.
    """
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    wrap_x = np.abs(a[:, -1, :] - a[:, 0, :]).mean()
    inner_x = np.abs(a[:, 1:, :] - a[:, :-1, :]).mean()
    wrap_y = np.abs(a[-1, :, :] - a[0, :, :]).mean()
    inner_y = np.abs(a[1:, :, :] - a[:-1, :, :]).mean()
    ratio = max(wrap_x / (inner_x + 1e-6), wrap_y / (inner_y + 1e-6))
    return {"ratio": ratio, "wrap_x": wrap_x, "wrap_y": wrap_y,
            "neighbour": (inner_x + inner_y) / 2}


def main():
    thing = sys.argv[1] if len(sys.argv) > 1 else "mossy cobblestone"
    passes = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    target = float(sys.argv[3]) if len(sys.argv) > 3 else 1.25
    ep = os.environ.get("TEX_ENDPOINT") or open(os.path.join(HERE, "endpoint.txt")).read().strip()
    os.environ["COMFY_BASE_URL"] = ep
    c = Comfy(api_key=api_key(), timeout=60)
    g = m.build_texture(thing, seed=int(time.time()) % 100000, max_passes=passes, target=target)
    t0 = time.time()
    job = c.submit(c.workflows.from_json(g))
    print(f"job {job.id}  \"{thing}\"  max_passes={passes} target={target}")
    while True:
        j = c.jobs.get(job.id)
        if j.status in ("succeeded", "completed", "success"):
            break
        if j.status in ("failed", "cancelled", "canceled", "error", "expired"):
            raise SystemExit(f"{j.status}: {j.error}")
        time.sleep(4)
    took = time.time() - t0
    os.makedirs(os.path.join(HERE, "results"), exist_ok=True)
    j = c.jobs.get(job.id)
    out = j.get_outputs("31")[0]
    tex = os.path.join(HERE, "results", f"tex_{job.id[:8]}.png")
    open(tex, "wb").write(out.to_bytes())
    tiled = os.path.join(HERE, "results", f"tex_{job.id[:8]}_tiled.png")
    tt = j.get_outputs("32")
    if tt:
        open(tiled, "wb").write(tt[0].to_bytes())
    mm = REPORT_RE.search(out.name or "")
    print(f"took {took:.0f}s")
    if mm:
        p, a, b, verdict = mm.group(1), int(mm.group(2)) / 100, int(mm.group(3)) / 100, mm.group(4)
        print(f"node says : {p} pass(es), seam {a:.2f} -> {b:.2f}, {verdict}")
    else:
        print("node report not on the filename:", out.name)
    mres = measure(tex)
    print(f"checked here: ratio {mres['ratio']:.3f}  (1.00 = the join is as quiet as the texture)")
    print(f"              wrap difference {mres['wrap_x']:.1f} across / {mres['wrap_y']:.1f} down, "
          f"vs {mres['neighbour']:.1f} between neighbouring pixels (0-255)")
    # Measured on this project's own output: a picture with a flat border scores 0.24, which
    # is "seamless" by the ratio and useless as a texture. A real tiling texture has a wrap
    # difference in the same league as its neighbour difference, not far below it.
    if mres["ratio"] < 0.5:
        print("              WARNING ratio below 0.5: the edges are probably a flat border, "
              "not a texture that tiles. Look at the 3x3 proof.")
    print("texture:", tex)
    print("tiled  :", tiled)


if __name__ == "__main__":
    main()
