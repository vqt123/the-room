"""The Room: a shared queue that a crowd fills, draining into one GPU.

Anyone in the room adds things from their phone and upvotes what is already queued. Every
tick the queue is emptied - not one item, ALL of it, up to a cap - into a SINGLE job: the
endpoint paints one busy scene and the private HideIt node hides every submitted thing in
it, one node per thing, each writing down the exact box it used. The picture goes up on the
big screen and on every phone at once and the room hunts all of them at the same time. Each
thing has its own first-finder.

Everything the room agrees on is one Redis hash, `rm:<sess>`, so a phone's poll is a single
HGETALL. The moments that could race are Redis primitives rather than code: HSETNX settles
who found a given thing first, and SET NX PX stops two tickers starting the same round. The
answer boxes live in their own key that no state read touches.

Pictures are too big for Redis and go to Vercel Blob, whose CDN then serves them to the room.
"""
import json, os, random, re, secrets, sys, time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import _blob
import _kv
from _findcore import (TAP_PAD, answers_many, poll, puzzle_url, story_text, submit_mash, submit_many,
                       submit_story, submit_vn)
from _findgraph import MAX_THINGS, MAX_WORDS, to_ing
from _core import check_password  # noqa: F401

# One thing takes about 24 s to draw and hide, each extra one about 4.5 s more (measured
# 2026-09-18 on the live endpoint: 3 things, 33 s). Drawing and playing are pipelined, so
# the window a picture stays up has to cover the next round's drawing AND give the room
# time to find everything in it. Both grow with the number of things, so the window does too.
BASE_PLAY_MS = 20_000
PER_THING_MS = 8_000
MIN_PLAY_MS, MAX_PLAY_MS = 30_000, 60_000
BUFFER = 1                  # how many rounds to keep drawn ahead of the one on screen
# What a round is expected to cost, so the ticker can come back when it is actually due
# instead of polling. Measured on the live endpoint: find 24 s for one thing and about 4.5 s
# per extra one, mash-up 11 s whatever the word count.
FIND_BASE_MS, FIND_PER_MS, MASH_MS = 24_000, 4_500, 12_000
MASH_PLAY_MS = 20_000       # a mash-up is looked at, not hunted, so it needs less time
VN_PLAY_MS = 25_000         # the verb-and-noun round: long enough to read the board and vote again
VNFIND_PLAY_MS = 45_000     # ... and longer again when the thing has to be found, not just looked at
KINDS = ("verb", "noun")
# The two modes that run on verb-and-noun pools with one vote each. Anything that asks
# "is this the single-ballot game?" must ask about both, or ballots silently stop counting.
VN_MODES = ("vn", "vnfind")
# Modes that put everything submitted this window into one picture, with no hiding.
MASH_MODES = ("mash", "pairs", "story")
STORY_MS = 20_000           # an LLM call plus one sampler pass, measured ~7 s + 11 s
PRELOAD_MS = 9_000          # how early the next picture's URL is handed out, to warm the cache
STUCK_MS = 210_000          # a job still going after this long stops holding the queue up
TTL_S = 86_400              # a session forgets itself after a day
LOCK_MS = 30_000
ID_RE = re.compile(r"^\d+-[0-9a-f]{6}$")
NAME_RE = re.compile(r"^[a-z0-9]{6,12}$")

# When nobody has queued anything, the house does, so the screen never stalls.
HOUSE = ["a rubber duck", "a traffic cone", "a fire extinguisher", "a taco", "a garden gnome",
         "a red telephone", "a watermelon", "a cowboy hat", "a rotary phone", "a pineapple",
         "a bowling ball", "a toaster", "a trophy", "a cactus", "a disco ball"]
HOUSE_VERBS = ["dancing", "exploding", "napping", "arguing", "juggling", "surfing", "knitting",
               "escaping", "celebrating", "brooding", "stampeding", "vanishing"]
HOUSE_NOUNS = ["a shark", "a librarian", "a walrus", "a vending machine", "a wizard", "a tractor",
               "a jellyfish", "a bagpiper", "a snowman", "a hot air balloon", "a crab", "a robot"]


def _room_key(sess):
    return f"rm:{sess}"


def _key_key(sess, job_id):
    return f"rm:{sess}:k:{job_id}"


