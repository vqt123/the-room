"""Kill or Save, on Team 6's prompts: one hero (Yoland), two secret teams, one comic page per round.

How a game runs (build/team_prompts.txt is the source; this is the engine it describes):
  1. Every player answers one question in one field: team Kill is asked "What kills Yoland?"
     and team Save "What saves Yoland?" (Vinh, 2026-09-18). Everyone sees every answer; nobody
     sees who wrote it or which question it answered. Sides are secret, assigned on join.
     There is no scene to read first: the answers make the setting.
  2. Go (the host): ONE Smash call gets the KILL pool, the SAVE pool, the story so far and a
     fixed verdict, and returns the story, the panel plans, one page_prompt, the continuity
     and the hook into the next round, as JSON. Round 1 also carries the hero's photo and the
     writer names his look there; every later round picks up moments after the last page.
  4. Render, one Comfy job on the Dev Platform endpoint (the point of the hackathon, so this is
     the default and stays the default): a Nano Banana API node draws the whole page from
     page_prompt with the hero's photo (and from round 2 the previous page) as references.
     `fal` draws the same model through the Router in one synchronous call instead (18-19 s,
     measured 2026-09-18); it is not the default, it is what the endpoint falls back to when the
     provider refuses, because a refusal from Vertex is not a refusal from fal. Last resort
     `panels`: the panel plans drawn as four separate pictures on our own GPU.
  5. The next scene goes up at once, so players type for the next round while the page draws.

Verdict rule: every round LIVES except the final round, which DIES; the host can override the
next round's verdict from the screen. total_rounds is per session (default 2).

State is one Redis hash per session, `ks:<sess>`:
  p:<voter>       json {n: name, t: team, ms}
  a:<r>:<voter>   json {idea, ms}              one answer per player per round, editable until Go
  round           the round the pot is filling right now (1-based)
  setup           json {hero_description, scene}
  scene           the current round's scene text
  cont            json continuity carried into the current round
  n:<r>           json the round record: the Smash's JSON plus job, verdict, pools, render, ms,
                  done_ms / error, images
plus `ks:<sess>:hero` (photo URL), `:rounds`, `:render` (page | pro | panels), `:verdict`
(override for the next round), all kept across resets, `:busy` (the stage marker every screen
shows while a round is being made) and two short locks.
"""
import json, os, random, re, secrets, sys, threading, time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import _blob, _fal, _kv, _llm, _voice
from _core import check_password  # noqa: F401
from _findcore import _output, client, poll, story_panels
from _findgraph import COMIC_STYLE, HERO_DRAW, HERO_LOOK, HERO_NAME, build_page, build_panels

TTL_S = 86_400
LOCK_MS = 120_000
STUCK_MS = 240_000
PAGE_MS = 25_000             # a Nano Banana page, for the countdown on screen
PANELS_MS = 55_000
FAL_MS = 20_000              # the same page drawn by fal, measured 18.4 s and 18.8 s on 2026-09-18
WRITE_MS = 45_000            # the Smash call, for the bar on the phones while it runs
VOICE_MS = 12_000            # the four speech calls at the end of a round
BUSY_MS = 300_000            # the stage marker's own expiry, so a crashed Go never sticks
# The image model will happily draw a fifth frame if the page leaves room, so the count is
# restated by us at the end of the prompt rather than trusted to the writer (seen 2026-09-18).
PAGE_TAIL = (" Dynamic manga-style comic page in wide landscape: exactly four panels of different sizes and shapes, "
             "some edges slanted so a gutter cuts across the page on a diagonal, thick black panel borders, white "
             "gutters, the last panel the largest. The panels must not overlap each other. Do not draw a fifth "
             "panel, an inset panel, a repeated panel, or a title banner.")
TEAMS = ("kill", "save")
NAME_RE = re.compile(r"^[a-z0-9]{6,12}$")
WORD_RE = re.compile(r"[^a-z0-9' \-]+")
RENDERS = ("pro", "page", "fal", "panels")


def _k(sess):
    return f"ks:{sess}"


