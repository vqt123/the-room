"""Pull a Figma file's design into a folder: the frame tree, the colours, the text styles, and
every exported image asset, so a page can be built against real values instead of a screenshot.

    python figma_pull.py <file-key-or-url> [--out ../vercel-app/public/assets] [--node 0:1]

The token is a Figma personal access token at ~/.config/figma/token (mode 0600, never in the
repo). It needs only file read access.

Writes into --out:
    design.json     the raw file JSON (trimmed to the chosen node)
    design.md       a readable summary: frames, their sizes, colours, text and fonts
    <name>.png      one export per top-level frame (2x), named after the frame
    <name>.svg      vector exports for frames whose name ends in .svg
"""
import argparse, json, os, re, sys, urllib.parse, urllib.request

API = "https://api.figma.com/v1"
TOKEN_PATH = os.path.expanduser("~/.config/figma/token")


def token():
    if os.environ.get("FIGMA_TOKEN"):
        return os.environ["FIGMA_TOKEN"].strip()
    if not os.path.exists(TOKEN_PATH):
        sys.exit(f"no Figma token at {TOKEN_PATH} (see the module docstring)")
    return open(TOKEN_PATH).read().strip()


def get(path, **params):
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-Figma-Token": token()})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        sys.exit(f"figma {e.code}: {e.read().decode(errors='replace')[:300]}")


def file_key(s):
    m = re.search(r"/(?:file|design)/([A-Za-z0-9]+)", s or "")
    return m.group(1) if m else s


def hexof(paint):
    c = paint.get("color") or {}
    if not c:
        return None
    r, g, b = (round(c.get(k, 0) * 255) for k in ("r", "g", "b"))
    a = paint.get("opacity", c.get("a", 1))
    h = f"#{r:02x}{g:02x}{b:02x}"
    return h if a in (None, 1) else f"{h} @ {round(a, 2)}"


def walk(node, depth=0, out=None, colours=None, fonts=None, texts=None):
    out = [] if out is None else out
    colours = {} if colours is None else colours
    fonts = {} if fonts is None else fonts
    texts = [] if texts is None else texts
    box = node.get("absoluteBoundingBox") or {}
    size = f"{round(box.get('width', 0))}x{round(box.get('height', 0))}" if box else ""
    out.append(f"{'  ' * depth}- {node.get('name')} [{node.get('type')}] {size}".rstrip())
    for key in ("fills", "strokes"):
        for paint in node.get(key) or []:
            if paint.get("visible") is False or paint.get("type") != "SOLID":
                continue
            h = hexof(paint)
            if h:
                colours.setdefault(h, set()).add(node.get("name"))
    st = node.get("style") or {}
    if st.get("fontFamily"):
        fonts.setdefault((st["fontFamily"], st.get("fontWeight"), round(st.get("fontSize", 0))), set()).add(
            node.get("name"))
    if node.get("type") == "TEXT" and node.get("characters"):
        texts.append((node.get("name"), node["characters"][:120]))
    for kid in node.get("children") or []:
        walk(kid, depth + 1, out, colours, fonts, texts)
    return out, colours, fonts, texts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--out", default="figma")
    ap.add_argument("--node", default=None, help="only this node id, e.g. 0:1 or 12-34")
    ap.add_argument("--scale", type=float, default=2)
    a = ap.parse_args()
    key = file_key(a.file)
    os.makedirs(a.out, exist_ok=True)

    if a.node:
        nid = a.node.replace("-", ":")
        doc = get(f"/files/{key}/nodes", ids=nid)
        roots = [v["document"] for v in doc["nodes"].values()]
        name = doc.get("name", key)
    else:
        doc = get(f"/files/{key}")
        roots = doc["document"].get("children") or []
        name = doc.get("name", key)

    json.dump({"name": name, "roots": roots}, open(os.path.join(a.out, "design.json"), "w"), indent=1)

    lines, colours, fonts, texts = [f"# {name}", ""], {}, {}, []
    frames = []
    for root in roots:
        kids = root.get("children") or [root]
        for f in kids:
            frames.append(f)
        tree, colours, fonts, texts = walk(root, 0, [], colours, fonts, texts)
        lines += tree + [""]
    lines += ["", "## Colours", ""]
    lines += [f"- `{h}` - {', '.join(sorted(list(who))[:4])}" for h, who in sorted(colours.items())]
    lines += ["", "## Type", ""]
    lines += [f"- {fam} {w or ''} {sz}px - {', '.join(sorted(list(who))[:4])}"
              for (fam, w, sz), who in sorted(fonts.items(), key=lambda kv: -kv[0][2])]
    lines += ["", "## Text", ""]
    lines += [f"- **{n}**: {t}" for n, t in texts[:80]]
    open(os.path.join(a.out, "design.md"), "w").write("\n".join(lines) + "\n")

    wanted = [f for f in frames if f.get("type") in ("FRAME", "COMPONENT", "INSTANCE", "GROUP")]
    if wanted:
        ids = ",".join(f["id"] for f in wanted[:40])
        for fmt in ("png", "svg"):
            pick = [f for f in wanted[:40] if (fmt == "svg") == f["name"].lower().endswith(".svg")]
            if not pick:
                continue
            got = get(f"/images/{key}", ids=",".join(f["id"] for f in pick), format=fmt, scale=a.scale)
            for f in pick:
                url = (got.get("images") or {}).get(f["id"])
                if not url:
                    continue
                safe = re.sub(r"[^a-z0-9]+", "-", f["name"].lower()).strip("-")[:48] or f["id"].replace(":", "-")
                path = os.path.join(a.out, f"{safe}.{fmt}")
                with urllib.request.urlopen(url, timeout=120) as r:
                    open(path, "wb").write(r.read())
                print("wrote", path)
        print(f"{len(wanted)} frames, {len(colours)} colours, {len(fonts)} text styles -> {a.out}/design.md")
    else:
        print("no frames found; see", os.path.join(a.out, "design.md"))


if __name__ == "__main__":
    main()