def _clean(s, n):
    return re.sub(r"\s+", " ", (s or "")).strip()[:n]


def play_ms(n, mode="find"):
    if mode in ("pairs", "story"):
        return 0
    if mode == "vnfind":
        return VNFIND_PLAY_MS
    if mode == "vn":
        return VN_PLAY_MS
    if mode in MASH_MODES:
        return MASH_PLAY_MS
    return max(MIN_PLAY_MS, min(MAX_PLAY_MS, BASE_PLAY_MS + PER_THING_MS * max(1, n)))


def draw_ms(n, mode="find"):
    """How long this round should take to draw. Used to schedule the next tick."""
    if mode == "vnfind":
        return FIND_BASE_MS
    if mode == "story":
        return STORY_MS
    if mode in MASH_MODES or mode == "vn":
        return MASH_MS
    return FIND_BASE_MS + FIND_PER_MS * max(0, n - 1)


def get_mode(sess):
    return _kv.cmd("GET", f"rm:{sess}:mode") or "find"


def set_mode(sess, mode):
    mode = str(mode or "").strip().lower()
    mode = mode if mode in ("find", "mash", "pairs", "story", "vn", "vnfind") else "find"
    _kv.cmd("SET", f"rm:{sess}:mode", mode, "EX", TTL_S)
    return {"mode": mode}


def state(sess):
    """Everything the room agrees on, from one HGETALL."""
    now = int(time.time() * 1000)
    h = _kv.hgetall(_room_key(sess))
    items, votes, rounds, dones, fails, shows, wins = {}, {}, {}, {}, {}, {}, {}
    ballots, retired = {}, set()
    for field, val in h.items():
        kind, _, rest = field.partition(":")
        try:
            if kind == "i":
                items[rest] = dict(json.loads(val), id=rest)
            elif kind == "v":
                item, _, voter = rest.rpartition(":")
                votes.setdefault(item, set()).add(voter)
            elif kind == "V":
                # One vote each: the field is the VOTER, so voting again overwrites.
                k, _, voter = rest.partition(":")
                ballots.setdefault(k, {})[voter] = val
            elif kind == "x":
                retired.add(rest)
            elif kind == "n":
                rounds[rest] = dict(json.loads(val), id=rest)
            elif kind == "d":
                dones[rest] = int(val)
            elif kind == "f":
                fails[rest] = val
            elif kind == "s":
                shows[rest] = int(val)
            elif kind == "w":
                rnd, _, item = rest.partition(":")
                ms, voter, name = val.split("|", 2)
                wins.setdefault(rnd, {})[item] = {"ms": int(ms), "voter": voter, "name": name}
        except (ValueError, KeyError):
            continue

    base, taken = _blob.base_url(), set()
    rows = []
    for r in rounds.values():
        rid = r["id"]
        if rid in fails:
            st = "failed"
        elif rid in shows:
            st = "onscreen"
        elif rid in dones:
            st = "ready"
        else:
            st = "drawing"
        things = []
        for t in r.get("items", []):
            taken.add(t["id"])
            src = items.get(t["id"], {})
            w = wins.get(rid, {}).get(t["id"])
            things.append({"id": t["id"], "text": t["text"], "by": src.get("b", "someone"),
                           "kind": t.get("kind"), "votes": t.get("votes"),
                           "found_by": w["name"] if w else None,
                           # Only the hide-and-find modes draw each thing on its own; the
                           # others have no thumbnail, and a URL to nothing is a broken image.
                           "thumb": (f"{base}/rm/{sess}/thumb/{rid}/{t['id']}.png"
                                     if rid in dones and r.get("mode", "find") in ("find", "vnfind")
                                     else None)})
        row = {"id": rid, "ms": r["ms"], "job": r.get("job"), "scene": r.get("scene"),
               "mode": r.get("mode", "find"), "prompt": r.get("prompt"),
               "subject": r.get("subject"), "story": r.get("story"),
               "state": st, "things": things, "count": len(things),
               "found": sum(1 for t in things if t["found_by"])}
        if st == "drawing":
            row["eta_ms"] = max(0, r["ms"] + draw_ms(len(things), row["mode"]) - now)
        if rid in dones:
            row["image"] = f"{base}/rm/{sess}/img/{rid}.png"
        if rid in shows:
            row["shown_ms"] = shows[rid]
        if rid in fails:
            row["error"] = fails[rid]
        rows.append(row)

    mode = get_mode(sess)
    pending = []
    for it in items.values():
        if it["id"] in taken or it["id"] in retired:
            continue
        k = it.get("k")
        if mode in VN_MODES:
            # One ballot per person per kind, so the count is how many people named this one.
            who = sorted(v for v, chosen in ballots.get(k, {}).items() if chosen == it["id"])
        else:
            who = sorted(votes.get(it["id"], ()))
        pending.append(dict(it, votes=len(who), voters=who, kind=k,
                            text=it["t"], by=it["b"], ms=it["m"]))
    queue = sorted(pending, key=lambda r: (-r["votes"], r["ms"]))
    verbs = [q for q in queue if q["kind"] == "verb"]
    nouns = [q for q in queue if q["kind"] == "noun"]
    drawing = sorted([r for r in rows if r["state"] == "drawing"], key=lambda r: r["ms"])
    ready = sorted([r for r in rows if r["state"] == "ready"], key=lambda r: r["ms"])
    shown = sorted([r for r in rows if r["state"] == "onscreen"], key=lambda r: r["shown_ms"])
    onscreen = shown[-1] if shown else None

    live = [d for d in drawing if now - d["ms"] < STUCK_MS]
    window = play_ms(onscreen["count"], onscreen["mode"]) if onscreen else 0
    show_left = max(0, window - (now - onscreen["shown_ms"])) if onscreen else 0
    # The next picture is already drawn and sitting in the CDN during the countdown. Handing
    # its URL over early lets every browser pull it before the flip, so the flip itself costs
    # nothing but a src change. In a hunt the URL IS the answer sheet, so it is held back
    # until the last few seconds; a mash-up hides nothing, so it goes out as soon as it exists.
    early = mode == "mash" or not onscreen or show_left <= PRELOAD_MS
    preload = ready[0]["image"] if ready and early else None
    return {"now": now, "queue": queue, "verbs": verbs, "nouns": nouns,
            "drawing": drawing, "ready": ready,
            "history": shown[-6:], "current": onscreen, "onscreen": onscreen,
            # In pairs the house never fills in: an empty list means wait, and the picture
            # that is up stays up until there is genuinely something new to draw.
            "can_start": len(live) + len(ready) < BUFFER and (mode not in ("pairs", "story") or bool(queue)),
            "can_show": bool(ready) and show_left == 0,
            "show_left": show_left, "period": window or play_ms(1, mode),
            "mode": mode, "preload": preload,
            "max_things": MAX_WORDS if mode == "mash" else MAX_THINGS,
            "next_ms": now + show_left, "blob_base": base, "sess": sess}


