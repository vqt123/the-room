"""Emit the API-format workflow for "Senior Year '88".

One selfie in, one VHS tape out. Five scenes are generated from the same photo
by Qwen-Image-Edit-2511 (the face is the thing that must survive), batched, and
handed to the private VHSTape node, which is what actually makes it a tape.

Node ids are fixed; the client sets inputs by id (see README.md).
  1  person A photo          2  person B photo (couple tape only; absent from the solo graph)
  21..25 scene prompts       61..65 scene seeds     31/51 shared negative
  90 VHSTape (stamps, label, seconds_per_scene, portrait, seed)
  91 SaveVideo  <- fetch the mp4 from this node
"""
import json

UNET = "qwen_image_edit_2511_fp8mixed.safetensors"
CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
LORA = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"

# date stamp, where they are, what they are wearing
SCENES = [
    ("SEP 04 1987", "in front of a wall of school lockers in a high school hallway on the first day of term",
     "1987 school clothes"),
    ("OCT 16 1987", "in the stands at a high school football game at night under the stadium floodlights",
     "a varsity jacket"),
    ("DEC 20 1987", "at the edge of a school gym decorated for a winter formal with streamers, balloons and paper snowflakes",
     "1987 formal wear"),
    ("MAR 12 1988", "sitting on the hood of an old parked car on a bright spring afternoon",
     "sunglasses and a 1988 spring outfit"),
    ("MAY 20 1988", "on the school lawn holding a rolled diploma",
     "a graduation cap and gown"),
]

LOOK = ("Amateur 1987 home video still, consumer camcorder, warm faded colours, soft focus, "
        "slight motion blur, natural snapshot framing. No text, no captions, no watermark, no borders.")


def prompt_for(where: str, wardrobe: str, couple: bool = False) -> str:
    if couple:
        return (f"Picture 1 and Picture 2 are photographs of two different people. Make one new photograph of those "
                f"two people together {where}, both wearing {wardrobe} with period 1980s hairstyles. Each face, bone "
                f"structure and skin tone stays exactly as in its own picture and both must stay clearly "
                f"recognisable. Exactly two people are in the foreground and nobody else. {LOOK}")
    # "Alone" has to be said more than once: the model otherwise supplies a twin or
    # a date at a dance (cloud jobs 50747dc7 and 9da1b632).
    return (f"Picture 1 is a photograph of one person. Make a new photograph of that same person, alone, {where}, "
            f"wearing {wardrobe} with a period 1980s hairstyle. Their face, bone structure and skin tone stay exactly "
            f"as in Picture 1 and must stay clearly recognisable. Exactly one person is in the foreground: no second "
            f"person beside them, no twin, no double, no date, no friend. {LOOK}")


def scene_prompts(couple: bool = False):
    return [prompt_for(where, wardrobe, couple) for _, where, wardrobe in SCENES]


def stamps() -> str:
    return "\n".join(s for s, _, _ in SCENES)


def graph(couple: bool = False, portrait: bool = False):
    g = {}
    g["1"] = {"class_type": "LoadImage", "_meta": {"title": "person A"}, "inputs": {"image": "personA.png"}}
    if couple:
        g["2"] = {"class_type": "LoadImage", "_meta": {"title": "person B"}, "inputs": {"image": "personB.png"}}
    g["3"] = {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}}
    g["4"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}}
    g["5"] = {"class_type": "VAELoader", "inputs": {"vae_name": VAE}}
    g["6"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["3", 0], "shift": 3.1}}
    g["7"] = {"class_type": "CFGNorm", "inputs": {"model": ["6", 0], "strength": 1.0}}
    g["8"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["7", 0], "lora_name": LORA,
              "strength_model": 1.0}}
    g["9"] = {"class_type": "FluxKontextImageScale", "inputs": {"image": ["1", 0]}}
    g["14"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["5", 0]}}

    # Solo tapes pass ONE reference picture. Passing the same photo twice made the
    # model put two of the person in the frame (cloud job 50747dc7, scenes 1 and 3).
    enc = {"clip": ["4", 0], "vae": ["5", 0], "image1": ["9", 0]}
    if couple:
        enc["image2"] = ["2", 0]
    # The negative is the same empty prompt over the same pictures for every scene,
    # so it is encoded once instead of five times.
    g["31"] = {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "negative (shared)"},
               "inputs": dict(enc, prompt="")}
    g["51"] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["31", 0], "reference_latents_method": "index_timestep_zero"}}

    prompts = scene_prompts(couple)
    for i, (stamp, _, _) in enumerate(SCENES, start=1):
        pos, rpos = str(20 + i), str(40 + i)
        ks, dec = str(60 + i), str(70 + i)
        g[pos] = {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": f"scene {i}: {stamp}"},
                  "inputs": dict(enc, prompt=prompts[i - 1])}
        g[rpos] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
                   "inputs": {"conditioning": [pos, 0], "reference_latents_method": "index_timestep_zero"}}
        g[ks] = {"class_type": "KSampler", "inputs": {"model": ["8", 0], "positive": [rpos, 0],
                 "negative": ["51", 0], "latent_image": ["14", 0], "seed": 1000 + i, "steps": 4, "cfg": 1.0,
                 "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}}
        g[dec] = {"class_type": "VAEDecode", "inputs": {"samples": [ks, 0], "vae": ["5", 0]}}

    # Chain the five decoded scenes into one IMAGE batch, in order.
    prev = "71"
    for i in range(2, len(SCENES) + 1):
        nid = str(80 + i - 1)  # 81..84
        g[nid] = {"class_type": "ImageBatch", "inputs": {"image1": [prev, 0], "image2": [str(70 + i), 0]}}
        prev = nid

    g["90"] = {"class_type": "VHSTape", "_meta": {"title": "VHS Tape (private)"},
               "inputs": {"images": [prev, 0], "stamps": stamps(), "label": "SENIOR YEAR '88",
                          "seconds_per_scene": 2.6, "fps": 24, "portrait": portrait, "seed": 7}}
    g["91"] = {"class_type": "SaveVideo", "inputs": {"video": ["90", 0], "filename_prefix": "senioryear",
               "format": "mp4", "codec": "h264"}}
    return g


if __name__ == "__main__":
    json.dump(graph(couple=False), open("workflow_tape.json", "w"), indent=2)
    json.dump(graph(couple=True), open("workflow_tape_couple.json", "w"), indent=2)
    # the private node on its own: five stills straight to a tape, no models
    only = {
        "1": {"class_type": "LoadImage", "inputs": {"image": "personA.png"}},
        "2": {"class_type": "ImageBatch", "inputs": {"image1": ["1", 0], "image2": ["1", 0]}},
        "3": {"class_type": "VHSTape", "inputs": {"images": ["2", 0], "stamps": "SEP 04 1987\nMAY 20 1988",
              "label": "SENIOR YEAR '88", "seconds_per_scene": 1.5, "fps": 24, "portrait": False, "seed": 7}},
        "4": {"class_type": "SaveVideo", "inputs": {"video": ["3", 0], "filename_prefix": "tape_only",
              "format": "mp4", "codec": "h264"}},
    }
    json.dump(only, open("workflow_tape_node_only.json", "w"), indent=2)
    print("wrote workflow_tape.json workflow_tape_couple.json workflow_tape_node_only.json")