def _unlock(key):
    """Best effort: a lock that cannot be released expires on its own, and a Redis hiccup here
    must not turn a round that already started into an error for the caller."""
    try:
        _kv.cmd("DEL", key)
    except Exception:  # noqa: BLE001
        pass


# ---- the stage marker -------------------------------------------------------------------------
# Go used to be silent: the Smash call runs inside the request that starts the round, and the
# round record is only written once it comes back, so for the first half minute every phone
# looked exactly as it did before the host pressed anything. This marker is written first and
# read by state(), so the room sees the work start immediately (Vinh, 2026-09-18).

def set_busy(sess, r, stage):
    try:
        _kv.cmd("SET", f"ks:{sess}:busy", json.dumps({"r": int(r), "stage": stage,
                                                      "ms": int(time.time() * 1000)}), "PX", BUSY_MS)
    except Exception:  # noqa: BLE001
        pass


def clear_busy(sess):
    _unlock(f"ks:{sess}:busy")


def get_busy(sess):
    try:
        raw = _kv.cmd("GET", f"ks:{sess}:busy")
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


# ---- per-session settings (kept across resets) ----------------------------------------------

def get_hero(sess):
    return _kv.cmd("GET", f"ks:{sess}:hero") or None


def set_hero(sess, data=None, clear=False):
    if clear:
        _kv.cmd("DEL", f"ks:{sess}:hero")
        return {"hero": None}
    if not data or len(data) < 1000:
        raise ValueError("no photo")
    if len(data) > 4_000_000:
        raise ValueError("photo is too big (4 MB max)")
    kind = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    path = f"ks/hero/{sess}-{secrets.token_hex(6)}.{'png' if kind == 'image/png' else 'jpg'}"
    old = get_hero(sess)
    _blob.put(path, data, kind)
    url = f"{_blob.base_url()}/{path}"
    _kv.cmd("SET", f"ks:{sess}:hero", url, "EX", TTL_S * 7)
    if old:
        try:
            _blob.delete([old])
        except Exception:  # noqa: BLE001
            pass
    return {"hero": url}


def _fetch(url, timeout=30):
    import urllib.request
    from _kv import _open
    with _open(urllib.request.Request(url), timeout) as r:
        return r.read()


def _hero_bytes(sess):
    url = get_hero(sess)
    return _fetch(url) if url else None


def get_voice(sess):
    """The voice that reads Yoland's lines: a clone of him once someone gives us audio, a
    stock male voice until then."""
    return _kv.cmd("GET", f"ks:{sess}:voice") or _voice.STOCK_VOICE


def set_voice(sess, voice_id=None, samples=None, name="Yoland"):
    """Point the session at an ElevenLabs voice, or clone one from reference audio."""
    if samples:
        voice_id = _voice.clone(name, samples)
    if not voice_id:
        raise ValueError("give a voice id or some audio")
    _kv.cmd("SET", f"ks:{sess}:voice", voice_id, "EX", TTL_S * 7)
    return {"voice": voice_id, "cloned": bool(samples)}


def total_rounds(sess):
    return int(_kv.cmd("GET", f"ks:{sess}:rounds") or 2)


def render_mode(sess):
    m = _kv.cmd("GET", f"ks:{sess}:render") or "pro"
    return m if m in RENDERS else "page"


def verdict_override(sess):
    v = _kv.cmd("GET", f"ks:{sess}:verdict") or ""
    return v if v in ("lives", "dies") else ""


def config(sess, rounds=None, render=None, verdict=None):
    """Host settings: how many rounds, which render path, and a verdict override for the next round."""
    if rounds is not None:
        _kv.cmd("SET", f"ks:{sess}:rounds", str(max(1, min(6, int(rounds)))), "EX", TTL_S * 7)
    if render is not None:
        if render not in RENDERS:
            raise ValueError("render is page, pro or panels")
        _kv.cmd("SET", f"ks:{sess}:render", render, "EX", TTL_S * 7)
    if verdict is not None:
        v = str(verdict).lower()
        if v in ("lives", "dies"):
            _kv.cmd("SET", f"ks:{sess}:verdict", v, "EX", TTL_S)
        else:
            _kv.cmd("DEL", f"ks:{sess}:verdict")
    return {"rounds": total_rounds(sess), "render": render_mode(sess), "verdict": verdict_override(sess) or "auto"}


