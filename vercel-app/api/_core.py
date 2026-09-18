"""Shared logic for the two serverless functions. Everything talks to the Dev Platform endpoint in COMFY_BASE_URL
with COMFY_API_KEY. The private YouTubeFrame node inside that endpoint does the frame grab; nothing is fetched here."""
import json, os, re
from comfy_sdk import Comfy

GRAPH = json.loads('{"1": {"class_type": "YouTubeFrame", "_meta": {"title": "YouTube Frame (private)"}, "inputs": {"url": "https://www.youtube.com/watch?v=Ewqq-3xJFdI", "timestamp": "3:20", "max_height": 720}}, "2": {"class_type": "LoadImage", "_meta": {"title": "profile picture"}, "inputs": {"image": "profile.png"}}, "3": {"class_type": "UNETLoader", "inputs": {"unet_name": "qwen_image_edit_2511_fp8mixed.safetensors", "weight_dtype": "default"}}, "4": {"class_type": "CLIPLoader", "inputs": {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}}, "5": {"class_type": "VAELoader", "inputs": {"vae_name": "qwen_image_vae.safetensors"}}, "6": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["3", 0], "shift": 3.1}}, "7": {"class_type": "CFGNorm", "inputs": {"model": ["6", 0], "strength": 1.0}}, "8": {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["7", 0], "lora_name": "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors", "strength_model": 1.0}}, "9": {"class_type": "FluxKontextImageScale", "inputs": {"image": ["1", 0]}}, "10": {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "positive"}, "inputs": {"clip": ["4", 0], "prompt": "In Picture 1, replace only the main person in the centre of the frame with the person from Picture 2. Everyone else in Picture 1 and everything else stays exactly as it is: the same body pose, the same move mid-motion, the same camera framing, background, floor and lighting. The replaced person must have the face, hair, skin tone and build of the person in Picture 2 and must be caught in the identical pose. Photorealistic, natural, no text.", "vae": ["5", 0], "image1": ["9", 0], "image2": ["2", 0]}}, "11": {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "negative"}, "inputs": {"clip": ["4", 0], "prompt": "", "vae": ["5", 0], "image1": ["9", 0], "image2": ["2", 0]}}, "12": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["10", 0], "reference_latents_method": "index_timestep_zero"}}, "13": {"class_type": "FluxKontextMultiReferenceLatentMethod", "inputs": {"conditioning": ["11", 0], "reference_latents_method": "index_timestep_zero"}}, "14": {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["5", 0]}}, "15": {"class_type": "KSampler", "inputs": {"model": ["8", 0], "positive": ["12", 0], "negative": ["13", 0], "latent_image": ["14", 0], "seed": 7, "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}}, "16": {"class_type": "VAEDecode", "inputs": {"samples": ["15", 0], "vae": ["5", 0]}}, "17": {"class_type": "SaveImage", "inputs": {"images": ["16", 0], "filename_prefix": "ytmove"}}}')

DEFAULT_WHO = "the main person in the centre of the frame"

def prompt_for(who):
    who = (who or "").strip() or DEFAULT_WHO
    return (f"In Picture 1, replace only {who} with the person from Picture 2. Everyone else in Picture 1 and everything "
            "else stays exactly as it is: the same body pose, the same move mid-motion, the same camera framing, background, "
            "floor and lighting, including any mirror reflection of that person. The replaced person must have the face, "
            "hair, skin tone and build of the person in Picture 2 and must be caught in the identical pose. "
            "Photorealistic, natural, no text.")

def client():
    base = os.environ.get("COMFY_BASE_URL", "").strip()
    key = os.environ.get("COMFY_API_KEY", "").strip()
    if not base or not key:
        raise RuntimeError("server is missing COMFY_BASE_URL or COMFY_API_KEY")
    return Comfy(api_key=key, timeout=50.0, client_info="steal-the-moves-vercel")

def check_password(given):
    want = os.environ.get("APP_PASSWORD", "").strip()
    return (not want) or (given or "").strip() == want

