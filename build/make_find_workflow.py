"""Emit the API-format workflow for "Find It".

The scene and the product are both drawn by Qwen-Image-Edit from a blank canvas.
The private HideIt node then hides the product in the scene and returns the box
it used; that box is wired into SaveImage's filename_prefix, so the answer rides
out as the saved file's NAME and only the serving app ever reads it.

Two variants:
  workflow_find.json         product drawn from a typed name
  workflow_find_upload.json  product uploaded as a photo (node 2)

Node ids the client sets:
  10 scene prompt       15 scene seed
  20 product prompt     25 product seed      (typed variant only)
  2  product photo                            (upload variant only)
  30 HideIt: difficulty, seed -> (puzzle, patch mask, key)
  47 the redraw prompt (name the same thing as node 20)
  53 redraw seed and denoise
  31 SaveImage  <- fetch the puzzle from this node; its NAME carries the answer
  32 SaveImage  <- the product on its own, shown to the player as the target
"""
import json
import random
import re

UNET = "qwen_image_edit_2511_fp8mixed.safetensors"
CLIP = "qwen_2.5_vl_7b_fp8_scaled.safetensors"
VAE = "qwen_image_vae.safetensors"
LORA = "Qwen-Image-Edit-2511-Lightning-4steps-V1.0-bf16.safetensors"

STYLE = ("flat bright colours, thin black outlines, cartoon illustration, even detail across the whole frame, "
         "no single focal point, no text, no watermark, no border")

SCENES = [
    ("carnival", f"a packed seaside carnival seen from above, hundreds of tiny people in striped clothes, stalls, "
                 f"tents, rides, dogs and balloons, {STYLE}"),
    ("attic", f"a cluttered attic packed with hundreds of small objects, toys, books, lamps, instruments, boxes and "
              f"cats spread evenly across the whole frame, {STYLE}"),
    ("market", f"a crowded city street market seen from above, hundreds of tiny shoppers, awnings, crates of fruit, "
               f"bicycles, market stalls and pigeons, {STYLE}"),
    ("sports day", f"a school sports day on a huge field, hundreds of tiny children running races, parents watching, "
                   f"bunting, deck chairs, picnic blankets and dogs, {STYLE}"),
    ("toy shop", f"the inside of an enormous toy shop, shelves from floor to ceiling crammed with hundreds of "
                 f"different toys, teddy bears, robots, balls and board games, {STYLE}"),
    ("pool", f"a packed public swimming pool seen from above, hundreds of tiny swimmers, inflatable rings, towels, "
             f"sun loungers, parasols and lifeguards, {STYLE}"),
]

SCENE_NEG = "text, watermark, blank, empty, plain background, one large subject, photograph, blurry"
PRODUCT_NEG = "photo, shadow, gradient background, scene, hands, people, text, watermark, border"
REFINE_NEG = "pasted, cut out, sticker, out of place, photo, blurry, text, watermark, different object"

# One round hides everything the room submitted in that window. Past this the picture runs
# out of quiet places and the round runs past its own cadence.
MAX_THINGS = 6

# The mash-up mode's starting point: three of these, drawn at random, describe the scene
# before any of the room's own words are added, so two rounds with the same words still
# come out different.
SCENE_WORDS = ["neon", "underwater", "medieval", "desert", "rainy", "sunset", "arctic", "jungle",
               "cyberpunk", "pastel", "foggy", "volcanic", "candlelit", "stormy", "autumnal", "moonlit",
               "tropical", "industrial", "baroque", "snowy", "underground", "floating", "overgrown",
               "crystalline", "windswept", "carnival", "abandoned", "golden", "misty", "electric"]

MAX_WORDS = 24            # one mash-up picture holds this many of the room's words

MASH_NEG = "text, watermark, caption, letters, signature, border, blurry, low detail, empty, plain background"


def mash_prompt(words, rng=None):
    """Everything the room typed in one window, dropped into one randomly described scene."""
    rng = rng or random
    base = rng.sample(SCENE_WORDS, 3)
    words = [w for w in (str(x).strip() for x in words) if w][:24]
    listed = ", ".join(words) if words else "whatever belongs there"
    return (f"One picture of a {base[0]}, {base[1]}, {base[2]} scene containing {listed}. Put every one of them in "
            f"the same frame, all clearly visible, arranged naturally. Rich detail everywhere, cartoon illustration, "
            f"thin black outlines, flat bright colours, no text, no watermark, no border."), base


def scene_prompt(name_or_index=0):
    if isinstance(name_or_index, int):
        return SCENES[name_or_index % len(SCENES)][1]
    for n, p in SCENES:
        if n == name_or_index:
            return p
    return SCENES[0][1]


def product_prompt(thing: str) -> str:
    thing = (thing or "red soda can").strip()
    for article in ("a ", "an ", "the "):   # "a single a yellow duck" reads badly
        if thing.lower().startswith(article):
            thing = thing[len(article):]
            break
    return (f"Draw a single {thing}, one object only, simple flat cartoon illustration with a thin black outline, "
            f"centred and filling most of the frame, completely plain pure white background, nothing else in the "
            f"picture, no shadow, no text.")


