"""ytmove: the private node pack.

Two nodes, each a thin wrapper over a compiled Go binary that ships inside this
folder. Nothing here is published anywhere; the pack reaches a Build only as an
uploaded zip.

  YouTubeFrame ("Steal the Moves")  bin/ytframe
      a URL and a timestamp -> the frame at that moment, as an IMAGE.

  HideIt ("Find It")                bin/hideit
      a busy scene and a product -> the product hidden in the scene, the exact
      box it went into, and a mask over that box. A model can draw both
      pictures but cannot say where it put anything, so only this can score a
      click. The mask is what lets the graph then re-draw just that patch, so
      the thing belongs to the picture instead of sitting on top of it.

  Seamless ("Tiles or it does not ship")   no binary, the loop IS the node
      a prompt -> a texture that actually tiles. Generates it, measures the
      wrap-around seam against the picture's own texture, repairs the seam and
      measures again, and keeps going until the number is under the threshold.
      How many passes that takes is decided by looking at the picture, so no
      caller outside the box can lay the chain out in advance.

  VHSTape ("Senior Year '88")       bin/vhstape
      a batch of stills -> one VHS camcorder tape, as a VIDEO: 4:3 crop, slow
      camcorder drift, chroma bleed, tape grain, scanlines, a rolling tracking
      band, head-switching noise along the bottom edge, an advancing date stamp
      in a hand-drawn bitmap font, snow between the shots, and hiss and mains
      hum underneath. No model does any of that.

The binary-backed nodes carry their own ffmpeg (bin/ffmpeg); Seamless needs none.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import time
import uuid

import numpy as np
import torch
from PIL import Image

import comfy.sample
import comfy.samplers
import node_helpers

_HERE = os.path.dirname(os.path.abspath(__file__))
_BIN = os.environ.get("YTMOVE_BIN") or os.path.join(_HERE, "bin")  # YTMOVE_BIN: local dev override


def _ensure_exec():
    # A zip round-trip can drop the execute bit; put it back at import time.
    for name in ("ytframe", "vhstape", "hideit", "yt-dlp", "ffmpeg"):
        p = os.path.join(_BIN, name)
        if os.path.exists(p):
            mode = os.stat(p).st_mode
            if not mode & stat.S_IXUSR:
                os.chmod(p, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


_ensure_exec()


def _patch_mask(box, w, h, margin):
    """A mask over the box, grown a little, for the pass that re-draws the patch.

    The object is pasted first because that is what makes its position knowable;
    the graph then regenerates this patch so the paste is redrawn in the
    picture's own style. The margin gives the model some surrounding context to
    match against.
    """
    x, y, bw, bh = box
    pad = int(max(bw, bh) * float(margin))
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
    mask = np.zeros((h, w), dtype=np.float32)
    mask[y0:y1, x0:x1] = 1.0
    return torch.from_numpy(mask)[None, ...]


def _save_tensor(images, path):
    """First image of a batch -> a PNG on disk."""
    arr = (images[0].detach().cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
    Image.fromarray(arr).save(path)


def _to_tensor(path):
    img = Image.open(path).convert("RGB")
    arr = np.asarray(img).astype(np.float32) / 255.0
    return torch.from_numpy(arr)[None, ...]


class YouTubeFrame:
    CATEGORY = "ytmove (private)"
    RETURN_TYPES = ("IMAGE", "STRING")
    RETURN_NAMES = ("frame", "info")
    FUNCTION = "grab"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "url": ("STRING", {"default": "https://www.youtube.com/watch?v=aqz-KE-bpKQ", "multiline": False}),
                "timestamp": ("STRING", {"default": "1:05", "multiline": False}),
            },
            "optional": {
                "max_height": ("INT", {"default": 720, "min": 144, "max": 2160, "step": 1}),
            },
        }

    @classmethod
    def IS_CHANGED(cls, url, timestamp, max_height=720):
        # Same url+timestamp is the same frame; let the executor cache it.
        return f"{url}|{timestamp}|{max_height}"

    def grab(self, url, timestamp, max_height=720):
        out_dir = os.path.join(tempfile.gettempdir(), "ytmove")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f"{uuid.uuid4().hex}.png")
        cmd = [
            os.path.join(_BIN, "ytframe"),
            "-url", url.strip(),
            "-time", timestamp.strip(),
            "-out", out,
            "-height", str(int(max_height)),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError(f"YouTubeFrame failed: {proc.stderr.strip() or proc.stdout.strip()}")
        frame = _to_tensor(out)
        h, w = frame.shape[1], frame.shape[2]
        info = f"{url.strip()} @ {timestamp.strip()} -> {w}x{h}"
        try:
            os.remove(out)
        except OSError:
            pass
        return (frame, info)


DEFAULT_STAMPS = "SEP 04 1987\nOCT 16 1987\nDEC 20 1987\nMAR 12 1988\nMAY 20 1988"


def _video_out(path):
    """Wrap the finished mp4 as a ComfyUI VIDEO so SaveVideo can write it out."""
    from comfy_api.input_impl import VideoFromFile

    return VideoFromFile(path)


def _sweep_old(root, max_age=3600):
    # The mp4 has to outlive this node's execute() so SaveVideo can read it, so
    # nothing is deleted inline. Drop the previous runs' directories instead.
    try:
        now = time.time()
        for name in os.listdir(root):
            p = os.path.join(root, name)
            try:
                if os.path.isdir(p) and now - os.path.getmtime(p) > max_age:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


class VHSTape:
    """A batch of stills in, one VHS tape out."""

    CATEGORY = "ytmove (private)"
    RETURN_TYPES = ("VIDEO", "STRING")
    RETURN_NAMES = ("tape", "info")
    FUNCTION = "tape"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "stamps": ("STRING", {"default": DEFAULT_STAMPS, "multiline": True}),
            },
            "optional": {
                "label": ("STRING", {"default": "SENIOR YEAR '88", "multiline": False}),
                "seconds_per_scene": ("FLOAT", {"default": 2.6, "min": 0.6, "max": 10.0, "step": 0.1}),
                "fps": ("INT", {"default": 24, "min": 8, "max": 60}),
                "portrait": ("BOOLEAN", {"default": False}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF}),
            },
        }

    def tape(self, images, stamps, label="", seconds_per_scene=2.6, fps=24, portrait=False, seed=0):
        root = os.path.join(tempfile.gettempdir(), "vhstape_jobs")
        os.makedirs(root, exist_ok=True)
        _sweep_old(root)
        work = tempfile.mkdtemp(dir=root)

        n = int(images.shape[0])
        if n == 0:
            raise RuntimeError("VHSTape: no images on the batch")
        paths = []
        for i in range(n):
            arr = (images[i].detach().cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
            p = os.path.join(work, f"scene_{i:02d}.png")
            Image.fromarray(arr).save(p)
            paths.append(p)

        marks = [ln.strip() for ln in stamps.splitlines() if ln.strip()]
        out = os.path.join(work, "tape.mp4")
        cmd = [
            os.path.join(_BIN, "vhstape"),
            "-frames", ",".join(paths),
            "-stamps", "|".join(marks),
            "-out", out,
            "-seconds", f"{float(seconds_per_scene):.2f}",
            "-fps", str(int(fps)),
            "-seed", str(int(seed)),
        ]
        if label.strip():
            cmd += ["-label", label.strip()]
        if portrait:
            cmd += ["-portrait"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not os.path.exists(out):
            raise RuntimeError(f"VHSTape failed: {proc.stderr.strip() or proc.stdout.strip()}")

        size = os.path.getsize(out)
        secs = n * float(seconds_per_scene)
        info = f"{n} scenes, ~{secs:.1f}s, {size // 1024} KB"
        return (_video_out(out), info)


class HideIt:
    """Hide the product in the scene and hand back the answer.

    The key output is meant to be wired into SaveImage's filename_prefix: the
    answer then rides out as the saved file's NAME, where the serving app can
    read it and the person looking at the picture cannot.
    """

    CATEGORY = "ytmove (private)"
    RETURN_TYPES = ("IMAGE", "MASK", "STRING")
    RETURN_NAMES = ("puzzle", "patch", "key")
    FUNCTION = "hide"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "scene": ("IMAGE",),
                "product": ("IMAGE",),
            },
            "optional": {
                "difficulty": ("INT", {"default": 3, "min": 1, "max": 5}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF}),
                "keep_alpha": ("BOOLEAN", {"default": False}),
                "patch_margin": ("FLOAT", {"default": 0.30, "min": 0.0, "max": 1.5, "step": 0.05}),
            },
        }

    def hide(self, scene, product, difficulty=3, seed=0, keep_alpha=False, patch_margin=0.30):
        work = tempfile.mkdtemp(prefix="hideit_")
        try:
            sp = os.path.join(work, "scene.png")
            pp = os.path.join(work, "product.png")
            op = os.path.join(work, "puzzle.png")
            _save_tensor(scene, sp)
            _save_tensor(product, pp)
            cmd = [os.path.join(_BIN, "hideit"), "-scene", sp, "-product", pp, "-out", op,
                   "-difficulty", str(int(difficulty)), "-seed", str(int(seed) or 1)]
            if keep_alpha:
                cmd.append("-keep-alpha")
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if proc.returncode != 0 or not os.path.exists(op):
                raise RuntimeError(f"HideIt failed: {proc.stderr.strip() or proc.stdout.strip()}")
            key, box = "", None
            for line in proc.stdout.splitlines():
                if line.startswith("key="):
                    key = line[4:].strip()
                elif line.startswith("pixels="):
                    nums = line[len("pixels="):].split()[0]
                    box = [int(v) for v in nums.split(",")]
            if not key or box is None:
                raise RuntimeError("HideIt produced no key")
            puzzle = _to_tensor(op)
            h, w = int(puzzle.shape[1]), int(puzzle.shape[2])
            return (puzzle, _patch_mask(box, w, h, patch_margin), key)
        finally:
            shutil.rmtree(work, ignore_errors=True)


# --------------------------------------------------------------------------------------
# Seamless: generate, measure, repair, measure again, until it actually tiles.
#
# This is the node that cannot be a chain of stock jobs, and the reason is the `while`.
# The number of repair passes is decided by looking at the picture, so a caller outside the
# box cannot lay the chain out in advance: it has to generate, download, measure, decide,
# upload, submit again - a round trip per turn, each one a chance to land on a cold worker.
# Here every turn is a function call against weights that are already resident.
#
# The measurement is not a model and not a guess. A texture tiles when the pixels down its
# right edge continue into the pixels down its left edge as smoothly as any two neighbouring
# columns inside the picture. So: take the mean absolute difference across the wrap, divide
# by the mean absolute difference between neighbouring columns inside. A ratio of 1 means the
# join is as quiet as the rest of the picture. Anything above about 1.3 is a seam you can see.
# Dividing by the interior is what makes the number mean the same thing for a smooth stone
# wall and for gravel.
#
# The repair uses the oldest trick there is: roll the image half its width and half its
# height. The four corners meet in the middle, and the two seams that were at the edges are
# now a cross through the centre of the picture, where the model can be asked to paint over
# them. Roll it back and the edges are continuous, because they are the pixels the model just
# drew as one continuous piece.
# --------------------------------------------------------------------------------------

def _seam_ratio(img):
    """How much louder the wrap-around join is than the picture's own texture. 1.0 = invisible."""
    x = img[0]
    wrap_x = (x[:, -1, :] - x[:, 0, :]).abs().mean()
    inner_x = (x[:, 1:, :] - x[:, :-1, :]).abs().mean()
    wrap_y = (x[-1, :, :] - x[0, :, :]).abs().mean()
    inner_y = (x[1:, :, :] - x[:-1, :, :]).abs().mean()
    rx = float(wrap_x / (inner_x + 1e-6))
    ry = float(wrap_y / (inner_y + 1e-6))
    return max(rx, ry), rx, ry


