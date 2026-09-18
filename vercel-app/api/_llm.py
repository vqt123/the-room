"""The text LLM, reached through Comfy's Router: api.comfy.org proxies Anthropic's Messages API
at /proxy/anthropic/v1/messages and bills the production Comfy key. The same route the
ClaudeNode inside ComfyUI uses; here it is called from the game engine, so the reply is a
JSON document instead of a file name.

The team's two prompts (build/team_prompts.txt, from Team 6's artifact) live in
prompts/setup.txt and prompts/smash.txt, plus a PANEL FLOW section added to the Smash on
2026-09-18 so the four panels read as one continuous moment. The "THIS GAME" / "THIS ROUND"
tails they end with are rendered here.
"""
import base64, json, os, re, urllib.request

ROUTER = "https://api.comfy.org/proxy/anthropic/v1/messages"
MODEL_FAST = "claude-haiku-4-5-20251001"
MODEL_FUNNY = "claude-sonnet-5"
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_ASPECT = "square, 1:1"
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


# ---- the one call per round ----------------------------------------------------------------

HERO_RULES = ("- The first image attached to this message is a photo of the hero. Add to your JSON a field "
              "\"hero_description\": 15-30 words, only what is visible and useful for drawing them consistently "
              "(hair colour and style, facial hair, glasses, clothing and colours, notable accessories). Neutral "
              "and kind: never comment on body, weight, age, attractiveness, ethnicity or anything personal. Use "
              "that description verbatim in the page prompt.")


def smash(hero_name, round_number, total_rounds, verdict, hero_description, current_scene, previous_rounds,
          continuity, kill_ideas, save_ideas, previous_page=False, panels_exactly=4,
          photo=None, photo_type="image/png"):
    """The one LLM call per round: the story, the panel plans, the page prompt, the continuity and
    the hook into the next round. There is no separate setup step any more (Vinh, 2026-09-18):
    round 1 carries the hero's photo and invents the setting from the words, and every later
    round picks up where the last one ended. `panels_exactly` pins the panel count and asks for
    a narration line per panel, printed beside the page instead of drawn into it."""
    final = round_number >= total_rounds
    join = lambda xs: ", ".join(xs) if xs else "(none)"
    lines = [f"THIS ROUND",
             f"- Round: {round_number} of {total_rounds}" + (" (FINAL ROUND: set next_scene to null)" if final else ""),
             f"- Verdict: the hero {verdict}",
             f"- Hero: {hero_name}",
             (f"- Hero description (use verbatim in the page prompt): {hero_description}" if hero_description
              else "- Hero description: you are writing it this round, see HERO below."),
             f"- Page aspect ratio: {PAGE_ASPECT}",
             f"- Art style (append verbatim at the end of the page prompt): {STYLE}",
             f"- Previous page as second reference image: {'yes' if previous_page else 'no'}"]
    if current_scene:
        lines.append(f"- Scene: {current_scene}")
    else:
        lines.append("- Scene: none was written for you. Invent the setting yourself, from the words below: pick "
                     "the one place that makes the funniest use of them, and establish it in the first panel.")
    lines += ["", "STORY SO FAR:"]
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
    lines += ["", "HOW THE WORDS WERE COLLECTED (this overrides the NOUN and VERB wording above):",
              f"- Every player answered one question, in their own words. The KILL team was asked \"What kills "
              f"{hero_name}?\" and the SAVE team was asked \"What saves {hero_name}?\". Each pool below is the list "
              f"of those answers. Treat them as things and actions to build the round from, mix and match freely "
              f"within a pool, and keep every rule about which pool does what.",
              "", "KILL POOL, their answers to \"what kills him?\" (teams are secret from players, visible to you):",
              f"- {join(kill_ideas)}", "",
              f"SAVE POOL, their answers to \"what saves him?\":", f"- {join(save_ideas)}"]
    if panels_exactly:
        lines += ["", "THIS GAME'S RENDER (overrides PANEL STRUCTURE counts, LETTERING and THE PAGE PROMPT):",
                  f"- Plan EXACTLY {panels_exactly} panels this round, whatever the round number: fold the beats "
                  f"into {panels_exactly} moments, the last one the OUTCOME.",
                  "- LAYOUT, manga style: the four panels are NOT a plain grid. Vary their size and shape across "
                  "the page: a tall narrow panel down one side, a wide letterbox strip, a couple of slanted edges "
                  "so at least one gutter cuts the page on a diagonal, and the OUTCOME panel clearly the biggest, "
                  "often bleeding to the page edge. They still read left to right, top to bottom, they never "
                  "overlap, and there are still exactly four of them. Write \"layout\" as that arrangement in plain "
                  "words, and every panel's \"page_position\" as its place, size and shape together, e.g. \"tall "
                  "narrow panel down the left third, slanted right edge\" or \"wide panel across the bottom half, "
                  "bleeding off both edges\".",
                  "- Match the camera to the shape: a tall panel wants a full-body or falling figure, a wide one "
                  "wants a landscape or a horizontal action sweep, the big one wants the impact.",
                  "- The whole page is drawn in ONE shot, so PANEL FLOW matters more than anything else: the four "
                  "panels are one continuous moment, same place, same time, same screen direction.",
                  "- NO lettering is drawn on the page. Write \"caption\" and \"bubbles\" as usual in the JSON (the "
                  "audience reads them beside the page), but the \"page_prompt\" must not mention captions, speech "
                  "bubbles, sound effects, signs, labels or any other text: give each panel its visual only, and "
                  "close the page prompt with \"No text, letters, numbers, speech bubbles, caption boxes or sound "
                  "effects anywhere on the page.\" before the art style.",
                  "- Add to every panel object a field \"text\": one or two sentences, max 35 words, narrating that "
                  "panel to the audience like a storybook (what happens, and what anyone says, in prose). The four "
                  "\"text\" fields read in order must tell the whole story on their own."]
    if previous_rounds:
        lines += ["", "THIS IS A CONTINUATION, NOT A NEW STORY:",
                  "- This page picks up moments after the last panel of the previous page, in the same world and "
                  "the same style. The hero is the same drawing, wearing the same clothes with the same damage.",
                  "- Panel 1 opens on the aftermath of how the last round ended and moves him somewhere new by "
                  "walking, falling, floating or being carried there; never cut to an unrelated place.",
                  "- Name the previous round's outcome in panel 1's narration text, so the audience hears the "
                  "story continue, and bring back one character or object from it as a background gag.",
                  "- If the hero died last round, bring him back in the first panel in a quick, silly way, and "
                  "keep a mark of it on him for the rest of the page."]
    if photo is not None:
        lines += ["", "HERO:", HERO_RULES]
    text = claude(prompt_file("smash.txt") + "\n" + "\n".join(lines), "Go.", photo, photo_type,
                  max_tokens=6000, timeout=150)
    got = parse_json(text)
    got["raw"] = text
    return got