def _bare(thing):
    thing = (thing or "object").strip()
    for article in ("a ", "an ", "the "):
        if thing.lower().startswith(article):
            return thing[len(article):]
    return thing


def refine_prompt_many(things):
    """The redraw pass for a whole round. Qwen takes at most three reference pictures and
    one of them is the puzzle, so past the first two things the prompt is all the model has
    to go on; naming every one of them is what stops it painting any of them out."""
    names = [_bare(t) for t in things]
    if len(names) == 1:
        return refine_prompt(things[0])
    listed = ", ".join("a " + n for n in names[:-1]) + " and a " + names[-1]
    return (f"{listed.capitalize()} have each been placed into this picture. Keep every one of them exactly where it "
            f"is, at exactly the same size, still clearly what it is. Only give each of them the same thin black "
            f"outline, the same flat colours and the same shading as the things drawn around it, so they look drawn "
            f"into the picture rather than stuck on top. Do not remove any of them. Do not replace any of them with "
            f"anything else. Change nothing else in the picture.")


def refine_prompt(thing: str) -> str:
    thing = (thing or "object").strip()
    for article in ("a ", "an ", "the "):
        if thing.lower().startswith(article):
            thing = thing[len(article):]
            break
    return (f"Picture 2 shows a {thing}. That same {thing} has been placed into Picture 1. Keep it exactly where it "
            f"is, at exactly the same size, still clearly a {thing}. Only give it the same thin black outline, the "
            f"same flat colours and the same shading as the things drawn around it, so it looks drawn into the "
            f"picture rather than stuck on top. Do not remove it. Do not replace it with anything else. Change "
            f"nothing else in the picture.")


def _chain(g, base_node, prompt, negative, seed, ids, extra=None):
    """One generation pass: encode, reference, encode latent, sample, decode. `extra` adds
    inputs to the positive encoder, e.g. a second reference image."""
    scale, pos, neg, rpos, rneg, enc, ks, dec = ids
    g[scale] = {"class_type": "FluxKontextImageScale", "inputs": {"image": [base_node, 0]}}
    g[pos] = {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": dict({"clip": ["4", 0], "prompt": prompt, "vae": ["5", 0], "image1": [scale, 0]},
                             **(extra or {}))}
    g[neg] = {"class_type": "TextEncodeQwenImageEditPlus",
              "inputs": {"clip": ["4", 0], "prompt": negative, "vae": ["5", 0], "image1": [scale, 0]}}
    g[rpos] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": [pos, 0], "reference_latents_method": "index_timestep_zero"}}
    g[rneg] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": [neg, 0], "reference_latents_method": "index_timestep_zero"}}
    g[enc] = {"class_type": "VAEEncode", "inputs": {"pixels": [scale, 0], "vae": ["5", 0]}}
    g[ks] = {"class_type": "KSampler", "inputs": {"model": ["8", 0], "positive": [rpos, 0], "negative": [rneg, 0],
             "latent_image": [enc, 0], "seed": seed, "steps": 4, "cfg": 1.0, "sampler_name": "euler",
             "scheduler": "simple", "denoise": 1.0}}
    g[dec] = {"class_type": "VAEDecode", "inputs": {"samples": [ks, 0], "vae": ["5", 0]}}
    return dec