def verdict_for(sess, round_no):
    return verdict_override(sess) or ("dies" if round_no >= total_rounds(sess) else "lives")


# ---- players and answers ----------------------------------------------------------------------

def _load(sess):
    h = _kv.hgetall(_k(sess))
    players, answers, rounds = {}, {}, {}
    for k, v in h.items():
        if k.startswith("p:"):
            players[k[2:]] = json.loads(v)
        elif k.startswith("a:"):
            _, r, voter = k.split(":", 2)
            answers.setdefault(int(r), {})[voter] = json.loads(v)
        elif k.startswith("n:"):
            rounds[int(k[2:])] = json.loads(v)
    meta = {"round": int(h.get("round") or 1),
            "setup": json.loads(h["setup"]) if h.get("setup") else None,
            "scene": h.get("scene") or "",
            "cont": json.loads(h["cont"]) if h.get("cont") else None}
    return players, answers, rounds, meta


def join(sess, voter, name):
    """Put a new player on the smaller team (coin flip on a tie). A returning player keeps
    their side. Nobody but the player is ever told which side that is."""
    if not NAME_RE.match(voter or ""):
        raise ValueError("bad voter")
    name = re.sub(r"\s+", " ", (name or "")).strip()[:14] or "someone"
    players, _, _, _ = _load(sess)
    me = players.get(voter)
    if me:
        if me["n"] != name:
            me["n"] = name
            _kv.cmd("HSET", _k(sess), f"p:{voter}", json.dumps(me))
        return {"team": me["t"], "name": name, "returning": True}
    counts = {t: sum(1 for p in players.values() if p["t"] == t) for t in TEAMS}
    team = min(TEAMS, key=lambda t: (counts[t], random.random()))
    rec = {"n": name, "t": team, "ms": int(time.time() * 1000)}
    _kv.pipe([["HSET", _k(sess), f"p:{voter}", json.dumps(rec)], ["EXPIRE", _k(sess), TTL_S]])
    return {"team": team, "name": name, "returning": False}


def clean_idea(text, limit=64):
    """An answer in the player's own words: letters, digits, spaces, apostrophes and hyphens,
    at most nine words, so a whole phrase fits without being chopped mid-thought."""
    t = " ".join((text or "").strip().lower().split()[:9])
    return WORD_RE.sub("", t)[:limit].strip("'- ")


def say(sess, voter, idea=None):
    """One answer per player per round, changeable until Go. Team Kill is answering "what kills
    him?" and team Save "what saves him?"; the answer itself never says which."""
    if not NAME_RE.match(voter or ""):
        raise ValueError("bad voter")
    players, answers, _, meta = _load(sess)
    if voter not in players:
        raise ValueError("join first")
    said = clean_idea(idea)
    if not said:
        raise ValueError("type an answer")
    r = meta["round"]
    rec = {"idea": said, "ms": int(time.time() * 1000)}
    _kv.pipe([["HSET", _k(sess), f"a:{r}:{voter}", json.dumps(rec)], ["EXPIRE", _k(sess), TTL_S]])
    return {"round": r, "idea": said}


# ---- the shared view --------------------------------------------------------------------------

def _voice_path(sess, rec, n):
    tag = f"-{rec['tag']}" if rec.get("tag") else ""
    return f"ks/{sess}/r{rec['r']}{tag}-v{n}.mp3"


def _path(sess, rec, panel=None):
    tag = f"-{rec['tag']}" if rec.get("tag") else ""
    if panel:
        return f"ks/{sess}/r{rec['r']}{tag}-p{panel}.png"
    return f"ks/{sess}/page{rec['r']}{tag}.png"


