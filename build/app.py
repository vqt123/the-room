"""Steal the Moves. One page: YouTube link + timestamp + your photo -> you doing that move.

Start:  ./start.sh   (opens http://127.0.0.1:8787)

How it runs, with no configuration:
  - the frame is grabbed on this Mac by darwin_bin/ytframe (YouTube does not block a home address), then the swap runs on
    Comfy Cloud with the key in ~/.config/comfy/api_key. About 20-30 s.
  - by default (the switch on the page) the URL goes to the serverless deployment in endpoint.txt instead, where the
    private YouTubeFrame node does the grab inside the cloud (works for direct video URLs; for YouTube links it needs a
    cookies.txt in the pack). If the cloud grab fails the page falls back to the local grab automatically and says so.
"""
import base64, json, os, subprocess, tempfile, threading, time, uuid, webbrowser
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from comfy_sdk import Comfy
from make_workflow import prompt_for, DEFAULT_WHO

HERE = os.path.dirname(os.path.abspath(__file__))
YTFRAME = os.path.join(HERE, "darwin_bin", "ytframe")
PORT = int(os.environ.get("PORT", "8787"))
JOBS = {}  # id -> {"state", "step", "started", "frame_b64", "result_b64", "error", "where"}


def _read(name):
    p = os.path.join(HERE, name)
    return open(p).read().strip() if os.path.exists(p) else ""


def cloud_key():
    k = os.environ.get("COMFY_API_KEY", "").strip()
    if k:
        return k
    p = os.path.expanduser("~/.config/comfy/api_key")
    return open(p).read().strip() if os.path.exists(p) else None


def endpoint_url():
    return os.environ.get("COMFY_BASE_URL", "").strip() or _read("endpoint.txt")


def endpoint_key():
    k = os.environ.get("COMFY_ENDPOINT_KEY", "").strip()
    if k:
        return k
    try:
        from comfy_cli import credentials
        s = credentials.get_session(refresh=True)
        return s.access_token if s and s.access_token else None
    except Exception:  # noqa: BLE001
        return None


def grab_frame_locally(url, ts):
    out = os.path.join(tempfile.gettempdir(), f"stm_{uuid.uuid4().hex}.png")
    p = subprocess.run([YTFRAME, "-url", url, "-time", ts, "-out", out], capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip().replace("ytframe: ", ""))
    return out


def run_job(job_id, url, ts, who, photo, photo_name, cloud_grab):
    j = JOBS[job_id]
    try:
        who = who or DEFAULT_WHO
        if cloud_grab and endpoint_url():
            j["step"] = "asking the cloud to grab the frame and do the swap (a cold worker takes a few minutes)"
            os.environ["COMFY_BASE_URL"] = endpoint_url()
            try:
                client = Comfy(api_key=endpoint_key(), timeout=180.0, client_info="steal-the-moves")
                wf = client.workflows.from_file(os.path.join(HERE, "workflow_api.json"))
                wf.set_input("1", "url", url)
                wf.set_input("1", "timestamp", ts)
                wf.set_input("2", "image", client.assets.from_bytes(photo, filename=photo_name or "profile.png"))
                wf.set_input("10", "prompt", prompt_for(who))
                job = client.run(wf, timeout=900)
                j["result_b64"] = base64.b64encode(job.get_outputs("17")[0].to_bytes()).decode()
                j["where"] = "frame grabbed inside the endpoint by the private node; swap on the endpoint"
                j["state"] = "done"
                return
            except Exception as e:  # noqa: BLE001
                j["note"] = f"cloud grab failed ({str(e)[:140]}), falling back to a local grab"
            finally:
                os.environ["COMFY_BASE_URL"] = ""
        j["step"] = "grabbing the frame on this Mac"
        frame = grab_frame_locally(url, ts)
        j["frame_b64"] = base64.b64encode(open(frame, "rb").read()).decode()
        # The swap still runs on the Dev Platform endpoint (LoadImage graph, same models). Comfy Cloud is used only
        # when there is no endpoint configured at all.
        if endpoint_url():
            j["step"] = "swapping you in on the Dev Platform endpoint"
            os.environ["COMFY_BASE_URL"] = endpoint_url()
            client = Comfy(api_key=endpoint_key(), timeout=180.0, client_info="steal-the-moves")
            where = "frame grabbed on this Mac; swap on the Dev Platform endpoint"
        else:
            j["step"] = "swapping you in (Comfy Cloud; no endpoint configured)"
            os.environ["COMFY_BASE_URL"] = ""
            client = Comfy(api_key=cloud_key(), client_info="steal-the-moves")
            where = "frame grabbed on this Mac; swap on Comfy Cloud (no endpoint configured)"
        try:
            wf = client.workflows.from_file(os.path.join(HERE, "workflow_cloud_test.json"))
            wf.set_input("1", "image", client.assets.from_file(frame))
            wf.set_input("2", "image", client.assets.from_bytes(photo, filename=photo_name or "profile.png"))
            wf.set_input("10", "prompt", prompt_for(who))
            job = client.run(wf, timeout=900)
        finally:
            os.environ["COMFY_BASE_URL"] = ""
        j["result_b64"] = base64.b64encode(job.get_outputs("17")[0].to_bytes()).decode()
        j["where"] = where
        j["state"] = "done"
    except Exception as e:  # noqa: BLE001
        j["error"] = str(e)[:400]
        j["state"] = "error"
    finally:
        j["seconds"] = round(time.time() - j["started"], 1)