def graph(upload=False):
    g = {}
    g["1"] = {"class_type": "EmptyImage", "_meta": {"title": "scene canvas"},
              "inputs": {"width": 1328, "height": 1328, "batch_size": 1, "color": 8421504}}
    g["3"] = {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}}
    g["4"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}}
    g["5"] = {"class_type": "VAELoader", "inputs": {"vae_name": VAE}}
    g["6"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["3", 0], "shift": 3.1}}
    g["7"] = {"class_type": "CFGNorm", "inputs": {"model": ["6", 0], "strength": 1.0}}
    g["8"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["7", 0], "lora_name": LORA,
              "strength_model": 1.0}}

    scene_out = _chain(g, "1", scene_prompt(0), SCENE_NEG, 1234,
                       ("9", "10", "11", "12", "13", "14", "15", "16"))

    if upload:
        g["2"] = {"class_type": "LoadImage", "_meta": {"title": "product photo"}, "inputs": {"image": "product.png"}}
        product_out = "2"
    else:
        g["18"] = {"class_type": "EmptyImage", "_meta": {"title": "product canvas"},
                   "inputs": {"width": 768, "height": 768, "batch_size": 1, "color": 16777215}}
        product_out = _chain(g, "18", product_prompt("a red soda can"), PRODUCT_NEG, 77,
                             ("19", "20", "21", "22", "23", "24", "25", "26"))

    g["30"] = {"class_type": "HideIt", "_meta": {"title": "Hide It (private)"},
               "inputs": {"scene": [scene_out, 0], "product": [product_out, 0], "difficulty": 3, "seed": 7,
                          "keep_alpha": False, "patch_margin": 0.45}}

    # The paste is what makes the position knowable. This second pass re-draws
    # only that patch, so the thing ends up in the picture's own line weight and
    # palette instead of sitting on top of it. Stock nodes from here on.
    # A small feather: the patch around a difficulty-3 object is only about 80 px,
    # and a wide feather leaves almost nothing at full strength to redraw.
    g["46"] = {"class_type": "FeatherMask", "inputs": {"mask": ["30", 1], "left": 6, "top": 6,
               "right": 6, "bottom": 6}}
    # The product picture goes in as a second reference: without it the model
    # treats the patch as scene and paints the object away (job ef9290c9 lost a
    # duck that way).
    g["47"] = {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "redraw the patch"},
               "inputs": {"clip": ["4", 0], "prompt": refine_prompt("a red soda can"), "vae": ["5", 0],
                          "image1": ["30", 0], "image2": [product_out, 0]}}
    g["48"] = {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "redraw negative"},
               "inputs": {"clip": ["4", 0], "prompt": REFINE_NEG, "vae": ["5", 0], "image1": ["30", 0]}}
    g["49"] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["47", 0], "reference_latents_method": "index_timestep_zero"}}
    g["50"] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["48", 0], "reference_latents_method": "index_timestep_zero"}}
    g["51"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["30", 0], "vae": ["5", 0]}}
    g["52"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["51", 0], "mask": ["46", 0]}}
    g["53"] = {"class_type": "KSampler", "inputs": {"model": ["8", 0], "positive": ["49", 0],
               "negative": ["50", 0], "latent_image": ["52", 0], "seed": 555, "steps": 4, "cfg": 1.0,
               "sampler_name": "euler", "scheduler": "simple", "denoise": 0.45}}
    g["54"] = {"class_type": "VAEDecode", "inputs": {"samples": ["53", 0], "vae": ["5", 0]}}
    # The key output becomes the saved file's name: that is how the answer reaches
    # the serving app without ever reaching the browser.
    g["31"] = {"class_type": "SaveImage", "inputs": {"images": ["54", 0], "filename_prefix": ["30", 2]}}
    # The player is shown WHAT to look for, never where: a real hidden-object book
    # prints the thing on the page too.
    g["32"] = {"class_type": "SaveImage", "_meta": {"title": "the thing itself"},
               "inputs": {"images": [product_out, 0], "filename_prefix": "thing"}}
    return g


VOWELS = "aeiou"


def to_ing(verb):
    """Turn whatever somebody typed into something that reads after a noun.

    "a shark dance" is wrong and "a shark dancing" is right, and people type both. This is
    the usual English spelling rules and nothing clever: it gets the common verbs right and
    leaves anything it does not recognise alone, which is the safe direction to be wrong in.
    """
    v = re.sub(r"[^a-zA-Z' -]", "", (verb or "")).strip().lower()
    if not v or v.endswith("ing"):
        return v
    if v.endswith("ie"):
        return v[:-2] + "ying"            # die -> dying
    if v.endswith("ee") or v.endswith("oe") or v.endswith("ye"):
        return v + "ing"                  # see -> seeing
    if v.endswith("e"):
        return v[:-1] + "ing"             # dance -> dancing
    if (len(v) <= 5 and len(v) >= 3 and v[-1] not in VOWELS + "wxy"
            and v[-2] in VOWELS and v[-3] not in VOWELS):
        return v + v[-1] + "ing"          # run -> running, swim -> swimming
    return v + "ing"


def vn_prompt(verb, noun, rng=None):
    """The winning verb and the winning noun, dropped into a randomly described scene."""
    rng = rng or random
    base = rng.sample(SCENE_WORDS, 2)
    noun = re.sub(r"\s+", " ", (noun or "thing")).strip()[:30] or "thing"
    for article in ("a ", "an ", "the "):
        if noun.lower().startswith(article):
            noun = noun[len(article):]
            break
    subject = f"a {noun} {to_ing(verb)}".strip()
    return (f"One picture of {subject}, in a {base[0]}, {base[1]} setting. The {noun} fills the frame and what it "
            f"is doing is unmistakable. Rich detail, cartoon illustration, thin black outlines, flat bright "
            f"colours, no text, no watermark, no border."), subject, base


PANELS = 4
PANEL_SIZE = 1024
# Never say "comic strip" or "panel" to the image model: it then draws a whole grid of tiny
# panels inside the one picture (seen live 2026-09-18). Each picture is one single scene.
COMIC_STYLE = (". One single scene filling the whole picture, no grid, no sub-pictures, no split frames. Rich "
               "detail, cartoon comic-book illustration, thin black outlines, flat bright colours, the characters "
               "drawn exactly as described, no speech bubbles, no text, no watermark, no border.")