def _pools(players, answers_r):
    """The two secret pools: what each side answered, in their own words."""
    pools = {t: [] for t in TEAMS}
    for voter, a in answers_r.items():
        t = players.get(voter, {}).get("t")
        if t in pools and a.get("idea"):
            pools[t].append(a["idea"])
    return pools


def state(sess, voter=None):
    now = int(time.time() * 1000)
    players, answers, rounds, meta = _load(sess)
    base = _blob.base_url()
    total = total_rounds(sess)
    r = meta["round"]
    mine = answers.get(r, {}).get(voter) if voter else None
    this = answers.get(r, {})
    # The feed is anonymous: the answers alone, alphabetically, so neither who wrote one nor
    # which question it answered can be read off the order they arrived in.
    ideas = sorted(a["idea"] for a in this.values() if a.get("idea"))
    pages, drawing = [], None
    for n in sorted(rounds):
        rec = rounds[n]
        row = {"round": n, "verdict": rec["verdict"], "title": rec.get("title"), "story": rec.get("story"),
               "outcome": rec.get("outcome"), "layout": rec.get("layout"), "panels": rec.get("panels") or [],
               "render": rec.get("render"), "ms": rec["ms"], "scene": rec.get("scene"),
               "words": sorted(w for pool in (rec.get("kill"), rec.get("save"))
                               for w in (pool if isinstance(pool, list) else []))}
        if rec.get("error"):
            row["state"] = "failed"
            row["error"] = rec["error"]
        elif rec.get("done_ms"):
            row["state"] = "done"
            row["done_ms"] = rec["done_ms"]
            # One clip per panel, in Yoland's own voice, or [] when the speech step failed.
            row["audio"] = [f"{base}/{_voice_path(sess, rec, i)}" for i in range(1, rec.get("audio", 0) + 1)]
            # Every game's files carry their own tag: after a reset, round 1 must not reuse the
            # old round 1's file name, or browsers (and the pages' "is it new" check) keep the
            # old picture. Records from before the tag get a version query instead.
            v = "" if rec.get("tag") else f"?v={rec['done_ms']}"
            if rec.get("render") == "panels":
                row["images"] = [f"{base}/{_path(sess, rec, i)}{v}" for i in range(1, rec.get("images", 0) + 1)]
            else:
                row["image"] = f"{base}/{_path(sess, rec)}{v}"
        else:
            row["state"] = "drawing"
            row["stage"] = "drawing"
            row["eta_ms"] = max(0, rec["ms"] + {"panels": PANELS_MS, "fal": FAL_MS}.get(
                rec.get("render"), PAGE_MS) - now)
            if now - rec["ms"] < STUCK_MS:
                drawing = row
        pages.append(row)
    # Between Go and the job being submitted there is no record yet, and at the end of a round
    # the speech is made after the picture is stored: both are real work with nothing on screen,
    # so the marker fills them in and every client gets one "drawing" row with a stage on it.
    busy = get_busy(sess)
    if busy and now - busy["ms"] < STUCK_MS:
        rec = rounds.get(busy["r"])
        if rec is None or not (rec.get("done_ms") or rec.get("error")):
            if drawing is None:
                drawing = {"round": busy["r"], "state": "drawing", "stage": busy["stage"], "ms": busy["ms"],
                           "panels": [], "words": [],
                           "eta_ms": max(0, busy["ms"] + (VOICE_MS if busy["stage"] == "voicing" else WRITE_MS) - now)}
            else:
                drawing["stage"] = busy["stage"]
                if busy["stage"] == "voicing":
                    drawing["eta_ms"] = max(0, busy["ms"] + VOICE_MS - now)
    done = [p for p in pages if p["state"] == "done"]
    finished = len(rounds) >= total and all(rec.get("done_ms") or rec.get("error") for rec in rounds.values())
    me = players.get(voter) if voter else None
    last = done[-1] if done else None
    return {"now": now, "sess": sess, "hero": get_hero(sess), "hero_name": HERO_NAME,
            "hero_description": (meta["setup"] or {}).get("hero_description"),
            # There is no scene step any more: the words make the setting. What the room needs
            # between rounds is where the story got to, so round 2 reads as a continuation.
            "so_far": (last or {}).get("story") or "",
            "round": r, "total_rounds": total, "final": r >= total, "finished": finished,
            "next_verdict": verdict_for(sess, r), "verdict_override": verdict_override(sess) or "auto",
            "render": render_mode(sess), "voice": get_voice(sess),
            "ideas": ideas, "answers": len(this),
            "question": (f"What kills {HERO_NAME}?" if me["t"] == "kill"
                         else f"What saves {HERO_NAME}?") if me else None,
            "mine": {"idea": mine.get("idea", "")} if mine else None,
            "players": {t: sum(1 for p in players.values() if p["t"] == t) for t in TEAMS},
            "me": {"team": me["t"], "name": me["n"]} if me else None,
            "pages": pages, "current": done[-1] if done else None, "drawing": drawing,
            "can_start": drawing is None and bool(this) and r <= total and not finished}


