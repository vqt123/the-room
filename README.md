# The Room

A crowd game on top of a serverless ComfyUI endpoint. Everyone in the room types things on
their phone; one GPU turns them into a picture on the big screen.

Built in a day for an internal Comfy hackathon (2026-09-18). It is a hackathon project: the
code is honest, the edges are rough, and the interesting parts are the design notes below.

## Kill or Save (the game for the demo)

Open `kill-screen.html` on the projector and `kill.html` on every phone. One hero, **Yoland**,
from a reference photo. Everyone who joins is put on a **secret side**, team Kill or team Save,
and told only their own. The prompts are Team 6's (`build/team_prompts.txt`, kept verbatim in
`vercel-app/api/prompts/`), and the engine is what they describe:

1. **Setup, once.** The text LLM (Claude, reached through Comfy's Router at
   `api.comfy.org/proxy/anthropic/v1/messages` with a production key) reads the hero's photo
   and writes a visual description plus round 1's scene: a place and an activity, no obstacle.
   The screen page triggers it the first time it loads.
2. **Answer.** Players read the scene and each lock in one **noun** and one **verb**, editable
   until Go. Every phone and the projector show every word, alphabetically, with no names and
   no sides.
3. **Go** (no timer; the host presses it). The **Smash** call gets the Kill pool, the Save pool,
   the story so far, the continuity and a fixed verdict, and returns JSON: title, story, one
   plan per panel (visual, caption, bubbles), a single `page_prompt`, continuity and the next
   round's scene. The next scene goes up at once, so people answer round 2 while page 1 draws.
4. **Render, one Comfy job on the endpoint.** The default (`render: panels`) draws each of the
   four panel plans as its own picture on the GPU, with the hero's photo as the reference
   image and no lettering; the Smash is also asked for a narration line per panel, which the
   pages print under each picture. The other path (`render: page`) is the artifact's: a
   **Nano Banana** API node inside the graph draws the whole page (panels, gutters, lettering)
   from `page_prompt`, with the hero's photo as the first reference image and, from round 2,
   the previous page as the second (both scaled to the page shape and batched, because the
   node takes one image input).

Verdict rule: every round LIVES except the final round, which DIES; the host can override the
next round with the LIVES / DIES buttons on the screen. Rounds per game default to 2. Measured:
Setup 5 s, Smash 40-55 s on Sonnet, page 10-15 s. `killctl.sh` drives it from a shell.

## The Room (the earlier modes)

Open `screen.html` on a projector and `room.html` on every phone. People type a **verb** and a
**noun** ("juggle" + "walrus"); the server folds each pair into a line that reads naturally
(*a walrus juggling*) and puts it on a shared list. Whoever is at the projector presses
**Submit**: every line on the list goes into ONE picture, drawn in a randomly described scene,
and the list is cleared. About 11 seconds a round.

Three other modes are in the code and selectable per session:

| mode | what happens on Submit |
|---|---|
| `story` | everyone's verb + noun go to an **LLM node inside the job**, which writes a four-panel comic; each panel is drawn as its own picture (with the session's hero photo as the reference, if one is set) and the pages stitch the strip |
| `pairs` | everyone's verb + noun in one picture, no LLM |
| `find` | each thing on the list is drawn, then **hidden** in a busy scene; the room races to tap it; each thing goes to whoever finds it first |
| `mash` | every word on the list in one picture, no hiding |
| `vn` / `vnfind` | one vote each on verbs and nouns; the winning pair is drawn (or drawn and hidden) |

## How it is put together

```
phones / projector  --->  Vercel (static pages + Python functions)
                                |            |             |
                             Redis        Blob store     QStash (optional clock)
                          (room state)   (the pictures)
                                |
                     Comfy Dev Platform endpoint: a serverless GPU running
                     ComfyUI with one private custom-node pack (build/ytmove_pack)
```

- **`vercel-app/`** - the site. `api/_room.py` is the game; `api/_kv.py` is Upstash Redis over
  REST with nothing but the standard library; `api/_blob.py` is Vercel Blob for the pictures;
  `api/tick.py` is an optional QStash-driven clock (not used by the live mode, which only
  moves on the button).
- **`build/`** - everything that runs on the GPU. `make_find_workflow.py` composes the
  ComfyUI graph per round (there is no fixed workflow file; the graph depends on how many
  things were submitted). `ytmove_pack/` is the private node pack. `release.sh` /
  `swap_deployment.sh` push it to the Comfy Dev Platform and swap the live endpoint.