def add(sess, text, by, voter, kind=None, verb=None, noun=None):
    if verb or noun:
        # A pair is one item: "a shark dancing". Whoever typed it owns it, nobody votes on it.
        n = _clean(noun, 24) or "something"
        for article in ("a ", "an ", "the "):
            if n.lower().startswith(article):
                n = n[len(article):]
                break
        text = f"a {n} {to_ing(_clean(verb, 24))}".strip()
        kind = None
    text = _clean(text, 40)
    if not text:
        raise ValueError("type something first")
    if not NAME_RE.match(voter or ""):
        raise ValueError("bad voter id")
    if kind is not None and kind not in KINDS:
        raise ValueError("a submission is either a verb or a noun")
    if kind == "verb":
        text = to_ing(text) or text
    ms = int(time.time() * 1000)
    item = f"{ms}-{secrets.token_hex(3)}"
    body = {"t": text, "b": _clean(by, 14) or "someone", "m": ms}
    if kind:
        body["k"] = kind
    # You back your own the moment you put it up, and can move your vote afterwards.
    ballot = [f"V:{kind}:{voter}", item] if kind else [f"v:{item}:{voter}", "1"]
    _kv.pipe([["HSET", _room_key(sess), f"i:{item}", json.dumps(body)] + ballot,
              ["EXPIRE", _room_key(sess), TTL_S]])
    return {"id": item, "text": text, "kind": kind}