HERO_LOOK = ("a young man with short dark hair, round wire-rimmed glasses, a black t-shirt with a yellow logo "
             "and an olive green leather bomber jacket")
HERO_DRAW = ("The main character is the person in the second picture, drawn as a cartoon character with the same "
             "face, hair, glasses and clothes. ")


def story_prompt(lines, hero=False):
    listed = "; ".join(str(x).strip() for x in lines if str(x).strip())[:1500]
    lead = (f"The main character of the strip is our hero, {HERO_LOOK}; everything the room typed happens to him "
            f"or around him, and he is in every panel. ") if hero else ""
    cast = ("start with our hero, described exactly as above, then " if hero else "")
    return (f"Here is a list of things a room full of people typed: {listed}. {lead}Write a four panel comic strip "
            f"in which every one of these things appears and they interact, with a set-up, a twist and a punchline. "
            f"Output exactly this, on one line, and nothing else: CAST: {cast}a short visual description of each "
            f"character, under 140 characters in total. PANEL 1: what the first panel shows, under 150 "
            f"characters. PANEL 2: the same for the second panel. PANEL 3: the same for the third. PANEL 4: the "
            f"same for the last. Present tense, plain words, no title, no quotes, no line breaks, no characters "
            f"other than letters, digits, spaces, commas, colons and full stops.")


def _extract(g, nid, source, label, until=r"PANEL\s*\d\s*:"):
    """A core RegexExtract node: the text after `label:` up to the next label (`until`) or the end."""
    g[nid] = {"class_type": "RegexExtract", "_meta": {"title": f"pull out {label}"},
              "inputs": {"string": source, "regex_pattern": rf"{label}\s*:\s*(.*?)\s*(?:{until}|$)",
                         "mode": "First Group", "case_insensitive": True, "multiline": False,
                         "dotall": True, "group_index": 1}}
    return [nid, 0]


def build_story(lines, seed=1234, llm_model="gemini-3-1-flash-lite", hero=False, prompt=None):
    """Everyone's lines go to an LLM node INSIDE the graph, which writes a four panel comic strip
    in a fixed format. Four core regex nodes cut it into a cast description and four panel
    descriptions; each panel gets its own prompt and its own sampler pass, all four with the
    same seed and the same cast text so the characters stay recognisable. Each panel's
    description becomes its saved file's name, so the room can read the captions back.
    With `hero`, node 2 is a photo of the main character, handed to every panel's encoder
    as a second reference picture next to the blank canvas, so the same person is drawn
    in all four. One job: LLM call, five text cuts, four pictures. Returns (graph, the LLM prompt).
    """
    g = {}
    g["40"] = {"class_type": "GeminiNode", "_meta": {"title": "the storyteller"},
               "inputs": {"prompt": prompt or story_prompt(lines, hero=hero), "model": llm_model,
                          "seed": seed % 2147483647}}
    extra = None
    if hero:
        g["2"] = {"class_type": "LoadImage", "_meta": {"title": "the main character"}, "inputs": {"image": "hero.png"}}
        extra = {"image2": ["2", 0]}
    cast = _extract(g, "42", ["40", 0], "CAST")
    g["1"] = {"class_type": "EmptyImage", "_meta": {"title": "canvas"},
              "inputs": {"width": PANEL_SIZE, "height": PANEL_SIZE, "batch_size": 1, "color": 8421504}}
    g["3"] = {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}}
    g["4"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}}
    g["5"] = {"class_type": "VAELoader", "inputs": {"vae_name": VAE}}
    g["6"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["3", 0], "shift": 3.1}}
    g["7"] = {"class_type": "CFGNorm", "inputs": {"model": ["6", 0], "strength": 1.0}}
    g["8"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["7", 0], "lora_name": LORA,
              "strength_model": 1.0}}
    for n in range(1, PANELS + 1):
        b = n * 100
        panel = _extract(g, str(b), ["40", 0], f"PANEL {n}")
        g[str(b + 1)] = {"class_type": "StringConcatenate", "_meta": {"title": f"panel {n}: cast"},
                         "inputs": {"string_a": "One single scene. " + (HERO_DRAW if hero else "")
                                                + "The characters: ",
                                    "string_b": cast, "delimiter": ""}}
        g[str(b + 2)] = {"class_type": "StringConcatenate", "_meta": {"title": f"panel {n}: what happens"},
                         "inputs": {"string_a": [str(b + 1), 0], "string_b": panel,
                                    "delimiter": ". What happens in this scene: "}}
        g[str(b + 3)] = {"class_type": "StringConcatenate", "_meta": {"title": f"panel {n}: style"},
                         "inputs": {"string_a": [str(b + 2), 0], "string_b": COMIC_STYLE, "delimiter": ""}}
        out = _chain(g, "1", [str(b + 3), 0], MASH_NEG, seed % 0xFFFFFFFF,
                     tuple(str(b + i) for i in range(4, 12)), extra=extra)
        g[str(30 + n)] = {"class_type": "SaveImage", "_meta": {"title": f"panel {n}, named after its caption"},
                          "inputs": {"images": [out, 0], "filename_prefix": panel}}
    return g, g["40"]["inputs"]["prompt"]


