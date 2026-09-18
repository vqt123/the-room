"""Write api/_meta.json from the build folder so the page's debug panel can show what it is talking to. Run before deploy."""
import json, os, subprocess
B = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "build")
spec = json.load(open(os.path.join(B, "build_spec.json")))
def read(n):
    p = os.path.join(B, n); return open(p).read().strip() if os.path.exists(p) else ""
rel = ""
try:
    out = subprocess.run([os.path.join(B, ".venv/bin/comfy"), "build", "release", "ls", "--id", spec["id"]],
                         capture_output=True, text=True, env={**os.environ, "COMFY_BUILDER_URL": os.environ.get("COMFY_BUILDER_URL", "https://stagingplatformapi.comfy.org/builder")}).stdout
    rels = json.loads(out)["data"]["releases"]; rels.sort(key=lambda r: r["createdAt"]); r = rels[-1]
    rel = {"id": r["id"], "version": r.get("version"), "status": r.get("status"), "createdAt": r.get("createdAt")}
except Exception as e:  # noqa: BLE001
    rel = {"error": str(e)[:120]}
meta = {
    "environment": "staging" if "staging" in read("endpoint.txt") + os.environ.get("COMFY_BUILDER_URL", "staging") else "prod",
    "build": {"id": spec["id"], "name": spec.get("name"), "baseComfyVersion": spec["definition"].get("baseComfyVersion"), "baseImage": spec["definition"].get("baseImage")},
    "privatePack": spec["definition"]["customNodes"][0],
    "privateNodes": ["YouTubeFrame (Steal the Moves)", "VHSTape (Senior Year '88)", "HideIt (Find It)"],
    "privateBinaries": ["bin/ytframe", "bin/vhstape", "bin/hideit", "bin/ffmpeg", "bin/yt-dlp"],
    "models": [m["filename"] for m in spec["definition"]["models"]],
    "release": rel,
    "deployment": {"id": read("deployment.txt"), "endpoint": read("endpoint.txt"), "gpu": "rtx-pro-6000-server / US-NE-1"},
    "syncedAt": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
}
json.dump(meta, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "api", "_meta.json"), "w"), indent=2)
print("api/_meta.json:", meta["build"]["id"][:8], rel if isinstance(rel, dict) else rel, meta["deployment"]["id"])