# ---- the three calls --------------------------------------------------------------------------

def _panel_prompts(hero_description, cont, plan):
    """The per-panel prompts: the hero's current look and what the panel shows. The hero clause
    itself (photo / character sheet) is added by the graph builder."""
    look = (cont or {}).get("hero_look") or ""
    carrying = (cont or {}).get("hero_carrying") or ""
    who = f"{HERO_NAME} is {hero_description}"
    if look:
        who += f"; right now {look}"
    if carrying:
        who += f"; carrying {carrying}"
    return [(who + ". What happens: " + str(p.get("visual", ""))[:600] + COMIC_STYLE) for p in plan]


def start(sess):
    """Go: close the pot, run the Smash, submit the render job, and open the next round."""
    if _kv.cmd("SET", f"ks:{sess}:lock", "held", "NX", "PX", LOCK_MS) is None:
        return {"skipped": "someone else just pressed Go"}
    try:
        st = state(sess)
        if st["drawing"]:
            return {"skipped": "a page is still being drawn"}
        if st["finished"] or st["round"] > st["total_rounds"]:
            return {"skipped": "the game is over; reset to play again"}
        if not st["answers"]:
            return {"skipped": "no words in the pot yet"}
        players, answers, rounds, meta = _load(sess)
        r, total = meta["round"], st["total_rounds"]
        # Every screen learns the round has closed on its next poll, not when the writer finishes.
        set_busy(sess, r, "writing")
        pools = _pools(players, answers.get(r, {}))
        verdict = verdict_for(sess, r)
        previous = [{"round": n, "verdict": rounds[n]["verdict"].upper(), "outcome": rounds[n].get("outcome", "")}
                    for n in sorted(rounds)]
        prev_done = [n for n in sorted(rounds) if rounds[n].get("done_ms") and rounds[n].get("render") != "panels"]
        render = render_mode(sess)
        use_prev = render != "panels" and bool(prev_done)
        hero = _hero_bytes(sess)
        look = (meta["setup"] or {}).get("hero_description")
        # Round 1 carries the photo and the writer names the hero's look; later rounds reuse it.
        photo = hero if (hero and not look) else None
        kind = "image/png" if (photo and photo[:8] == b"\x89PNG\r\n\x1a\n") else "image/jpeg"
        plan = _llm.smash(HERO_NAME, r, total, verdict.upper(), look, meta["scene"],
                          previous, meta["cont"], pools["kill"], pools["save"], previous_page=use_prev,
                          panels_exactly=4, photo=photo, photo_type=kind)
        look = look or str(plan.get("hero_description") or "").strip() or HERO_LOOK
        panels = plan.get("panels") or []
        if not panels or (render != "panels" and not plan.get("page_prompt")):
            raise RuntimeError("the smash came back without panels or a page prompt: " + plan.get("raw", "")[:200])
        panels = panels[:4]
        seed = random.randrange(1, 2 ** 31)
        # fal draws the page in one synchronous call, so there is no job to submit here: the
        # picture is fetched in publish(), where the endpoint's job would have been polled.
        if render == "fal":
            job_id = ""
        else:
            c = client()
            if render == "panels":
                g = build_panels(_panel_prompts(look, meta["cont"], panels), seed=seed, hero=bool(hero), look=look)
            else:
                g = build_page(plan["page_prompt"] + PAGE_TAIL, seed=seed, previous_page=use_prev,
                               pro=(render == "pro"))
            wf = c.workflows.from_json(g)
            if hero and "2" in g:
                wf.set_input("2", "image", c.assets.from_bytes(hero, filename="hero.png"))
            if use_prev:
                prev = _fetch(f"{_blob.base_url()}/{_path(sess, rounds[prev_done[-1]])}")
                wf.set_input("4", "image", c.assets.from_bytes(prev, filename="prev.png"))
            job_id = c.submit(wf, api_key=_llm.partner_key()).id
        rec = {"r": r, "job": job_id, "verdict": verdict, "render": render, "ms": int(time.time() * 1000),
               "tag": secrets.token_hex(3),
               "seed": seed, "scene": meta["scene"], "kill": pools["kill"], "save": pools["save"],
               "look": look,
               "title": plan.get("title"), "story": plan.get("story"), "outcome": plan.get("outcome"),
               "layout": plan.get("layout"), "panels": panels, "page_prompt": plan.get("page_prompt"),
               "continuity": plan.get("continuity") or {}, "next_scene": plan.get("next_scene"),
               "n_panels": len(panels)}
        cmds = [["HSET", _k(sess), f"n:{r}", json.dumps(rec)],
                ["HSET", _k(sess), "setup", json.dumps({"hero_description": look})],
                ["HSET", _k(sess), "round", str(r + 1)],
                ["HSET", _k(sess), "cont", json.dumps(rec["continuity"])],
                ["HSET", _k(sess), "scene", plan.get("next_scene") or ""],
                ["DEL", f"ks:{sess}:verdict"],
                ["EXPIRE", _k(sess), TTL_S]]
        _kv.pipe(cmds)
        _voice_async(sess, rec)
        return {"round": r, "job": job_id, "verdict": verdict, "title": rec["title"], "panels": len(panels),
                "render": render, "next_scene": rec["next_scene"]}
    finally:
        # The record now carries the round, or the Smash failed and nothing should still say
        # "writing"; either way the marker's job is over.
        clear_busy(sess)
        _unlock(f"ks:{sess}:lock")


