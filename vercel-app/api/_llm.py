"""The text LLM, reached through Comfy's Router on its v2 model surface:

    POST /v2/models/anthropic/<model>      the Anthropic Messages body, model named by the path

This is the same catalogue the picture is drawn from (docs.comfy.org/development/comfy-router/
models), so every call the game makes goes through one Router surface and one production key,
rather than the older /proxy/anthropic/v1/messages passthrough (Vinh, 2026-09-18).

The team's two prompts (build/team_prompts.txt, from Team 6's artifact) live in
prompts/setup.txt and prompts/smash.txt, plus a PANEL FLOW section added to the Smash on
2026-09-18 so the four panels read as one continuous moment. The "THIS GAME" / "THIS ROUND"
tails they end with are rendered here.
"""
import base64, json, os, re, urllib.request

ROUTER = "https://api.comfy.org/v2/models/anthropic"        # + "/<model>"
LEGACY_ROUTER = "https://api.comfy.org/proxy/anthropic/v1/messages"
MODEL_FAST = "claude-haiku-4-5-20251001"
MODEL_FUNNY = "claude-sonnet-5"
HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_ASPECT = "wide landscape, 16:9"
STYLE = ("Bright, clean, modern cartoon comic-book art: bold black ink outlines, flat saturated colours, "
         "simple shading, expressive faces, clear readable compositions.")


def partner_key():
    return os.environ.get("COMFY_PARTNER_API_KEY", "").strip() or None


def prompt_file(name):
    return open(os.path.join(HERE, "prompts", name), encoding="utf-8").read()


def claude(system, user_text, image_bytes=None, image_type="image/png", model=MODEL_FUNNY, max_tokens=4000,
           timeout=90, _retried=False):
    """One Messages call. Returns the reply text. Falls back to the fast model when the
    Router does not know the requested one, and asks again when a reply comes back with no text
    at all (seen live 2026-09-18: the whole budget went on a thinking block and the round died)."""
    key = partner_key()
    if not key:
        raise RuntimeError("server is missing COMFY_PARTNER_API_KEY (the Router needs a production key)")
    content = []
    if image_bytes:
        content.append({"type": "image", "source": {"type": "base64", "media_type": image_type,
                                                    "data": base64.b64encode(image_bytes).decode()}})
    content.append({"type": "text", "text": user_text})
    # v2 names the model in the path; the body is the Messages body otherwise unchanged.
    # The system prompt is the same 5k tokens every round, so it is marked cacheable: after the
    # first call of a game the model reads it instead of re-reading it. Thinking is turned off
    # because it is pure wall-clock here: the room waits while it happens and never sees it, and
    # a round died on 2026-09-18 when it ate the whole token budget before a word of JSON.
    body = {"max_tokens": max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "thinking": {"type": "disabled"},
            "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(f"{ROUTER}/{model}", data=json.dumps(body).encode(), method="POST", headers={
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
        kinds = ",".join(sorted({p.get("type", "?") for p in parts})) or "none"
        why = f"stop_reason={out.get('stop_reason')} blocks={kinds}"
        if not _retried:
            return claude(system, user_text, image_bytes, image_type, model, max_tokens, timeout, True)
        raise RuntimeError(f"router returned no text ({why})")
    return text


def _repair(t):
    """A long JSON reply comes back malformed now and then: a real newline left inside a string,
    a trailing comma, curly quotes. Measured 2026-09-18: three of four Smash calls failed to
    parse, and every one of them was one of these. Walk the text and fix them rather than lose
    the round."""
    out, in_str, esc = [], False, False
    for ch in t:
        if esc:
            out.append(ch)
            esc = False
            continue
        if ch == "\\":
            out.append(ch)
            esc = in_str          # a backslash only escapes inside a string
            continue
        if ch == '"':
            in_str = not in_str
            out.append(ch)
            continue
        if in_str and ch in "\n\r\t":
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch])
            continue
        out.append(ch)
    t = "".join(out)
    if in_str:                     # the reply stopped mid-string: close it and the document
        t += '"'
    t = re.sub(r",(\s*[}\]])", r"\1", t)        # trailing commas
    opens = t.count("{") - t.count("}")
    if opens > 0:
        t += "}" * opens
    return t