def vote(sess, item, voter):
    """In find and mash you can back as many as you like. In the verb-and-noun game you get
    one vote per pool, so the ballot is stored under YOUR name and voting again replaces it
    rather than adding to it."""
    if not NAME_RE.match(voter or "") or not ID_RE.match(item or ""):
        raise ValueError("bad vote")
    if get_mode(sess) in VN_MODES:
        raw = _kv.cmd("HGET", _room_key(sess), f"i:{item}")
        kind = (json.loads(raw).get("k") if raw else None)
        if kind not in KINDS:
            raise ValueError("that is not on the board any more")
        _kv.cmd("HSET", _room_key(sess), f"V:{kind}:{voter}", item)
        return {"ok": True, "kind": kind, "single": True}
    _kv.cmd("HSET", _room_key(sess), f"v:{item}:{voter}", "1")
    return {"ok": True, "single": False}


def start(sess, force=False):
    """Empty the queue into one job. Everything waiting goes into the same picture."""
    st = state(sess)
    if not force and not st["can_start"]:
        return {"skipped": "not yet", "wait_ms": max(0, st["next_ms"] - st["now"])}
    # A real lock, so a second ticker or a second screen cannot submit the same round.
    if _kv.cmd("SET", f"rm:{sess}:lock", "held", "NX", "PX", LOCK_MS) is None:
        return {"skipped": "another ticker is starting this one"}
    try:
        mode = get_mode(sess)
        rid = f"{int(time.time() * 1000)}-{secrets.token_hex(3)}"
        if mode in VN_MODES:
            return _start_vn(sess, st, rid, hide=(mode == "vnfind"))
        cap = MAX_WORDS if mode in MASH_MODES else MAX_THINGS
        picked = st["queue"][:cap]
        if not picked and mode in ("pairs", "story"):
            return {"skipped": "nothing on the list yet"}
        if not picked:
            picked = [add(sess, random.choice(HOUSE), "the house", "housebot00")]
        words = [p["text"] for p in picked]
        if mode == "story":
            got = submit_story(words)
            items = [{"id": p["id"], "text": p["text"]} for p in picked]
        elif mode in MASH_MODES:
            got = submit_mash(words)
            items = [{"id": p["id"], "text": p["text"]} for p in picked]
        else:
            got = submit_many(words, scene=None, difficulty=3)
            items = [{"id": p["id"], "text": p["text"], "node": t["node"]}
                     for p, t in zip(picked, got["things"])]
        record = {"ms": int(time.time() * 1000), "job": got["id"], "scene": got.get("scene"),
                  "mode": mode, "prompt": got.get("prompt"), "items": items}
        cmds = [["HSET", _room_key(sess), f"n:{rid}", json.dumps(record)]]
        # Once it generates, the whole list goes: anything past the cap is retired rather
        # than carried into the next round.
        leftover = [f"x:{q['id']}" for q in st["queue"][cap:]]
        if leftover:
            cmds.append(["HSET", _room_key(sess)] + [x for i in leftover for x in (i, rid)])
        cmds.append(["EXPIRE", _room_key(sess), TTL_S])
        _kv.pipe(cmds)
        return {"round": rid, "job": got["id"], "mode": mode, "scene": got.get("scene"),
                "things": words}
    finally:
        _kv.cmd("DEL", f"rm:{sess}:lock")