def build_vn(verb, noun, seed=1234):
    """The verb-and-noun round: one subject, one picture, one sampler pass.

    Same blank-canvas-to-edit-model trick as build_mash, so it costs the same 11 s. The only
    difference is what the prompt says, which is the whole point: the room chose it.
    Returns (graph, prompt, subject, the two scene words).
    """
    rng = random.Random(seed)
    prompt, subject, base = vn_prompt(verb, noun, rng)
    g, out = _blank_canvas_chain(prompt, MASH_NEG, seed)
    g["31"] = {"class_type": "SaveImage", "_meta": {"title": "the picture"},
               "inputs": {"images": [out, 0], "filename_prefix": "vn"}}
    return g, prompt, subject, base


def _blank_canvas_chain(prompt, negative, seed, size=1328):
    """Loaders plus one generation pass from a blank canvas. Shared by the two picture modes."""
    g = {}
    g["1"] = {"class_type": "EmptyImage", "_meta": {"title": "canvas"},
              "inputs": {"width": size, "height": size, "batch_size": 1, "color": 8421504}}
    g["3"] = {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}}
    g["4"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}}
    g["5"] = {"class_type": "VAELoader", "inputs": {"vae_name": VAE}}
    g["6"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["3", 0], "shift": 3.1}}
    g["7"] = {"class_type": "CFGNorm", "inputs": {"model": ["6", 0], "strength": 1.0}}
    g["8"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["7", 0], "lora_name": LORA,
              "strength_model": 1.0}}
    out = _chain(g, "1", prompt, negative, seed % 0xFFFFFFFF,
                 ("9", "10", "11", "12", "13", "14", "15", "16"))
    return g, out


def build_mash(words, seed=1234):
    """The mash-up round: no hiding, no answer, one picture out of everything submitted.

    Same trick as the scene in Find It - a blank canvas handed to an edit model, which draws
    a whole new picture - so it needs no extra model and only one sampler pass.
    Returns (graph, prompt, the three words that described the scene).
    """
    rng = random.Random(seed)
    prompt, base = mash_prompt(words, rng)
    g, out = _blank_canvas_chain(prompt, MASH_NEG, seed)
    g["31"] = {"class_type": "SaveImage", "_meta": {"title": "the picture"},
               "inputs": {"images": [out, 0], "filename_prefix": "mash"}}
    return g, prompt, base


TEXTURE_NEG = ("seam, visible join, border, frame, edge, vignette, text, watermark, signature, "
               "single object, centred subject, perspective, horizon, shadow of the camera")


def texture_prompt(thing):
    thing = _bare(thing) or "mossy cobblestone"
    return (f"A flat top-down photograph of {thing}, filling the entire frame edge to edge, evenly lit with no "
            f"shadows and no highlights, the same scale of detail everywhere, no border, no single focal point, "
            f"nothing centred, no text.")


def build_texture(thing, seed=1234, size=1024, steps=4, max_passes=4, target=1.25,
                  band=0.16, repair_denoise=0.60):
    """A texture that tiles, checked by the node rather than by the person looking at it.

    Everything up to node 16 is the same blank-canvas trick the other graphs use. Node 20 is
    the private one: it does its own sampling in a loop, measuring the wrap-around seam after
    each pass and repairing it again if the number is still too high. The graph cannot know
    how many passes that will be, which is the whole point.
    """
    g = {}
    g["1"] = {"class_type": "EmptyImage", "_meta": {"title": "canvas"},
              "inputs": {"width": size, "height": size, "batch_size": 1, "color": 8421504}}
    g["3"] = {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}}
    g["4"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}}
    g["5"] = {"class_type": "VAELoader", "inputs": {"vae_name": VAE}}
    g["6"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["3", 0], "shift": 3.1}}
    g["7"] = {"class_type": "CFGNorm", "inputs": {"model": ["6", 0], "strength": 1.0}}
    g["8"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["7", 0], "lora_name": LORA,
              "strength_model": 1.0}}
    g["9"] = {"class_type": "FluxKontextImageScale", "inputs": {"image": ["1", 0]}}
    g["10"] = {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "positive"},
               "inputs": {"clip": ["4", 0], "prompt": texture_prompt(thing), "vae": ["5", 0],
                          "image1": ["9", 0]}}
    g["11"] = {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "negative"},
               "inputs": {"clip": ["4", 0], "prompt": TEXTURE_NEG, "vae": ["5", 0], "image1": ["9", 0]}}
    g["12"] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["10", 0], "reference_latents_method": "index_timestep_zero"}}
    g["13"] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["11", 0], "reference_latents_method": "index_timestep_zero"}}
    g["14"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["9", 0], "vae": ["5", 0]}}
    g["20"] = {"class_type": "Seamless", "_meta": {"title": "Seamless (private)"},
               "inputs": {"model": ["8", 0], "positive": ["12", 0], "negative": ["13", 0],
                          "vae": ["5", 0], "latent": ["14", 0], "seed": seed % 0xFFFFFFFF,
                          "steps": steps, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple",
                          "max_passes": max_passes, "target": target, "band": band,
                          "feather": 0.02, "repair_denoise": repair_denoise}}
    # The pass count and the before/after numbers ride out on the file's NAME, the same way
    # Find It's answer does.
    g["31"] = {"class_type": "SaveImage", "_meta": {"title": "the texture"},
               "inputs": {"images": ["20", 0], "filename_prefix": ["20", 2]}}
    g["32"] = {"class_type": "SaveImage", "_meta": {"title": "tiled 3x3 proof"},
               "inputs": {"images": ["20", 1], "filename_prefix": "tiled"}}
    return g