def _submit_render(sess, rec, render, seed, pro=False):
    """Send one render for a round that already has its plan. Used for the first attempt and
    for the retries, so a refusal from the image model never costs the story."""
    c = client()
    hero = _hero_bytes(sess)
    prev_done = [n for n in sorted(_load(sess)[2]) if n < rec["r"]]
    use_prev = render != "panels" and bool(prev_done)
    if render == "panels":
        g = build_panels(_panel_prompts(rec.get("look") or HERO_LOOK, rec.get("continuity"), rec["panels"]),
                         seed=seed, hero=bool(hero), look=rec.get("look") or HERO_LOOK)
    else:
        g = build_page(rec["page_prompt"] + PAGE_TAIL, seed=seed, previous_page=use_prev, pro=pro)
    wf = c.workflows.from_json(g)
    if hero and "2" in g:
        wf.set_input("2", "image", c.assets.from_bytes(hero, filename="hero.png"))
    if use_prev:
        rounds = _load(sess)[2]
        prev = _fetch(f"{_blob.base_url()}/{_path(sess, rounds[prev_done[-1]])}")
        wf.set_input("4", "image", c.assets.from_bytes(prev, filename="prev.png"))
    return c.submit(wf, api_key=_llm.partner_key())


def _fal_refs(sess, rec):
    """The hero photo first, then the previous page, as public URLs. fal takes URLs, so nothing
    is uploaded: the blob store already serves both."""
    refs = [get_hero(sess)]
    rounds = _load(sess)[2]
    prev = [n for n in sorted(rounds) if n < rec["r"] and rounds[n].get("done_ms")
            and rounds[n].get("render") != "panels"]
    if prev:
        refs.append(f"{_blob.base_url()}/{_path(sess, rounds[prev[-1]])}")
    return [x for x in refs if x]


