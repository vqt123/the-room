# The Room

A crowd game on top of a serverless ComfyUI endpoint. Everyone in the room types things on
their phone; one GPU turns them into a picture on the big screen.

Built in a day for an internal Comfy hackathon (2026-09-18). It is a hackathon project: the
code is honest, the edges are rough, and the interesting parts are the design notes below.

## The game, as it ended up

Open `screen.html` on a projector and `room.html` on every phone. People type a **verb** and a
**noun** ("juggle" + "walrus"); the server folds each pair into a line that reads naturally
(*a walrus juggling*) and puts it on a shared list. Whoever is at the projector presses
**Submit**: every line on the list goes into ONE picture, drawn in a randomly described scene,
and the list is cleared. About 11 seconds a round.

Three other modes are in the code and selectable per session:

| mode | what happens on Submit |
|---|---|
| `pairs` | everyone's verb + noun in one picture (the live one) |
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
| `QSTASH_TOKEN` | only if you want the optional clock |

Then, roughly: `cd build && ./release.sh` to bake the pack into a release and deploy it,
`cd ../vercel-app && ./sync_vercel.sh` to point the site at it, and `./roomctl.sh mode pairs
main` to pick the game. `roomctl.sh state|reset|add|say|tick|mode` drives a room from a
shell. One warm GPU means one room at a time.

The Go sources for the binary-backed nodes are in `build/ytframe`, `build/hideit` and
`build/vhstape`; the binaries and the bundled ffmpeg / yt-dlp are not in the repo.