def _cross_mask(h, w, band, feather):
    """A cross through the middle: the two seams, after the image has been rolled."""
    m = torch.zeros((h, w), dtype=torch.float32)
    bw, bh = max(8, int(w * band)), max(8, int(h * band))
    m[:, w // 2 - bw // 2: w // 2 + bw // 2] = 1.0
    m[h // 2 - bh // 2: h // 2 + bh // 2, :] = 1.0
    if feather > 0:
        k = max(3, int(min(h, w) * feather) | 1)
        pad = k // 2
        blur = torch.nn.functional.avg_pool2d(
            torch.nn.functional.pad(m[None, None], (pad, pad, pad, pad), mode="replicate"),
            kernel_size=k, stride=1)
        m = blur[0, 0].clamp(0.0, 1.0)
    return m[None, ...]


def _tiled_proof(img, n=3):
    """The picture repeated n by n, shrunk back to one frame, so a seam has nowhere to hide."""
    x = img[0].permute(2, 0, 1)[None]
    h, w = x.shape[2], x.shape[3]
    small = torch.nn.functional.interpolate(x, size=(max(1, h // n), max(1, w // n)),
                                            mode="area")
    row = torch.cat([small] * n, dim=3)
    grid = torch.cat([row] * n, dim=2)
    return grid[0].permute(1, 2, 0)[None]


class Seamless:
    CATEGORY = "ytmove (private)"
    RETURN_TYPES = ("IMAGE", "IMAGE", "STRING")
    RETURN_NAMES = ("texture", "tiled", "report")
    FUNCTION = "make"

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "positive": ("CONDITIONING",),
                "negative": ("CONDITIONING",),
                "vae": ("VAE",),
                "latent": ("LATENT",),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xFFFFFFFF}),
                "steps": ("INT", {"default": 4, "min": 1, "max": 50}),
                "cfg": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 20.0, "step": 0.1}),
                "sampler_name": (comfy.samplers.KSampler.SAMPLERS, {"default": "euler"}),
                "scheduler": (comfy.samplers.KSampler.SCHEDULERS, {"default": "simple"}),
            },
            "optional": {
                "max_passes": ("INT", {"default": 4, "min": 0, "max": 8}),
                "target": ("FLOAT", {"default": 1.25, "min": 1.0, "max": 4.0, "step": 0.01}),
                "band": ("FLOAT", {"default": 0.16, "min": 0.04, "max": 0.5, "step": 0.01}),
                "feather": ("FLOAT", {"default": 0.02, "min": 0.0, "max": 0.2, "step": 0.005}),
                "repair_denoise": ("FLOAT", {"default": 0.60, "min": 0.1, "max": 1.0, "step": 0.05}),
            },
        }

    def _sample(self, model, pos, neg, latent, seed, steps, cfg, sampler_name, scheduler,
                denoise, mask=None):
        noise = comfy.sample.prepare_noise(latent, seed)
        return comfy.sample.sample(model, noise, steps, cfg, sampler_name, scheduler,
                                   pos, neg, latent, denoise=denoise, noise_mask=mask, seed=seed)

    def make(self, model, positive, negative, vae, latent, seed, steps, cfg, sampler_name,
             scheduler, max_passes=4, target=1.25, band=0.16, feather=0.02, repair_denoise=0.60):
        t0 = time.time()
        samples = self._sample(model, positive, negative, latent["samples"], seed, steps, cfg,
                               sampler_name, scheduler, 1.0)
        img = vae.decode(samples)
        if img.dim() == 5:
            img = img.reshape(-1, img.shape[-3], img.shape[-2], img.shape[-1])
        h, w = img.shape[1], img.shape[2]

        first, _, _ = _seam_ratio(img)
        worst = first
        passes, trail = 0, [first]
        for p in range(int(max_passes)):
            if worst <= target:
                break
            rolled = torch.roll(img, shifts=(h // 2, w // 2), dims=(1, 2))
            lat = vae.encode(rolled[:, :, :, :3])
            # The repair has to be told what it is looking at, or the edit model drifts back
            # towards whatever the original conditioning referenced (here, a blank canvas).
            pos = node_helpers.conditioning_set_values(positive, {"reference_latents": [lat]})
            mask = _cross_mask(h, w, band, feather)
            fixed = self._sample(model, pos, negative, lat, seed + 1 + p, steps, cfg,
                                 sampler_name, scheduler, float(repair_denoise), mask=mask)
            out = vae.decode(fixed)
            if out.dim() == 5:
                out = out.reshape(-1, out.shape[-3], out.shape[-2], out.shape[-1])
            img = torch.roll(out, shifts=(-(h // 2), -(w // 2)), dims=(1, 2))
            worst, _, _ = _seam_ratio(img)
            passes, _ = p + 1, trail.append(worst)
            print(f"[Seamless] pass {p + 1}: seam ratio {worst:.3f} (target {target})", flush=True)

        took = time.time() - t0
        ok = "ok" if worst <= target else "gave-up"
        print(f"[Seamless] {' -> '.join(f'{v:.3f}' for v in trail)} in {passes} "
              f"pass(es), {took:.1f}s, {ok}", flush=True)
        # Filename-safe, so the graph can hang it off SaveImage and the caller reads the
        # whole story off the picture's own name.
        report = (f"seam-p{passes}-from{int(round(first * 100))}-to{int(round(worst * 100))}-{ok}")
        return (img, _tiled_proof(img), report)


NODE_CLASS_MAPPINGS = {"YouTubeFrame": YouTubeFrame, "VHSTape": VHSTape, "HideIt": HideIt,
                       "Seamless": Seamless}
NODE_DISPLAY_NAME_MAPPINGS = {
    "YouTubeFrame": "YouTube Frame (private)",
    "VHSTape": "VHS Tape (private)",
    "HideIt": "Hide It (private)",
    "Seamless": "Seamless (private)",
}