def _fal_page(sess, rec, model=None):
    """Draw the page at fal and store it, in place of submitting and polling a job."""
    url = _fal.draw(rec["page_prompt"] + PAGE_TAIL, _fal_refs(sess, rec),
                    model=model or _fal.PRO)
    _blob.put(_path(sess, rec), _fetch(url, 60), "image/png")


def _retry(sess, rec, why):
    """The image model refuses now and then (a safety filter, a prompt it does not like). Try the
    Pro model once, then fall back to drawing the four panels on our own GPU, and only give up
    after that. A give-up rolls the round back so the host can simply press Go again."""
    tries = int(rec.get("tries", 0)) + 1
    rec["tries"] = tries
    rec["last_error"] = str(why)[:200]
    # Retry ladder. A refusal is provider-shaped, so the first step changes provider rather than
    # model: fal falls to the endpoint's Nano Banana and the endpoint falls to fal. Only after
    # both providers have said no does it come back to our own GPU.
    if rec.get("render") == "fal":
        plan = {1: ("pro", True), 2: ("panels", False)}.get(tries)
    else:
        plan = {1: ("fal", False), 2: ("panels", False)}.get(tries)
    if plan and rec.get("panels"):
        render, pro = plan
        try:
            if render == "fal":
                # Nothing to submit: flip the record to fal and let the next publish draw it,
                # so the picture is fetched exactly once however many times this is polled.
                rec.update({"render": "fal", "job": "", "ms": int(time.time() * 1000)})
                _kv.cmd("HSET", _k(sess), f"n:{rec['r']}", json.dumps(rec))
                return {"state": "retry", "round": rec["r"], "try": tries, "render": render}
            job = _submit_render(sess, rec, render, random.randrange(1, 2 ** 31), pro=pro)
        except Exception as e:  # noqa: BLE001
            plan = None
            rec["last_error"] = f"{rec['last_error']} | retry failed: {str(e)[:120]}"
        else:
            rec.update({"job": job.id, "render": render, "ms": int(time.time() * 1000)})
            _kv.cmd("HSET", _k(sess), f"n:{rec['r']}", json.dumps(rec))
            return {"state": "retry", "round": rec["r"], "try": tries, "render": render}
    # Out of retries: drop the round so its answers are live again and Go re-runs it.
    _kv.pipe([["HDEL", _k(sess), f"n:{rec['r']}"], ["HSET", _k(sess), "round", str(rec["r"])]])
    return {"state": "error", "round": rec["r"], "error": rec["last_error"], "rolled_back": True}


def _au(rec):
    """The field the speech thread writes to: per round AND per attempt, because a retry
    redraws under a new tag and the old attempt's clips are gone with it."""
    return f"au:{rec['r']}:{rec.get('tag') or '0'}"


def _voice_async(sess, rec):
    """Yoland's four lines are written the moment the Smash returns, and they do not depend on
    the picture, so the speech is made while the page is being drawn instead of after it. The
    result lands in its own field, because publish() owns the round record and this does not."""
    def run():
        try:
            got = _speak(sess, rec, mark=False)
        except Exception as e:  # noqa: BLE001
            got = {"audio": 0, "voice_error": str(e)[:160]}
        try:
            _kv.cmd("HSET", _k(sess), _au(rec), json.dumps(got))
        except Exception:  # noqa: BLE001
            pass
    t = threading.Thread(target=run, name=f"voice-{sess}-{rec['r']}", daemon=True)
    t.start()
    return t


def _voice_result(sess, rec, wait_s=30):
    """What the speech thread produced, waiting for it if the picture won the race."""
    deadline = time.time() + wait_s
    while True:
        raw = _kv.cmd("HGET", _k(sess), _au(rec))
        if raw:
            try:
                return json.loads(raw)
            except Exception:  # noqa: BLE001
                return None
        if time.time() >= deadline:
            return None
        time.sleep(0.5)


