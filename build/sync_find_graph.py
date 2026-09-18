"""Copy make_find_workflow.py into vercel-app/api/_findgraph.py.

The app used to carry a hand-written twin of the prompts plus two frozen JSON graphs. It
now needs `build()` itself, because a round's graph depends on how many things the room
submitted, so the generator is copied verbatim instead. It is pure standard library.
"""
import os, re

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "make_find_workflow.py")
OUT = os.path.join(HERE, "..", "vercel-app", "api", "_findgraph.py")

src = open(SRC).read()
src = src[:src.index('if __name__ == "__main__":')].rstrip() + "\n"
src = re.sub(r'^"""', '"""Copied from build/make_find_workflow.py by build/sync_find_graph.py '
                     '-- do not edit by hand.\n\n', src, count=1)
src += """

# The single-thing graphs the older Find It page still posts.
TYPED = graph(upload=False)
UPLOAD = graph(upload=True)
SCENE_NAMES = [n for n, _ in SCENES]


def scene_index(name, rng=None):
    \"\"\"The index of a named scene, or a random one when the name is empty or unknown.\"\"\"
    for i, (n, _) in enumerate(SCENES):
        if n == name:
            return i
    import random as _r
    return (rng or _r).randrange(len(SCENES))
"""

open(OUT, "w").write(src)
print("wrote", os.path.relpath(OUT, HERE))
