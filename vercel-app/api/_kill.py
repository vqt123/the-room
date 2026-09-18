"""Kill or Save: one character (Yoland), two secret teams, one four-panel comic per Go.

Everyone who joins is put on team Kill or team Save and told only their own side. Everybody
drops single words into one shared, anonymous feed; the room sees every word and nobody can
tell whose it is or which side it serves. When the host presses Go, ONE job runs on the
endpoint: an LLM node smashes both teams' words into a four-panel story (the LLM is told
which words were Kill and which were Save; the last panel always gets Yoland), five regex
nodes cut the cast and the four panel descriptions out of it, and four pictures are drawn
with Yoland's photo as the reference. Each caption rides out as its picture's file name.
The pot is emptied at Go; new words go into the next comic while this one draws.

State is one Redis hash per session, `ks:<sess>`:
  p:<voter>  json {n: name, t: team, ms}
  w:<id>     json {t: word, v: voter, r: round, ms}
  round      the round the pot is filling right now
  n:<r>      json {r, job, kill, save, ms, prompt, panels?, captions?, done_ms?, error?}
plus `ks:<sess>:hero` (the photo URL, kept across resets) and two short locks.
"""
import json, os, random, re, secrets, sys, time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import _blob, _kv
from _core import check_password  # noqa: F401
from _findcore import client, partner_key, poll, story_panels
from _findgraph import HERO_NAME, build_kill

TTL_S = 86_400
LOCK_MS = 30_000
STUCK_MS = 210_000
ROUND_MS = 45_000            # an LLM call plus four 1024-square passes, for the countdown on screen
WORDS_PER_PERSON = 8         # per round
TEAMS = ("kill", "save")
NAME_RE = re.compile(r"^[a-z0-9]{6,12}$")
WORD_RE = re.compile(r"[^a-z0-9'\-]+")


def _k(sess):
    return f"ks:{sess}"


# ---- the main character ----------------------------------------------------------------

def get_hero(sess):
    return _kv.cmd("GET", f"ks:{sess}:hero") or None


def set_hero(sess, data=None, clear=False):
    """Store the main character's photo for this session. It lives outside the session's
    picture folder, so a reset keeps it."""
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


def _hero_bytes(sess):
    url = get_hero(sess)
    if not url:
        return None
    import urllib.request
    with urllib.request.urlopen(url, timeout=30) as r:
        return r.read()


# ---- players and words ------------------------------------------------------------------

def _load(sess):
    h = _kv.hgetall(_k(sess))
    players, words, rounds = {}, {}, {}
    for k, v in h.items():
        if k.startswith("p:"):
            players[k[2:]] = json.loads(v)
        elif k.startswith("w:"):
            words[k[2:]] = json.loads(v)
        elif k.startswith("n:"):
            rounds[int(k[2:])] = json.loads(v)
    current = int(h.get("round") or 1)
    return players, words, rounds, current


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


def clean_word(text):
    """One word: the first token typed, letters, digits, apostrophes and hyphens only."""
    first = (text or "").strip().lower().split()
    if not first:
        return ""
    return WORD_RE.sub("", first[0])[:20].strip("'-")


def word(sess, voter, text):
    if not NAME_RE.match(voter or ""):
        raise ValueError("bad voter")
    w = clean_word(text)
    if not w:
        raise ValueError("type one word")
    players, words, _, current = _load(sess)
    if voter not in players:
        raise ValueError("join first")
    mine = [x for x in words.values() if x["v"] == voter and x["r"] == current]
    if len(mine) >= WORDS_PER_PERSON:
        raise ValueError(f"{WORDS_PER_PERSON} words per round each, that is the cap")
    if any(x["t"] == w for x in words.values() if x["r"] == current):
        return {"word": w, "round": current, "dup": True}
    wid = f"{int(time.time() * 1000)}-{secrets.token_hex(2)}"
    rec = {"t": w, "v": voter, "r": current, "ms": int(time.time() * 1000)}
    _kv.pipe([["HSET", _k(sess), f"w:{wid}", json.dumps(rec)], ["EXPIRE", _k(sess), TTL_S]])
    return {"word": w, "round": current, "mine": len(mine) + 1}


# ---- the shared view ----------------------------------------------------------------------

def state(sess, voter=None):
    now = int(time.time() * 1000)
    players, words, rounds, current = _load(sess)
    base = _blob.base_url()
    feed = sorted((w for w in words.values() if w["r"] == current), key=lambda w: w["ms"])
    comics, drawing = [], None
    for r in sorted(rounds):
        rec = rounds[r]
        n_words = len(rec.get("kill", [])) + len(rec.get("save", []))
        row = {"round": r, "ms": rec["ms"], "words": n_words,
               "all_words": sorted(rec.get("kill", []) + rec.get("save", []))}
        if rec.get("error"):
            row["state"] = "failed"
            row["error"] = rec["error"]
        elif rec.get("done_ms"):
            row["state"] = "done"
            row["panels"] = [f"{base}/ks/{sess}/c{r}-p{n}.png" for n in range(1, rec["panels"] + 1)]
            row["captions"] = rec.get("captions") or []
        else:
            row["state"] = "drawing"
            row["eta_ms"] = max(0, rec["ms"] + ROUND_MS - now)
            if now - rec["ms"] < STUCK_MS:
                drawing = row
        comics.append(row)
    done = [c for c in comics if c["state"] == "done"]
    me = players.get(voter) if voter else None
    return {"now": now, "sess": sess, "hero": get_hero(sess), "hero_name": HERO_NAME,
            "round": current,
            "feed": [{"id": k, "word": v["t"]} for k, v in
                     sorted(words.items(), key=lambda kv: kv[1]["ms"]) if v["r"] == current],
            "feed_count": len(feed),
            "my_words": [w["t"] for w in feed if voter and w["v"] == voter],
            "players": {t: sum(1 for p in players.values() if p["t"] == t) for t in TEAMS},
            "me": {"team": me["t"], "name": me["n"]} if me else None,
            "comics": comics, "current": done[-1] if done else None, "history": done[-6:],
            "drawing": drawing,
            "can_start": drawing is None and bool(feed),
            "words_per_person": WORDS_PER_PERSON}