def _speak(sess, rec, mark=True):
    """Yoland reads his four lines. The four calls run together, so this costs a few seconds; a
    failure here must never cost the round, so it returns a result instead of raising."""
    if mark:
        set_busy(sess, rec["r"], "voicing")
    try:
        lines = [str(p.get("text") or p.get("caption") or "") for p in (rec.get("panels") or [])]
        clips = _voice.say_all(lines, get_voice(sess)) if any(lines) else []
        n = 0
        for i, mp3 in enumerate(clips, 1):
            if not mp3:
                break
            _blob.put(_voice_path(sess, rec, i), mp3, "audio/mpeg")
            n = i
        return {"audio": n}
    except Exception as e:  # noqa: BLE001
        return {"audio": 0, "voice_error": str(e)[:160]}


def publish(sess, round_no):
    """Draw the page (fal) or collect it from the finished job (the endpoint), store it in the
    blob store, then have Yoland read his four lines."""
    raw = _kv.cmd("HGET", _k(sess), f"n:{int(round_no)}")
    if not raw:
        return {"state": "error", "error": "no such round"}
    rec = json.loads(raw)
    if rec.get("done_ms") or rec.get("error"):
        return {"state": "done" if rec.get("done_ms") else "error", "round": rec["r"]}
    if rec.get("render") == "fal":
        # One synchronous call: no queue, no polling, and the picture comes back as a URL.
        try:
            _fal_page(sess, rec)
        except Exception as e:  # noqa: BLE001
            return _retry(sess, rec, e)
    else:
        got = poll(rec["job"])
        if got.get("state") == "error":
            return _retry(sess, rec, got.get("error"))
        if got.get("state") != "done":
            return got
    try:
        if rec.get("render") == "fal":
            pass                      # already stored above
        elif rec.get("render") == "panels":
            panels = story_panels(rec["job"])
            if not panels:
                raise RuntimeError("the job finished without any panels")
            for i, p in enumerate(panels, 1):
                _blob.put(_path(sess, rec, i), _fetch(p["url"], 60), "image/png")
            rec["images"] = len(panels)
        else:
            out = _output(client().jobs.get(rec["job"]), "31")
            if out is None:
                raise RuntimeError("the job finished without a page")
            _blob.put(_path(sess, rec), _fetch(str(out.get_download_url().url), 60), "image/png")
    except Exception as e:  # noqa: BLE001
        return _retry(sess, rec, e)
    got = _voice_result(sess, rec)
    rec.update(got if got is not None else _speak(sess, rec))
    rec["done_ms"] = int(time.time() * 1000)
    _kv.pipe([["HSET", _k(sess), f"n:{rec['r']}", json.dumps(rec)], ["EXPIRE", _k(sess), TTL_S]])
    clear_busy(sess)
    return {"state": "done", "round": rec["r"], "clips": rec.get("audio", 0)}


def advance(sess):
    st = state(sess)
    live = [p for p in st["pages"] if p["state"] == "drawing"]
    if not live:
        return {"did": []}
    if _kv.cmd("SET", f"ks:{sess}:publock", "held", "NX", "PX", 120_000) is None:
        return {"did": [], "skipped": "another publisher is on it"}
    try:
        return {"did": [{"round": p["round"], **publish(sess, p["round"])} for p in live]}
    finally:
        _unlock(f"ks:{sess}:publock")


def reset(sess):
    """Wipe the game: players, answers, setup, pages. The hero photo and the settings stay."""
    keep = (":hero", ":rounds", ":render", ":voice")
    keys = [_k(sess)] + [k for k in _kv.scan(f"ks:{sess}:*") if not k.endswith(keep)]
    if keys:
        _kv.cmd("DEL", *keys)
    urls = [b["url"] for b in _blob.listing(f"ks/{sess}/")]
    for i in range(0, len(urls), 100):
        _blob.delete(urls[i:i + 100])
    return {"cleared": len(keys), "pictures": len(urls)}