def build(things, scene=0, difficulty=3, seed=1234, photos=None):
    """One job that hides EVERY thing the room submitted in one picture.

    Each thing is drawn on its own, then hidden by its own HideIt node whose scene is the
    output of the one before it, so the hides stack up in a single pass over the GPU. Each
    HideIt's key becomes the filename_prefix of that thing's own thumbnail, which is how N
    answers ride out of one job without a node that knows how to concatenate them.

    Returns (graph, thumb_node_ids) - thumb ids in the same order as `things`.
    """
    things = list(things)[:MAX_THINGS] or ["a red soda can"]
    photos = list(photos or [])
    rng = random.Random(seed)
    g = {}
    g["1"] = {"class_type": "EmptyImage", "_meta": {"title": "scene canvas"},
              "inputs": {"width": 1328, "height": 1328, "batch_size": 1, "color": 8421504}}
    g["3"] = {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}}
    g["4"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}}
    g["5"] = {"class_type": "VAELoader", "inputs": {"vae_name": VAE}}
    g["6"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["3", 0], "shift": 3.1}}
    g["7"] = {"class_type": "CFGNorm", "inputs": {"model": ["6", 0], "strength": 1.0}}
    g["8"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["7", 0], "lora_name": LORA,
              "strength_model": 1.0}}
    scene_out = _chain(g, "1", scene_prompt(scene), SCENE_NEG, seed % 0xFFFFFFFF,
                       ("9", "10", "11", "12", "13", "14", "15", "16"))

    stage, mask_out, thumbs, products = scene_out, None, [], []
    for i, thing in enumerate(things):
        b = 100 + i * 20
        ids = tuple(str(b + k) for k in range(1, 9))
        if i < len(photos) and photos[i]:
            g[str(b)] = {"class_type": "LoadImage", "_meta": {"title": f"photo {i}"},
                         "inputs": {"image": photos[i]}}
            product = str(b)
        else:
            g[str(b)] = {"class_type": "EmptyImage", "_meta": {"title": f"canvas {i}"},
                         "inputs": {"width": 768, "height": 768, "batch_size": 1, "color": 16777215}}
            product = _chain(g, str(b), product_prompt(thing), PRODUCT_NEG,
                             (seed + 4242 + i * 7919) % 0xFFFFFFFF, ids)
        products.append(product)
        hide = str(b + 9)
        g[hide] = {"class_type": "HideIt", "_meta": {"title": f"Hide It {i} (private)"},
                   "inputs": {"scene": [stage, 0], "product": [product, 0],
                              "difficulty": int(difficulty), "seed": rng.randrange(1, 2 ** 31),
                              "keep_alpha": False, "patch_margin": 0.45}}
        stage = hide
        if mask_out is None:
            mask_out = [hide, 1]
        else:
            comp = str(b + 10)
            g[comp] = {"class_type": "MaskComposite",
                       "inputs": {"destination": mask_out, "source": [hide, 1], "x": 0, "y": 0,
                                  "operation": "add"}}
            mask_out = [comp, 0]
        # Each thumbnail is saved under its own hide's key, so one job returns N answers.
        thumb = str(b + 11)
        g[thumb] = {"class_type": "SaveImage", "_meta": {"title": f"thing {i}"},
                    "inputs": {"images": [product, 0], "filename_prefix": [hide, 2]}}
        thumbs.append(thumb)

    g["46"] = {"class_type": "FeatherMask", "inputs": {"mask": mask_out, "left": 6, "top": 6,
               "right": 6, "bottom": 6}}
    enc = {"clip": ["4", 0], "prompt": refine_prompt_many(things), "vae": ["5", 0], "image1": [stage, 0],
           "image2": [products[0], 0]}
    if len(products) > 1:
        enc["image3"] = [products[1], 0]
    g["47"] = {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "redraw the patches"}, "inputs": enc}
    g["48"] = {"class_type": "TextEncodeQwenImageEditPlus", "_meta": {"title": "redraw negative"},
               "inputs": {"clip": ["4", 0], "prompt": REFINE_NEG, "vae": ["5", 0], "image1": [stage, 0]}}
    g["49"] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["47", 0], "reference_latents_method": "index_timestep_zero"}}
    g["50"] = {"class_type": "FluxKontextMultiReferenceLatentMethod",
               "inputs": {"conditioning": ["48", 0], "reference_latents_method": "index_timestep_zero"}}
    g["51"] = {"class_type": "VAEEncode", "inputs": {"pixels": [stage, 0], "vae": ["5", 0]}}
    g["52"] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": ["51", 0], "mask": ["46", 0]}}
    g["53"] = {"class_type": "KSampler", "inputs": {"model": ["8", 0], "positive": ["49", 0],
               "negative": ["50", 0], "latent_image": ["52", 0], "seed": (seed + 777) % 0xFFFFFFFF,
               "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 0.45}}
    g["54"] = {"class_type": "VAEDecode", "inputs": {"samples": ["53", 0], "vae": ["5", 0]}}
    g["31"] = {"class_type": "SaveImage", "_meta": {"title": "the puzzle"},
               "inputs": {"images": ["54", 0], "filename_prefix": "puzzle"}}
    return g, thumbs


# ---- Kill or Save: two secret teams' words, one four-panel comic, Yoland dies in panel 4 ----
HERO_NAME = "Yoland"


def kill_prompt(kill_words, save_words):
    """The smash. The LLM knows which words were Kill and which were Save; the room does not."""
    kill_words = ", ".join(kill_words)[:700] or "nothing"
    save_words = ", ".join(save_words)[:700] or "nothing"
    return (f"A room full of people is playing a game about {HERO_NAME}, {HERO_LOOK}. Two secret teams typed single "
            f"words. The KILL team wants {HERO_NAME} gone; their words: {kill_words}. The SAVE team wants him to "
            f"live; their words: {save_words}. The teams are invisible: they are never characters, never drawn, "
            f"never mentioned in the panels. Write a four panel comic strip that smashes as many of these words "
            f"as possible into one absurd, funny story; fun over logic; every word you use is a real thing or "
            f"action in the scene. Panels 1 to 3: the Kill words come at him and the Save words rescue him each "
            f"time, just barely. Panel 4: the Kill words finally get {HERO_NAME} HIMSELF, comic-book style: his "
            f"own body flattened like a pancake, or squashed, zapped or launched into the sky, his own eyes spinning "
            f"in spirals, no blood, no gore; describe {HERO_NAME}'s body in that state. Output "
            f"exactly this, on one line, and nothing else: CAST: start with {HERO_NAME}, {HERO_LOOK}, then a short "
            f"visual description of every other character, under 160 characters in total. PANEL 1: what the first "
            f"panel shows, under 150 characters, {HERO_NAME} in it. PANEL 2: the same for the second panel. PANEL "
            f"3: the same for the third. PANEL 4: the same for the last. Present tense, plain words, no title, no "
            f"quotes, no line breaks, no characters other than letters, digits, spaces, commas, colons and full stops.")


def build_kill(kill_words, save_words, seed=1234, hero=True):
    """One round of Kill or Save as one job: the smash, five regex cuts, four pictures with the
    hero's photo as the reference, each caption riding out as its file's name."""
    return build_story([], seed=seed, hero=hero, prompt=kill_prompt(kill_words, save_words))


# ---- Kill or Save, the team's prompts: one comic page per round, or four panels ---------------

def build_page(page_prompt, seed=42, aspect="1:1", previous_page=False, pro=False):
    """The artifact's render step as one job: a Nano Banana API node draws the whole comic page
    (panels, gutters, lettering) from the Smash's page_prompt, with the hero's photo as the
    first reference image and, from round 2, the previous page as the second. Both references
    are scaled to the page's shape and batched, because the node takes one IMAGE input."""
    w, h = (768, 1024) if aspect == "3:4" else (1024, 1024)   # references are scaled to the page shape
    g = {}
    g["2"] = {"class_type": "LoadImage", "_meta": {"title": "the hero's photo"}, "inputs": {"image": "hero.png"}}
    g["3"] = {"class_type": "ImageScale", "_meta": {"title": "hero, page-shaped"},
              "inputs": {"image": ["2", 0], "upscale_method": "lanczos", "width": w, "height": h, "crop": "center"}}
    refs = ["3", 0]
    if previous_page:
        g["4"] = {"class_type": "LoadImage", "_meta": {"title": "the previous page"}, "inputs": {"image": "prev.png"}}
        g["5"] = {"class_type": "ImageScale", "_meta": {"title": "previous page, page-shaped"},
                  "inputs": {"image": ["4", 0], "upscale_method": "lanczos", "width": w, "height": h,
                             "crop": "center"}}
        g["6"] = {"class_type": "ImageBatch", "_meta": {"title": "hero + previous page"},
                  "inputs": {"image1": ["3", 0], "image2": ["5", 0]}}
        refs = ["6", 0]
    if pro:
        g["20"] = {"class_type": "GeminiImage2Node", "_meta": {"title": "draw the page (Nano Banana Pro)"},
                   "inputs": {"prompt": page_prompt, "model": "gemini-3-pro-image-preview", "seed": seed % 2147483647,
                              "aspect_ratio": aspect, "resolution": "1K", "response_modalities": "IMAGE",
                              "images": refs}}
    else:
        g["20"] = {"class_type": "GeminiImageNode", "_meta": {"title": "draw the page (Nano Banana)"},
                   "inputs": {"prompt": page_prompt, "model": "gemini-2.5-flash-image", "seed": seed % 2147483647,
                              "aspect_ratio": aspect, "response_modalities": "IMAGE", "images": refs}}
    g["31"] = {"class_type": "SaveImage", "_meta": {"title": "the page"},
               "inputs": {"images": ["20", 0], "filename_prefix": "page"}}
    return g


SHEET_PROMPT = ("A character model sheet of one person, standing, full body, front view, relaxed neutral pose, "
                "arms at the sides, looking at the viewer, on a plain flat white background. The person is exactly "
                "the person in the second picture, drawn as a cartoon character with the same face, hair, glasses "
                "and clothes: {look}. Rich detail, cartoon comic-book illustration, thin black outlines, flat bright "
                "colours, no text, no watermark, no border.")
SHEET_DRAW = ("The main character is the cartoon character in the second picture, drawn EXACTLY like it: the same "
              "face, the same hair, the same round glasses, the same jacket and shirt, the same body proportions and "
              "the same drawing style; the third picture is the photo that character was drawn from. ")


def build_panels(prompts, seed=1234, hero=True, look="", sheet=True):
    """The panel render: each panel plan drawn as its own picture on the GPU. With `sheet`, the
    job first draws one character sheet of the hero from the photo (a fixed cartoon design), and
    every panel then copies that sheet (second reference) with the photo as the third, so the
    hero is the same drawing in all four instead of being re-invented from the photo each
    time. `prompts` are the finished per-panel prompts without the hero clause; it is added here."""
    g = {}
    g["1"] = {"class_type": "EmptyImage", "_meta": {"title": "canvas"},
              "inputs": {"width": PANEL_SIZE, "height": PANEL_SIZE, "batch_size": 1, "color": 8421504}}
    g["3"] = {"class_type": "UNETLoader", "inputs": {"unet_name": UNET, "weight_dtype": "default"}}
    g["4"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": CLIP, "type": "qwen_image", "device": "default"}}
    g["5"] = {"class_type": "VAELoader", "inputs": {"vae_name": VAE}}
    g["6"] = {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["3", 0], "shift": 3.1}}
    g["7"] = {"class_type": "CFGNorm", "inputs": {"model": ["6", 0], "strength": 1.0}}
    g["8"] = {"class_type": "LoraLoaderModelOnly", "inputs": {"model": ["7", 0], "lora_name": LORA,
              "strength_model": 1.0}}
    neg = MASH_NEG + ", blood, gore, grid, split frame"
    extra, who = None, ""
    if hero:
        g["2"] = {"class_type": "LoadImage", "_meta": {"title": "the hero's photo"}, "inputs": {"image": "hero.png"}}
        if sheet:
            dec = _chain(g, "1", SHEET_PROMPT.format(look=look or HERO_LOOK), neg + ", scene, background objects",
                         seed % 0xFFFFFFFF, ("40", "41", "42", "43", "44", "45", "46", "47"),
                         extra={"image2": ["2", 0]})
            g["30"] = {"class_type": "SaveImage", "_meta": {"title": "the character sheet"},
                       "inputs": {"images": [dec, 0], "filename_prefix": "sheet"}}
            extra, who = {"image2": [dec, 0], "image3": ["2", 0]}, SHEET_DRAW
        else:
            extra, who = {"image2": ["2", 0]}, HERO_DRAW
    for n, prompt in enumerate(prompts, 1):
        b = n * 100
        out = _chain(g, "1", "One single scene. " + who + prompt, neg, seed % 0xFFFFFFFF,
                     tuple(str(b + i) for i in range(4, 12)), extra=extra)
        g[str(30 + n)] = {"class_type": "SaveImage", "_meta": {"title": f"panel {n}"},
                          "inputs": {"images": [out, 0], "filename_prefix": f"panel{n}"}}
    return g


if __name__ == "__main__":
    json.dump(graph(upload=False), open("workflow_find.json", "w"), indent=2)
    json.dump(graph(upload=True), open("workflow_find_upload.json", "w"), indent=2)
    many, thumbs = build(["a rubber duck", "a taco", "a traffic cone"], scene=2)
    json.dump(many, open("workflow_find_many.json", "w"), indent=2)
    print("wrote workflow_find.json workflow_find_upload.json workflow_find_many.json;",
          len(SCENES), "scenes; thumbs", thumbs)