def parse_json(text):
    """The prompts ask for bare JSON; strip fences or stray prose if the model adds them, then
    repair the reply rather than throw away a round over a stray newline."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    a, b = t.find("{"), t.rfind("}")
    for candidate in (t, t[a:b + 1] if (a >= 0 and b > a) else None, _repair(t),
                      _repair(t[a:]) if a >= 0 else None):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("the writer's reply is not JSON, even after repair: " + t[:300])


# ---- the one call per round ----------------------------------------------------------------

HERO_RULES = ("- The first image attached to this message is a photo of the hero. Add to your JSON a field "
              "\"hero_description\": 15-30 words, only what is visible and useful for drawing them consistently "
              "(hair colour and style, facial hair, glasses, clothing and colours, notable accessories). Neutral "
              "and kind: never comment on body, weight, age, attractiveness, ethnicity or anything personal. Use "
              "that description verbatim in the page prompt.")


def smash(hero_name, round_number, total_rounds, verdict, hero_description, current_scene, previous_rounds,
          continuity, kill_ideas, save_ideas, previous_page=False, panels_exactly=4,
          photo=None, photo_type="image/png", model=None):
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
        lines += ["", "THIS GAME'S RENDER. It REPLACES the JSON shape above: send these fields and no others.",
                  "{\"title\", \"story\", \"outcome\", \"panels\":[{\"text\"}], \"page_prompt\", "
                  "\"continuity\", \"next_scene\"}",
                  "- No \"visual\", no \"caption\", no \"bubbles\", no \"page_position\", no \"layout\", no "
                  "\"sfx\", no \"camera\". Nothing reads them, and every one of them is time the room spends "
                  "waiting for a field nobody will ever see. A panel object has exactly one key: \"text\".",
                  f"- EXACTLY {panels_exactly} panels this round, whatever the round number: fold the beats into "
                  f"{panels_exactly} moments, the last one the OUTCOME.",
                  f"- Every panel's \"text\" is {hero_name.upper()} NARRATING THAT PANEL HIMSELF, out loud, in "
                  f"FIRST PERSON and present tense: \"I stroll into the market, and that is when the anvil finds "
                  f"me.\" One or two sentences, max 30 words, said the way a person talks, because it is read "
                  f"aloud in his voice. Never write \"{hero_name}\" in the third person there, never say "
                  f"\"Panel 1\", no emoji, no asterisks, no stage directions in brackets. He can react, complain "
                  f"and joke about what is happening to him. The four \"text\" fields read in order must tell the "
                  f"whole story on their own.",
                  "- \"story\" and \"outcome\" stay in the third person, for the record; only \"text\" is his "
                  "voice.",
                  "- \"page_prompt\" is the ONLY thing the image model sees, so everything visual lives there and "
                  "nowhere else. Open it with EXACTLY this count, in these words: \"A comic page with exactly 4 "
                  "panels and no others.\" Then the layout in one sentence, manga style on a WIDE LANDSCAPE page: "
                  "four panels of different sizes and shapes, not a plain grid, a tall narrow panel down one side, "
                  "a wide letterbox strip, a slanted edge or two so a gutter cuts the page on a diagonal, the "
                  "OUTCOME panel clearly the biggest, reading left to right, never overlapping. Then exactly four "
                  "sentences, each starting \"Panel 1:\", \"Panel 2:\", \"Panel 3:\", \"Panel 4:\", saying what "
                  "is in that frame and the camera, matching the camera to the shape: a tall panel wants a falling "
                  "or full-body figure, a wide one a horizontal sweep, the big one the impact. The four panels are "
                  "ONE continuous moment: same place, same time, same screen direction.",
                  "- NEVER add a fifth frame. No establishing shot, no scene-setting strip, no wide empty view of "
                  "the location, no inset, no title banner, no repeated panel. Panel 1 already establishes the "
                  f"place WITH {hero_name} in it, so a panel showing the location on its own is one frame too "
                  "many. Four sentences, four panels, and the hero is visible in every one of them.",
                  "- NO lettering anywhere on the page: the page_prompt must not mention captions, speech bubbles, "
                  "sound effects, signs or labels, and it closes with \"No text, letters, numbers, speech bubbles, "
                  "caption boxes or sound effects anywhere on the page.\" before the art style.",
                  "", "LENGTH (this decides how long the room waits, so it is not negotiable):",
                  "- The size of the pot NEVER changes the size of the page. Thirty answers and three answers "
                  "both produce exactly four panels, the same length of narration and the same page prompt. Extra "
                  "answers get folded into the same four beats, several to a panel, or left out; they never buy "
                  "more panels, more sentences or a longer prompt.",
                  "- Use as many of the answers as the story can carry. Crowding four or five of them into one "
                  "panel is good; stretching the page to fit them all is not.",
                  "- Work in as many of the answers as you can, aiming for at least two thirds of each pool, "
                  "by crowding several into one panel rather than by adding panels or sentences.",
                  "- Hard sizes, counted: \"story\" max 45 words. \"outcome\" max 25 words. Each panel's "
                  "\"text\" max 30 words.",
                  "- \"page_prompt\" is the one field that must stay complete: it is the only thing the "
                  "image model ever sees, so it always describes the layout once and then all four panels, "
                  "and it always ends with the art style. Aim for about 250 words by giving each panel one "
                  "tight sentence, never by leaving a panel out or by shortening it to a stub.",
                  "- No commentary, no explanation, no notes about what you used or skipped: the JSON only."]
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
    def check(got):
        """A round is only usable if the writer sent the four panels AND a real page prompt.
        Measured 2026-09-18: pushed on length, it sometimes returns page_prompt as a 14-character
        stub, which draws a garbage page rather than failing loudly."""
        if len(got.get("panels") or []) < panels_exactly:
            raise ValueError(f"only {len(got.get('panels') or [])} panels came back")
        if len(str(got.get("page_prompt") or "").strip()) < 200:
            raise ValueError("page_prompt came back as a stub: " + repr(got.get("page_prompt"))[:120])
        return got

    system = prompt_file("smash.txt") + "\n" + "\n".join(lines)
    model = model or MODEL_FUNNY
    text = claude(system, "Go.", photo, photo_type, model=model, max_tokens=12000, timeout=180)
    try:
        got = check(parse_json(text))
    except ValueError:
        # One more go, saying what went wrong. A round is worth 40 seconds; losing it is not.
        text = claude(system, "Your last reply was unusable. Send the whole page again as ONE valid JSON "
                              "object: no prose, no markdown fence, no real line breaks inside strings, all "
                              f"{panels_exactly} panels, and \"page_prompt\" written out in full.",
                      photo, photo_type, model=model, max_tokens=12000, timeout=180)
        got = check(parse_json(text))
    got["raw"] = text
    return got
