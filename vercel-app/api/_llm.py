"""The text LLM, reached through Comfy's Router: api.comfy.org proxies Anthropic's Messages API
at /proxy/anthropic/v1/messages and bills the production Comfy key. The same route the
ClaudeNode inside ComfyUI uses; here it is called from the game engine, so the reply is a
JSON document instead of a file name.

The team's two prompts (build/team_prompts.txt, from Team 6's artifact) live verbatim in
prompts/setup.txt and prompts/smash.txt; the "THIS GAME" / "THIS ROUND" tails they end with
are rendered here.
"""
import base64, json, os, re, urllib.request

ROUTER = "https://api.comfy.org/proxy/anthropic/v1/messages"
MODEL_FAST = "claude-haiku-4-5-20251001"
MODEL_FUNNY = "claude-sonnet-5"
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_ASPECT = "portrait, 3:4"
STYLE = ("Bright, clean, modern cartoon comic-book art: bold black ink outlines, flat saturated colours, "
         "simple shading, expressive faces, clear readable compositions.")


def partner_key():
    return os.environ.get("COMFY_PARTNER_API_KEY", "").strip() or None


def prompt_file(name):
    return open(os.path.join(HERE, "prompts", name), encoding="utf-8").read()


def claude(system, user_text, image_bytes=None, image_type="image/png", model=MODEL_FUNNY, max_tokens=4000,
           timeout=90):
    """One Messages call. Returns the reply text. Falls back to the fast model when the
    Router does not know the requested one."""
    key = partner_key()
    if not key:
        raise RuntimeError("server is missing COMFY_PARTNER_API_KEY (the Router needs a production key)")
    content = []
    if image_bytes:
        content.append({"type": "image", "source": {"type": "base64", "media_type": image_type,
                                                    "data": base64.b64encode(image_bytes).decode()}})
    content.append({"type": "text", "text": user_text})
    body = {"model": model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(ROUTER, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "X-API-KEY": key, "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = e.read().decode(errors="replace")[:300]
        if model != MODEL_FAST and e.code in (400, 404) and "model" in msg.lower():
            return claude(system, user_text, image_bytes, image_type, MODEL_FAST, max_tokens, timeout)
        raise RuntimeError(f"router {e.code}: {msg}")
    parts = out.get("content") or []
    text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    if not text:
        raise RuntimeError(f"router returned no text: {json.dumps(out)[:200]}")
    return text


def parse_json(text):
    """The prompts ask for bare JSON; strip fences or stray prose if the model adds them."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a >= 0 and b > a:
            return json.loads(t[a:b + 1])
        raise


# ---- the two calls ----------------------------------------------------------------------

def setup(hero_name, total_rounds, photo_bytes, photo_type="image/png"):
    """Prompt 1, once per game: reads the photo, writes the hero description and round 1's scene."""
    tail = (f"THIS GAME\n- Total rounds: {total_rounds}\n- Hero name: {hero_name}\n"
            f"- Hero photo: attached as an image.")
    text = claude(prompt_file("setup.txt") + "\n" + tail, "Go.", photo_bytes, photo_type, max_tokens=600)
    got = parse_json(text)
    return {"hero_description": str(got.get("hero_description", "")).strip(),
            "scene": str(got.get("scene", "")).strip(), "raw": text}


def smash(hero_name, round_number, total_rounds, verdict, hero_description, current_scene, previous_rounds,
          continuity, kill_nouns, kill_verbs, save_nouns, save_verbs, previous_page=False, panels_exactly=4):
    """Prompt 2, every round: the story, the panel plans, the page prompt, continuity, next scene.
    `panels_exactly` pins the panel count (the pages lay them out 2 x 2) and asks for a
    narration line per panel, printed under the picture instead of drawn into it."""
    final = round_number >= total_rounds
    join = lambda xs: ", ".join(xs) if xs else "(none)"
    lines = [f"THIS ROUND",
             f"- Round: {round_number} of {total_rounds}" + (" (FINAL ROUND: set next_scene to null)" if final else ""),
             f"- Verdict: the hero {verdict}",
             f"- Hero: {hero_name}",
             f"- Hero description (use verbatim in the page prompt): {hero_description}",
             f"- Page aspect ratio: {PAGE_ASPECT}",
             f"- Art style (append verbatim at the end of the page prompt): {STYLE}",
             f"- Previous page as second reference image: {'yes' if previous_page else 'no'}",
             f"- Scene: {current_scene}", "", "STORY SO FAR:"]
    if previous_rounds:
        lines += [f"- Round {p['round']} ({p['verdict']}): {p['outcome']}" for p in previous_rounds]
    else:
        lines.append("This is the first round.")
    lines += ["", "CONTINUITY (still true at the start of this round):"]
    if continuity:
        lines += [f"- Hero's look: {continuity.get('hero_look') or '(none)'}",
                  f"- Hero is carrying: {continuity.get('hero_carrying') or '(none)'}",
                  f"- Still around: {continuity.get('world') or '(none)'}"]
    else:
        lines.append("None yet. This is the first round.")
    lines += ["", "KILL POOL (teams are secret from players, visible to you):",
              f"- Nouns: {join(kill_nouns)}", f"- Verbs: {join(kill_verbs)}", "",
              "SAVE POOL:", f"- Nouns: {join(save_nouns)}", f"- Verbs: {join(save_verbs)}"]
    if panels_exactly:
        lines += ["", "THIS GAME'S RENDER (overrides PANEL STRUCTURE counts and LETTERING):",
                  f"- Plan EXACTLY {panels_exactly} panels this round, whatever the round number: fold the beats "
                  f"into {panels_exactly} moments, the last one the OUTCOME.",
                  "- Each panel is drawn as its own separate picture with NO lettering at all. Keep \"caption\" and "
                  "\"bubbles\" in the JSON, but add to every panel object a field \"text\": one or two sentences, "
                  "max 35 words, that narrate that panel to the audience like a storybook (what happens and what is "
                  "said, in prose). The four \"text\" fields read in order must tell the whole story.",
                  "- Still write \"page_prompt\"; it may be short."]
    text = claude(prompt_file("smash.txt") + "\n" + "\n".join(lines), "Go.", max_tokens=6000, timeout=120)
    got = parse_json(text)
    got["raw"] = text
    return got