def _start_vn(sess, st, rid, hide=False):
    """The verb-and-noun round: the top of each pool, and then the board is wiped.

    Everything else submitted this window is retired rather than carried over, and every
    ballot is cleared, so the next round starts from nothing and one vote each means
    something again.

    With `hide`, the subject the room voted for is not just drawn: it is drawn and then
    hidden in a busy scene by the private node, and the room has to find the thing it chose.
    That is the same Find It pipeline with one thing in it, so the answer still arrives on
    the thumbnail's filename and never reaches a phone.
    """
    verb = st["verbs"][0] if st["verbs"] else add(sess, random.choice(HOUSE_VERBS),
                                                  "the house", "housebot00", "verb")
    noun = st["nouns"][0] if st["nouns"] else add(sess, random.choice(HOUSE_NOUNS),
                                                  "the house", "housebot00", "noun")
    subject = f"{noun['text']} {to_ing(verb['text'])}".strip()
    for article in ("a ", "an ", "the "):
        if subject.lower().startswith(article):
            subject = subject[len(article):]
            break
    if hide:
        got = submit_many([subject], scene=None, difficulty=3)
        node = got["things"][0]["node"]
    else:
        got = submit_vn(verb["text"], noun["text"])
        node = None
        subject = got.get("subject", subject)
    item = {"id": noun["id"], "text": subject, "kind": "noun",
            "votes": noun.get("votes", 0)}
    if node:
        item["node"] = node
    record = {"ms": int(time.time() * 1000), "job": got["id"], "scene": got.get("scene"),
              "mode": "vnfind" if hide else "vn", "prompt": got.get("prompt"),
              "subject": subject,
              "items": [{"id": verb["id"], "text": verb["text"], "kind": "verb",
                         "votes": verb.get("votes", 0)}, item] if not hide else [item]}
    wipe = []
    for row in st["verbs"] + st["nouns"]:
        if row["id"] not in (verb["id"], noun["id"]):
            wipe += [f"x:{row['id']}", rid]
    if hide:
        wipe += [f"x:{verb['id']}", rid]   # folded into the subject; not a thing of its own
    cmds = [["HSET", _room_key(sess), f"n:{rid}", json.dumps(record)]]
    if wipe:
        cmds.append(["HSET", _room_key(sess)] + wipe)
    ballot_fields = [f for f in _kv.hgetall(_room_key(sess)) if f.startswith("V:")]
    if ballot_fields:
        cmds.append(["HDEL", _room_key(sess)] + ballot_fields)
    cmds.append(["EXPIRE", _room_key(sess), TTL_S])
    _kv.pipe(cmds)
    return {"round": rid, "job": got["id"], "mode": "vnfind" if hide else "vn",
            "subject": subject, "verb": verb["text"], "noun": noun["text"],
            "scene": got.get("scene")}


def show(sess, rid):
    """Put a drawn picture up on every screen at the same moment."""
    if not ID_RE.match(rid or ""):
        raise ValueError("bad round")
    _kv.cmd("HSET", _room_key(sess), f"s:{rid}", str(int(time.time() * 1000)))
    return {"shown": rid}


def _round(sess, rid):
    raw = _kv.cmd("HGET", _room_key(sess), f"n:{rid}")
    return json.loads(raw) if raw else None


def publish(sess, rid):
    """Once the endpoint is finished, copy the picture and every thumbnail into the blob
    store, and put the boxes somewhere only this server looks."""
    if not ID_RE.match(rid or ""):
        raise ValueError("bad round")
    rec = _round(sess, rid)
    if not rec:
        return {"state": "error", "error": "no such round"}
    job_id = rec["job"]
    got = poll(job_id)
    if got.get("state") == "error":
        _kv.cmd("HSET", _room_key(sess), f"f:{rid}", str(got.get("error"))[:120])
        return {"state": "error", "error": got.get("error")}
    if got.get("state") != "done":
        return got
    import urllib.request
    url = puzzle_url(job_id)
    with urllib.request.urlopen(url, timeout=60) as r:
        _blob.put(f"rm/{sess}/img/{rid}.png", r.read(), "image/png")
    if rec.get("mode") == "story":
        story = story_text(job_id)
        if story:
            rec["story"] = story
            _kv.cmd("HSET", _room_key(sess), f"n:{rid}", json.dumps(rec))
    if rec.get("mode") in MASH_MODES or rec.get("mode") == "vn":
        _kv.pipe([["HSET", _room_key(sess), f"d:{rid}", str(int(time.time() * 1000))],
                  ["EXPIRE", _room_key(sess), TTL_S]])
        return {"state": "done", "round": rid, "mode": rec.get("mode"),
                "things": len(rec["items"])}
    found = answers_many(job_id, [t["node"] for t in rec["items"]]) or []
    boxes = {}
    for t, a in zip(rec["items"], found):
        if not a:
            continue
        boxes[t["id"]] = a["box"]
        with urllib.request.urlopen(a["thumb_url"], timeout=45) as r:
            _blob.put(f"rm/{sess}/thumb/{rid}/{t['id']}.png", r.read(), "image/png")
    _kv.cmd("SET", _key_key(sess, job_id), json.dumps(boxes), "EX", TTL_S)
    _kv.pipe([["HSET", _room_key(sess), f"d:{rid}", str(int(time.time() * 1000))],
              ["EXPIRE", _room_key(sess), TTL_S]])
    return {"state": "done", "round": rid, "answers": len(boxes), "things": len(rec["items"])}