# ---- a round --------------------------------------------------------------------------------

def start(sess):
    """Close the pot into one job. New words go to the next comic while this one draws."""
    if _kv.cmd("SET", f"ks:{sess}:lock", "held", "NX", "PX", LOCK_MS) is None:
        return {"skipped": "someone else just pressed Go"}
    try:
        st = state(sess)
        if st["drawing"]:
            return {"skipped": "a comic is still being drawn"}
        if not st["feed"]:
            return {"skipped": "no words in the pot yet"}
        if not partner_key():
            raise RuntimeError("server is missing COMFY_PARTNER_API_KEY (the LLM node needs a production key)")
        players, words, rounds, current = _load(sess)
        feed = sorted((w for w in words.values() if w["r"] == current), key=lambda w: w["ms"])
        kill = [w["t"] for w in feed if players.get(w["v"], {}).get("t") == "kill"]
        save = [w["t"] for w in feed if players.get(w["v"], {}).get("t") == "save"]
        seed = random.randrange(1, 2 ** 31)
        hero = _hero_bytes(sess)
        graph, prompt = build_kill(kill, save, seed=seed, hero=bool(hero))
        c = client()
        wf = c.workflows.from_json(graph)
        if hero:
            wf.set_input("2", "image", c.assets.from_bytes(hero, filename="hero.png"))
        job = c.submit(wf, api_key=partner_key())
        rec = {"r": current, "job": job.id, "kill": kill, "save": save,
               "ms": int(time.time() * 1000), "seed": seed, "prompt": prompt}
        _kv.pipe([["HSET", _k(sess), f"n:{current}", json.dumps(rec)],
                  ["HSET", _k(sess), "round", str(current + 1)],
                  ["EXPIRE", _k(sess), TTL_S]])
        return {"round": current, "job": job.id, "kill": len(kill), "save": len(save)}
    finally:
        _kv.cmd("DEL", f"ks:{sess}:lock")


def publish(sess, round_no):
    """Once the endpoint is finished, copy the four panels into the blob store and keep the captions."""
    raw = _kv.cmd("HGET", _k(sess), f"n:{int(round_no)}")
    if not raw:
        return {"state": "error", "error": "no such round"}
    rec = json.loads(raw)
    if rec.get("done_ms") or rec.get("error"):
        return {"state": "done" if rec.get("done_ms") else "error", "round": rec["r"]}
    got = poll(rec["job"])
    if got.get("state") == "error":
        rec["error"] = str(got.get("error"))[:160]
        _kv.cmd("HSET", _k(sess), f"n:{rec['r']}", json.dumps(rec))
        return {"state": "error", "error": rec["error"]}
    if got.get("state") != "done":
        return got
    import urllib.request
    panels = story_panels(rec["job"])
    if not panels:
        rec["error"] = "the job finished without any panels"
        _kv.cmd("HSET", _k(sess), f"n:{rec['r']}", json.dumps(rec))
        return {"state": "error", "error": rec["error"]}
    for n, p in enumerate(panels, 1):
        with urllib.request.urlopen(p["url"], timeout=60) as r:
            _blob.put(f"ks/{sess}/c{rec['r']}-p{n}.png", r.read(), "image/png")
    rec["panels"] = len(panels)
    rec["captions"] = [p["caption"] for p in panels]
    rec["done_ms"] = int(time.time() * 1000)
    _kv.pipe([["HSET", _k(sess), f"n:{rec['r']}", json.dumps(rec)], ["EXPIRE", _k(sess), TTL_S]])
    return {"state": "done", "round": rec["r"], "captions": rec["captions"]}


def advance(sess):
    """Move any finished round along. Every browser calls this while a comic is in flight; a
    lock keeps it to one publisher at a time."""
    st = state(sess)
    live = [c for c in st["comics"] if c["state"] == "drawing"]
    if not live:
        return {"did": []}
    if _kv.cmd("SET", f"ks:{sess}:publock", "held", "NX", "PX", LOCK_MS) is None:
        return {"did": [], "skipped": "another publisher is on it"}
    try:
        return {"did": [{"round": c["round"], **publish(sess, c["round"])} for c in live]}
    finally:
        _kv.cmd("DEL", f"ks:{sess}:publock")


def reset(sess):
    """Wipe the game: players, words, comics, pictures. The hero photo stays."""
    keys = [_k(sess)] + [k for k in _kv.scan(f"ks:{sess}:*") if not k.endswith(":hero")]
    if keys:
        _kv.cmd("DEL", *keys)
    urls = [b["url"] for b in _blob.listing(f"ks/{sess}/")]
    for i in range(0, len(urls), 100):
        _blob.delete(urls[i:i + 100])
    return {"cleared": len(keys), "pictures": len(urls)}