**The LLM is in the graph, not in the web app.** `story` mode uses ComfyUI's `GeminiNode` - one
of the partner "API nodes" that route through Comfy's API - wired straight into the text
encoder through a core `StringConcatenate`. The whole round is one job on the endpoint: LLM
call, prompt join, sampler pass. The story also rides out as the picture's filename, which is
how the room gets to read it. One catch worth knowing: the node calls `api.comfy.org`, which
only knows production keys, so the job carries a separate `api_key_comfy_org` in `extra_data`
(the SDK's `submit(workflow, api_key=...)`) even when the endpoint itself is on staging.

The pictures come from Qwen-Image-Edit-2511 with the 4-step Lightning LoRA, handed a blank
canvas: an edit model asked to edit nothing draws a whole new picture, which is why the
whole thing needs no extra models.

## Things worth knowing

**The state is one Redis hash per room, and a phone's poll is one `HGETALL`.** The two moments
that could race are Redis primitives, not code: `HSETNX` settles who found a thing first, and
`SET NX PX` is the lock that keeps two Submit presses to one round. In the voting modes a
ballot is keyed by the *voter* (`V:<kind>:<voter>` = item), so changing your vote is one
atomic overwrite and nobody can be counted twice.

**Drawing is pipelined behind the picture that is up.** A round takes 11-46 s depending on
mode and count; the next one is drawn while the room is still on the current one, and its
URL is handed out a few seconds early so every browser has it cached before the flip. In
the hide-and-find mode that URL is the answer sheet, so it is held back until the last nine
seconds.

**The answer never reaches a phone.** The private `HideIt` node places each thing and returns
its box; the box rides out as the saved file's *name*, the server reads it into a Redis key
no state read touches, and taps are scored server-side. Each thing's answer rides on its own
thumbnail, which is how N answers come out of one job without a node that concatenates.

**`api/queue.py` shadowed the standard library's `queue`.** Every handler put `api/` first on
`sys.path` to reach its siblings; `concurrent.futures` then got the Find It endpoint instead
of `queue`. It is appended now. Same shape as naming a file `platform.py`.

**QStash wants the destination URL unencoded** on its path; percent-encoding it gets
"invalid destination url: endpoint has invalid scheme".

## The honest part: does any of this need a custom node?

Mostly no, and the repo says so rather than pretending. The test to apply: *given the job's
inputs and outputs, can code outside the box reproduce the result?* A frame grab is a
download; a VHS tape is ffmpeg on the output; hiding a thing and reading back where it went
is two jobs with a function in between. A stock `SaveLatent` even comes back through the
API, so sliced sampling can chain across jobs.

What a private node actually buys is **hops**: it turns N jobs into one, on one warm GPU
with the models loaded once, and each hop out here costs 5-8 s plus a cold-start lottery.
That matters exactly when the loop's length is unknown - generate, check with a program,
fix, repeat - because such a chain cannot even be laid out in advance. `Seamless` in the
pack is that shape (a texture that measures its own wrap-around seam and repairs it until it
tiles); mechanically it works, but with these settings it did not converge, and it is parked.

## Running it

You need a Comfy Dev Platform workspace, a Vercel project with Upstash Redis and Vercel Blob
attached (both are one click in the Vercel marketplace), and these env vars on the project:

| var | what |
|---|---|
| `COMFY_BASE_URL` | the deployment's endpoint URL |
| `COMFY_API_KEY` | a key that endpoint accepts |
| `APP_PASSWORD` | the shared room password; every round spends GPU time |
| `KV_REST_API_URL`, `KV_REST_API_TOKEN` | from the Redis store |
| `BLOB_READ_WRITE_TOKEN` | from the Blob store |
| `COMFY_PARTNER_API_KEY` | a **production** Comfy API key with credits, for the LLM node in `story` mode and in Kill or Save |
| `QSTASH_TOKEN` | only if you want the optional clock |

Then, roughly: `cd build && ./release.sh` to bake the pack into a release and deploy it,
`cd ../vercel-app && ./sync_vercel.sh` to point the site at it, and `./roomctl.sh mode pairs
main` to pick the game. `roomctl.sh state|reset|add|say|tick|mode` drives a room from a
shell. One warm GPU means one room at a time.

If Vercel itself is down (it was, mid-hackathon: an incident on triggering deployments), the
same code runs on a laptop: `vercel env pull prod.env`, then `python local_server.py --env
prod.env --port 8791` serves the pages and every `api/<name>.py` at `/api/<name>`, and
`cloudflared tunnel --url http://127.0.0.1:8791` gives phones a public URL. Same Redis, same
Blob store, same endpoint.

The Go sources for the binary-backed nodes are in `build/ytframe`, `build/hideit` and
`build/vhstape`; the binaries and the bundled ffmpeg / yt-dlp are not in the repo.