def submit(url, timestamp, who, photo_bytes, photo_name):
    if not re.match(r"^https?://", url or ""):
        raise ValueError("that is not a link")
    c = client()
    wf = c.workflows.from_json(json.loads(json.dumps(GRAPH)))
    wf.set_input("1", "url", url.strip())
    wf.set_input("1", "timestamp", (timestamp or "0").strip() or "0")
    wf.set_input("2", "image", c.assets.from_bytes(photo_bytes, filename=photo_name or "profile.png"))
    wf.set_input("10", "prompt", prompt_for(who))
    job = c.submit(wf)
    return job.id

def poll(job_id):
    c = client()
    job = c.jobs.get(job_id)
    st = job.status
    if st in ("completed", "succeeded", "success"):
        outs = job.get_outputs("17")
        if not outs:
            return {"state": "error", "error": "job finished with no image"}
        import base64
        return {"state": "done", "result_b64": base64.b64encode(outs[0].to_bytes()).decode()}
    if st in ("failed", "cancelled", "canceled", "error", "expired"):
        err = job.error
        msg = err.get("message") if isinstance(err, dict) else str(err)
        node = err.get("class_type") if isinstance(err, dict) else None
        if node == "YouTubeFrame" and "not a bot" in (msg or ""):
            msg = ("YouTube refused the cloud's address (sign-in check). Direct video links (.mp4) work; YouTube links "
                   "work once the pack carries a login. Node error: " + msg)
        return {"state": "error", "error": msg or st}
    m = job._model
    qp = getattr(m, "queue_position", None)
    prog = getattr(m, "progress", None)
    if isinstance(prog, dict):
        prog = prog.get("value") or prog.get("percent")
    # The gateway never fills started_at (verified in platform-gateway; nothing sets it), so age since
    # submission is the only clock. Measured 2026-09-18 on staging: warm worker 4 to 12 s end to end;
    # the first job after a worker wakes spends 2 to 6 minutes loading about 20 GB of models.
    import datetime as _dt
    created = getattr(m, "created_at", None)
    age = None
    if created:
        try:
            age = (_dt.datetime.now(_dt.timezone.utc) - created).total_seconds()
        except Exception:
            age = None
    if qp:
        human = f"queued, position {qp} (a job ahead of yours may be waking the worker: a few minutes)"
    elif age is not None and age > 90:
        human = (f"running for {int(age)} s: the worker is loading its models from the network volume "
                 "(first job after a wake takes 2 to 6 minutes). The job is not stuck.")
    elif age is not None and age > 20:
        human = "still running: if the worker just woke up it loads about 20 GB of models before the first swap"
    else:
        human = "running: grabbing the frame in the cloud, then the swap (about 10 s on a warm worker)"
    return {"state": "running", "status": human, "raw": st, "queue_position": qp, "progress": prog}


TERMINAL = ("succeeded", "completed", "success", "failed", "cancelled", "canceled", "error", "expired")

def queue(job_id=None):
    """What the endpoint is doing right now: jobs waiting, whether one is running, and where job_id sits."""
    c = client()
    low = c._low
    listing = low.request("GET", "/api/v2/jobs?limit=30") if hasattr(low, "request") else None
    jobs = []
    if listing is None:
        import httpx, os
        r = httpx.get(os.environ["COMFY_BASE_URL"].rstrip("/") + "/api/v2/jobs?limit=30",
                      headers={"Authorization": "Bearer " + os.environ["COMFY_API_KEY"]}, timeout=20)
        listing = r.json()
    raw = listing.get("jobs") if isinstance(listing, dict) else listing
    for j in (raw or []):
        if isinstance(j, dict) and j.get("status") not in TERMINAL:
            jobs.append(j.get("id"))
    details = []
    for jid in jobs[:20]:
        try:
            jb = c.jobs.get(jid)
            m = jb._model
            details.append({"id": jid, "created": str(getattr(m, "created_at", "") or ""), "started": bool(getattr(m, "started_at", None))})
        except Exception:  # noqa: BLE001
            pass
    details.sort(key=lambda d: d["created"])
    ahead = None
    if job_id:
        ids = [d["id"] for d in details]
        ahead = ids.index(job_id) if job_id in ids else None
    return {"waiting": len(details), "running": any(d["started"] for d in details), "ahead": ahead,
            "jobs": [{"id": d["id"][:8], "started": d["started"]} for d in details]}
