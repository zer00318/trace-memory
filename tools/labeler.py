#!/usr/bin/env python3
"""F1 labeler — zero-setup UI.  Run:  python3 tools/labeler.py

Opens your browser already loaded with every frame in gold/frames/.
Draw a box over readable text, type the text, done — every change autosaves
to gold/frames/gold_labels.json. No file pickers, no export button.
Close the tab / Ctrl-C when finished; re-running resumes where you stopped.
"""
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRAMES = ROOT / "gold" / "frames"
LABELS = FRAMES / "gold_labels.json"
PORT = 8901  # never 8765 — that port is weather

HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>TRACE gold labeler</title>
<style>
 body{font:14px -apple-system,sans-serif;margin:0;display:flex;height:100vh}
 #side{width:330px;padding:12px;overflow-y:auto;border-right:1px solid #ccc;flex-shrink:0}
 #main{flex:1;overflow:auto;background:#1e1e1e}
 canvas{cursor:crosshair;display:block}
 .region{padding:4px 0;border-bottom:1px solid #eee;font-size:12px}
 button{margin:2px;font-size:13px}
 #cls{color:#0a66cc;font-weight:600;margin:6px 0}
 #st{color:#0a0;font-weight:600}
 #prog{font-weight:600}
</style></head><body>
<div id="side">
  <h3>F1 labeler</h3>
  <p>Draw boxes over READABLE text; type it EXACTLY (case, punctuation).
  3&ndash;8 boxes on text-heavy frames; 0&ndash;2 on video/sparse ones.
  Everything autosaves &mdash; just close when done. Arrow keys switch frames.</p>
  <div>
    <button id="prev">&#9664; prev</button>
    <span id="pos">0/0</span>
    <button id="next">next &#9654;</button>
    <button id="undo">undo box</button>
    <span id="st"></span>
  </div>
  <div id="prog"></div>
  <div id="cls"></div>
  <div id="regions"></div>
</div>
<div id="main"><canvas id="cv"></canvas></div>
<script>
let frames=[], idx=0, img=new Image(), scale=1, drag=null;
const cv=document.getElementById('cv'), ctx=cv.getContext('2d');
fetch('/manifest').then(r=>r.json()).then(m=>{
  frames=m.map(f=>({name:f.name,stem:f.stem,sidecar:f.sidecar,
    regions:(f.sidecar&&f.sidecar.text_regions)||[]}));
  idx=0; show();
});
function save(){
  const out={};
  for(const f of frames) out[f.stem]=Object.assign({},f.sidecar,{text_regions:f.regions});
  fetch('/save',{method:'POST',body:JSON.stringify(out)}).then(()=>{
    document.getElementById('st').textContent='saved';
    setTimeout(()=>document.getElementById('st').textContent='',900);
  });
  progress();
}
function progress(){
  const n=frames.filter(f=>f.regions.length>0).length;
  document.getElementById('prog').textContent=n+'/'+frames.length+' frames have labels';
}
function show(){
  if(!frames.length) return;
  const f=frames[idx];
  img=new Image();
  img.onload=()=>{
    scale=Math.min(1,(window.innerWidth-350)/img.width,(window.innerHeight-20)/img.height);
    cv.width=img.width*scale; cv.height=img.height*scale; redraw();
  };
  img.src='/frames/'+f.name;
  document.getElementById('pos').textContent=(idx+1)+'/'+frames.length;
  document.getElementById('cls').textContent=f.name+' — '+((f.sidecar&&f.sidecar.class)||'?')+' · '+((f.sidecar&&f.sidecar.app)||'?');
  renderRegions(); progress();
}
function redraw(){
  ctx.drawImage(img,0,0,cv.width,cv.height);
  ctx.lineWidth=2; ctx.strokeStyle='#f30'; ctx.fillStyle='rgba(255,60,0,.12)';
  for(const r of frames[idx].regions){
    ctx.strokeRect(r.x*scale,r.y*scale,r.w*scale,r.h*scale);
    ctx.fillRect(r.x*scale,r.y*scale,r.w*scale,r.h*scale);
  }
  if(drag){ctx.strokeStyle='#0c6';ctx.strokeRect(drag.x0,drag.y0,drag.x1-drag.x0,drag.y1-drag.y0);}
}
cv.onmousedown=e=>{const b=cv.getBoundingClientRect();
  drag={x0:e.clientX-b.left,y0:e.clientY-b.top,x1:e.clientX-b.left,y1:e.clientY-b.top};};
cv.onmousemove=e=>{if(!drag)return;const b=cv.getBoundingClientRect();
  drag.x1=e.clientX-b.left;drag.y1=e.clientY-b.top;redraw();};
cv.onmouseup=()=>{
  if(!drag)return; const d=drag; drag=null;
  const x=Math.min(d.x0,d.x1)/scale, y=Math.min(d.y0,d.y1)/scale,
        w=Math.abs(d.x1-d.x0)/scale, h=Math.abs(d.y1-d.y0)/scale;
  if(w<8||h<8){redraw();return;}
  const text=prompt('Exact text inside this box (empty = cancel):','');
  if(text){frames[idx].regions.push({x:Math.round(x),y:Math.round(y),
    w:Math.round(w),h:Math.round(h),text:text}); save();}
  redraw(); renderRegions();
};
function renderRegions(){
  document.getElementById('regions').innerHTML=frames[idx].regions.map((r,i)=>
    '<div class="region">#'+i+' ['+r.x+','+r.y+' '+r.w+'x'+r.h+'] '+
    r.text.slice(0,60).replace(/</g,'&lt;')+
    ' <button onclick="delRegion('+i+')">x</button></div>').join('');
}
function delRegion(i){frames[idx].regions.splice(i,1);redraw();renderRegions();save();}
document.getElementById('undo').onclick=()=>{frames[idx].regions.pop();redraw();renderRegions();save();};
document.getElementById('prev').onclick=()=>{idx=Math.max(0,idx-1);show();};
document.getElementById('next').onclick=()=>{idx=Math.min(frames.length-1,idx+1);show();};
window.onkeydown=e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='TEXTAREA')return;
  if(e.key==='ArrowRight')document.getElementById('next').click();
  if(e.key==='ArrowLeft')document.getElementById('prev').click();
};
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send(200, HTML.encode(), "text/html")
        elif self.path == "/manifest":
            labels = json.loads(LABELS.read_text()) if LABELS.exists() else {}
            items = []
            for png in sorted(FRAMES.glob("*.png")):
                stem = png.stem
                sidecar = {}
                j = FRAMES / (stem + ".json")
                if j.exists():
                    sidecar = json.loads(j.read_text())
                if stem in labels:  # resume: saved labels win
                    sidecar = labels[stem]
                items.append({"name": png.name, "stem": stem, "sidecar": sidecar})
            self._send(200, json.dumps(items).encode(), "application/json")
        elif self.path.startswith("/frames/"):
            p = FRAMES / Path(self.path).name
            if p.suffix == ".png" and p.exists():
                self._send(200, p.read_bytes(), "image/png")
            else:
                self._send(404, b"", "text/plain")
        else:
            self._send(404, b"", "text/plain")

    def do_POST(self):
        if self.path == "/save":
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n))
            LABELS.write_text(json.dumps(data, indent=2))
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"", "text/plain")


def main():
    if not any(FRAMES.glob("*.png")):
        print("No frames yet — run  python3 tools/gold_capture.py  first.")
        print(f"(looked in {FRAMES})")
        return
    print(f"Labeler: http://localhost:{PORT}  — autosaves to {LABELS}")
    print("Close the tab / Ctrl-C here when done.")
    webbrowser.open(f"http://localhost:{PORT}")
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