PAGE = r"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Steal the Moves</title>
<style>
:root{--bg:#0f1115;--card:#181b22;--ink:#e8eaf0;--mut:#9aa3b2;--acc:#7c5cff;--ok:#3ddc97;--bad:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:32px 20px}h1{font-size:34px;margin:0 0 4px}.sub{color:var(--mut);margin:0 0 24px}
.grid{display:grid;grid-template-columns:380px 1fr;gap:24px}@media(max-width:820px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border-radius:14px;padding:20px}label{display:block;font-weight:600;margin:14px 0 6px}label:first-child{margin-top:0}
input[type=text]{width:100%;padding:10px 12px;border-radius:8px;border:1px solid #2a2f3a;background:#0f1115;color:var(--ink);font-size:15px}
.drop{border:2px dashed #2a2f3a;border-radius:10px;padding:18px;text-align:center;color:var(--mut);cursor:pointer}.drop.has{border-color:var(--acc);color:var(--ink)}
.drop img{max-height:140px;border-radius:8px;display:block;margin:0 auto 8px}
button{margin-top:18px;width:100%;padding:13px;border:0;border-radius:10px;background:var(--acc);color:#fff;font-size:17px;font-weight:700;cursor:pointer}
button:disabled{opacity:.5;cursor:default}.small{font-size:13px;color:var(--mut)}.row{display:flex;gap:10px;align-items:center}
.status{margin-top:14px;min-height:22px}.status.err{color:var(--bad)}.status.ok{color:var(--ok)}
.out{display:grid;grid-template-columns:1fr 1fr;gap:14px}.out img{width:100%;border-radius:10px;background:#000}.out h3{margin:0 0 8px;font-size:14px;color:var(--mut);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.big{grid-column:1/-1}.hist{margin-top:18px;display:grid;grid-template-columns:repeat(auto-fill,minmax(120px,1fr));gap:8px}.hist img{width:100%;border-radius:8px;cursor:pointer}
.spin{display:inline-block;width:14px;height:14px;border:2px solid var(--mut);border-top-color:var(--ink);border-radius:50%;animation:s .8s linear infinite;vertical-align:-2px;margin-right:8px}@keyframes s{to{transform:rotate(360deg)}}
.toggle{display:flex;align-items:center;gap:10px;margin-top:14px;color:var(--mut);font-size:14px}.toggle input{width:auto}
a{color:var(--acc)}
</style>
<div class=wrap>
<h1>Steal the Moves</h1>
<p class=sub>A YouTube link, a moment, your photo. You, doing that move.</p>
<div class=grid>
<form class=card id=f>
<label>YouTube link</label><input type=text name=url id=url placeholder="https://www.youtube.com/watch?v=..." required>
<div class=small style="margin-top:6px">Try: <a href=# data-u="https://www.youtube.com/watch?v=i0OQmw5WSIQ" data-t="2:10" data-w="">body wave (one person)</a> ·
<a href=# data-u="https://www.youtube.com/watch?v=Ewqq-3xJFdI" data-t="3:20" data-w="the woman in the yellow top dancing in the centre">African dance (four people)</a></div>
<label>Moment (mm:ss)</label><input type=text name=timestamp id=ts value="0:05" required>
<label>Your photo</label>
<div class=drop id=drop><span id=dropt>Drop a photo here or click to choose</span><input type=file id=file name=profile accept="image/*" hidden></div>
<label>Which person? <span class=small>(only if the frame has several)</span></label><input type=text name=who id=who placeholder="the main person in the centre of the frame">
<div class=toggle><input type=checkbox id=cloud name=cloud_grab checked> <span>Grab the frame inside the cloud with the private node (default). Falls back to a grab on this Mac if the cloud is refused.</span></div>
<button id=go>Do the move</button>
<div class=status id=st></div>
</form>
<div class=card>
<div class=out id=out><div class=big><p class=small>Results appear here. First run takes about 30 seconds.</p></div></div>
<div class=hist id=hist></div>
</div></div></div>
<script>
const $=s=>document.querySelector(s);let photo=null,timer=null;
$('#drop').onclick=()=>$('#file').click();
$('#file').onchange=e=>setPhoto(e.target.files[0]);
['dragover','dragenter'].forEach(ev=>$('#drop').addEventListener(ev,e=>{e.preventDefault();}));
$('#drop').addEventListener('drop',e=>{e.preventDefault();setPhoto(e.dataTransfer.files[0]);});
function setPhoto(f){if(!f)return;photo=f;const r=new FileReader();r.onload=()=>{$('#drop').classList.add('has');$('#drop').innerHTML=`<img src="${r.result}"><span>${f.name}</span><input type=file id=file hidden>`;$('#drop').querySelector('#file').onchange=e=>setPhoto(e.target.files[0]);};r.readAsDataURL(f);}
document.querySelectorAll('a[data-u]').forEach(a=>a.onclick=e=>{e.preventDefault();$('#url').value=a.dataset.u;$('#ts').value=a.dataset.t;$('#who').value=a.dataset.w;});
$('#f').onsubmit=async e=>{e.preventDefault();if(!photo){status('Add a photo first.','err');return;}
const fd=new FormData();fd.append('url',$('#url').value.trim());fd.append('timestamp',$('#ts').value.trim());fd.append('who',$('#who').value.trim());fd.append('cloud_grab',$('#cloud').checked?'1':'');fd.append('profile',photo,photo.name);
$('#go').disabled=true;status('<span class=spin></span>starting');const r=await fetch('/api/run',{method:'POST',body:fd});const {id}=await r.json();poll(id,Date.now());};
function status(h,c){const s=$('#st');s.className='status '+(c||'');s.innerHTML=h;}
async function poll(id,t0){const r=await fetch('/api/job/'+id);const j=await r.json();const secs=((Date.now()-t0)/1000).toFixed(0);
if(j.state==='running'){status(`<span class=spin></span>${j.step} · ${secs}s`);if(j.frame_b64&&!$('#fr'))show(j,false);setTimeout(()=>poll(id,t0),1000);return;}
$('#go').disabled=false;
if(j.state==='error'){status('Failed: '+j.error,'err');return;}
show(j,true);status(`Done in ${j.seconds}s. ${j.where}${j.note?' ('+j.note+')':''}`,'ok');
const im=document.createElement('img');im.src='data:image/png;base64,'+j.result_b64;im.onclick=()=>window.open(im.src);$('#hist').prepend(im);}
function show(j,final){const fr=j.frame_b64?`<div><h3>The move</h3><img id=fr src="data:image/png;base64,${j.frame_b64}"></div>`:`<div><h3>The move</h3><p class=small>grabbed inside the endpoint</p></div>`;
const rs=j.result_b64?`<div><h3>You</h3><img src="data:image/png;base64,${j.result_b64}"><p class=small><a download="steal-the-moves.png" href="data:image/png;base64,${j.result_b64}">download</a></p></div>`:`<div><h3>You</h3><p class=small>working...</p></div>`;$('#out').innerHTML=fr+rs;}
</script></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", code=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def _form(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\nMIME-Version: 1.0\r\n\r\n" + self.rfile.read(length)
        msg = BytesParser(policy=HTTP).parsebytes(raw)
        fields, files = {}, {}
        for part in msg.iter_parts():
            name = part.get_param("name", header="content-disposition")
            fn = part.get_filename()
            payload = part.get_payload(decode=True) or b""
            if fn:
                files[name] = (fn, payload)
            else:
                fields[name] = payload.decode("utf-8", "replace")
        return fields, files

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._send(PAGE)
        if path.startswith("/api/job/"):
            j = JOBS.get(path.rsplit("/", 1)[1])
            return self._send(json.dumps(j or {"state": "error", "error": "no such job"}), "application/json")
        self._send("not found", code=404)

    def do_POST(self):
        if urlparse(self.path).path != "/api/run":
            return self._send("not found", code=404)
        fields, files = self._form()
        name, data = files.get("profile", ("profile.png", b""))
        if not data:
            return self._send(json.dumps({"error": "no photo"}), "application/json", 400)
        job_id = uuid.uuid4().hex[:8]
        JOBS[job_id] = {"state": "running", "step": "queued", "started": time.time(), "frame_b64": None, "result_b64": None, "error": None, "where": "", "note": ""}
        threading.Thread(target=run_job, args=(job_id, fields.get("url", "").strip(), fields.get("timestamp", "0").strip() or "0",
                                               fields.get("who", "").strip(), data, name, bool(fields.get("cloud_grab"))), daemon=True).start()
        self._send(json.dumps({"id": job_id}), "application/json")


if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    print("Steal the Moves ->", url, "| endpoint:", endpoint_url() or "(none)", "| cloud key:", "yes" if cloud_key() else "MISSING ~/.config/comfy/api_key")
    if os.environ.get("NO_BROWSER", "") == "":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