def _inside(box, x, y):
    return (box["x"] - TAP_PAD <= x <= box["x"] + box["w"] + TAP_PAD
            and box["y"] - TAP_PAD <= y <= box["y"] + box["h"] + TAP_PAD)


def _distance(box, x, y):
    cx, cy = box["x"] + box["w"] / 2, box["y"] + box["h"] / 2
    return ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5


def found(sess, rid, x, y, voter, name):
    """Score a tap against every thing hidden in this round, and on a hit claim that one.

    A tap does not say what it is aiming at, so it is checked against all of them. HSETNX
    writes only if that thing is still unclaimed, so the first person to reach it wins it
    and everybody else is told who did.
    """
    if not NAME_RE.match(voter or "") or not ID_RE.match(rid or ""):
        raise ValueError("bad tap")
    rec = _round(sess, rid)
    if not rec:
        return {"error": "that round is not ready"}
    if rec.get("mode") in ("mash", "vn"):
        return {"error": "nothing is hidden in a mash-up"}
    raw = _kv.cmd("GET", _key_key(sess, rec["job"]))
    boxes = json.loads(raw) if raw else {}
    if not boxes:
        return {"error": "that round is not ready"}
    x, y = float(x), float(y)
    texts = {t["id"]: t["text"] for t in rec["items"]}
    hit = next((i for i, b in boxes.items() if _inside(b, x, y)), None)
    if hit is None:
        near = min((_distance(b, x, y) for b in boxes.values()), default=9999)
        return {"hit": False, "distance": round(near),
                "hint": "boiling" if near < 80 else "warm" if near < 200
                        else "cold" if near < 400 else "freezing"}
    who = _clean(name, 14) or "someone"
    ms = int(time.time() * 1000)
    first = _kv.cmd("HSETNX", _room_key(sess), f"w:{rid}:{hit}", f"{ms}|{voter}|{who}")
    out = {"hit": True, "distance": 0, "item": hit, "text": texts.get(hit, ""),
           "box": boxes[hit], "first": bool(first)}
    if not first:
        held = _kv.cmd("HGET", _room_key(sess), f"w:{rid}:{hit}") or ""
        out["taken_by"] = held.split("|", 2)[-1]
    return out


def advance(sess):
    """Move a finished round along - publish it, put it up - but never start one.

    Phones call this while something is in flight, so a room with no projector and no
    ticker still sees its picture. A lock keeps it to one publisher: without it every phone
    would download and re-upload the same picture at the same moment.
    """
    st = state(sess)
    did = []
    if st["drawing"]:
        if _kv.cmd("SET", f"rm:{sess}:publock", "held", "NX", "PX", 25_000) is not None:
            try:
                got = publish(sess, st["drawing"][0]["id"])
                did.append({"publish": st["drawing"][0]["id"], "state": got.get("state", "running")})
            finally:
                _kv.cmd("DEL", f"rm:{sess}:publock")
            st = state(sess)
    if st["can_show"] and st["ready"]:
        did.append({"show": show(sess, st["ready"][0]["id"])["shown"]})
    return {"did": did}


def conduct(sess):
    """One tick: move a finished round along. Nothing here starts a round any more - that is
    the Submit button on the screen page, and only that (Vinh, 2026-09-18: "get rid of the
    30s automatic queue")."""
    out = advance(sess)
    out["queue"] = len(state(sess)["queue"])
    return out


def reset(sess):
    mode = get_mode(sess)
    keys = [_room_key(sess)] + [k for k in _kv.scan(f"rm:{sess}:*")
                                if not k.endswith(KEEP_ON_RESET)]
    if keys:
        _kv.cmd("DEL", *keys)
    urls = [b["url"] for b in _blob.listing(f"rm/{sess}/")]
    for i in range(0, len(urls), 100):
        _blob.delete(urls[i:i + 100])
    return {"cleared": len(keys), "pictures": len(urls), "mode": mode}
