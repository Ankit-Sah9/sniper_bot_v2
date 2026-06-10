"""
analysis/render_charts.py — Render charts.json into a self-contained,
TradingView-style multi-timeframe trade viewer (offline, no CDN).

Features in the generated HTML:
  * Candlestick chart drawn on <canvas> (green up / red down).
  * Timeframe switcher: 5m / 15m / 1h / 1D (resampled data, instant switch).
  * Trade markings like a real chart annotation:
      - entry price line (dashed)
      - green TARGET zone (entry -> target)
      - red STOP zone (entry -> stop)
      - exit marker (triangle) coloured by win/loss
      - swept liquidity level line
      - FVG band (shaded)
  * Pan (drag) and zoom (wheel / +/- buttons).
  * Prev/Next trade navigation + a trade list with result & P&L.
  * Crosshair with OHLC readout (top-left like TradingView).

Run AFTER build_charts.py:
  python analysis/render_charts.py
Then open analysis/output/chart_viewer.html
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
CHARTS_JSON = OUT_DIR / "charts.json"
HTML_OUT = OUT_DIR / "chart_viewer.html"


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Sniper Bot v2 — Trade Chart Viewer</title>
<style>
  :root{
    --bg:#0e1016; --panel:#131722; --line:#2a2e39; --txt:#d1d4dc; --muted:#787b86;
    --up:#26a69a; --down:#ef5350; --grid:#1c1f2a; --accent:#2962ff;
    --target:rgba(38,166,154,.16); --stop:rgba(239,83,80,.16);
    --target-line:#26a69a; --stop-line:#ef5350; --entry:#e0b84c; --sweep:#5b9cff;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
  .app{display:grid;grid-template-columns:260px 1fr;height:100vh}
  .side{background:var(--panel);border-right:1px solid var(--line);overflow-y:auto}
  .side h1{font-size:14px;margin:0;padding:14px 16px;border-bottom:1px solid var(--line)}
  .side .sub{padding:8px 16px;color:var(--muted);font-size:12px;border-bottom:1px solid var(--line)}
  .tlist{list-style:none;margin:0;padding:0}
  .tlist li{padding:9px 16px;border-bottom:1px solid var(--grid);cursor:pointer;display:flex;
            justify-content:space-between;gap:8px;align-items:center}
  .tlist li:hover{background:#171b27}
  .tlist li.active{background:#1c2230;border-left:3px solid var(--accent);padding-left:13px}
  .tlist .id{font-size:11px;color:var(--muted)}
  .tlist .dir{font-weight:600}
  .pill{font-size:10px;padding:2px 6px;border-radius:4px}
  .win{background:rgba(38,166,154,.18);color:#4cd0c0}
  .loss{background:rgba(239,83,80,.18);color:#ff8a87}
  .flat{background:rgba(120,123,134,.2);color:#aab}
  .main{display:flex;flex-direction:column;min-width:0}
  .topbar{display:flex;align-items:center;gap:14px;padding:8px 14px;border-bottom:1px solid var(--line);
          background:var(--panel);flex-wrap:wrap}
  .tfs{display:flex;gap:4px}
  .tfs button,.nav button,.zoom button{background:#1c2030;color:var(--txt);border:1px solid var(--line);
        border-radius:6px;padding:5px 11px;cursor:pointer;font-size:12px}
  .tfs button.active{background:var(--accent);border-color:var(--accent);color:#fff}
  .tfs button:hover,.nav button:hover,.zoom button:hover{border-color:#3a4458}
  .spacer{flex:1}
  .meta{color:var(--muted);font-size:12px}
  .meta b{color:var(--txt)}
  .chartwrap{position:relative;flex:1;min-height:0}
  canvas{display:block;width:100%;height:100%;cursor:crosshair}
  .ohlc{position:absolute;left:12px;top:10px;font-size:12px;pointer-events:none;
        text-shadow:0 1px 2px #000}
  .ohlc span{margin-right:10px}
  .legend{position:absolute;right:12px;top:10px;font-size:11px;color:var(--muted);
           background:rgba(19,23,34,.7);border:1px solid var(--line);border-radius:8px;padding:8px 10px;
           pointer-events:none;line-height:1.7}
  .legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px}
  .nav,.zoom{display:flex;gap:4px;align-items:center}
</style>
</head>
<body>
<div class="app">
  <aside class="side">
    <h1>Trades</h1>
    <div class="sub" id="sidesub"></div>
    <ul class="tlist" id="tlist"></ul>
  </aside>
  <section class="main">
    <div class="topbar">
      <div class="tfs" id="tfs"></div>
      <div class="nav">
        <button id="prev">‹ Prev</button>
        <button id="next">Next ›</button>
      </div>
      <div class="zoom">
        <button id="zoomout">−</button>
        <button id="zoomin">+</button>
        <button id="reset">Reset</button>
      </div>
      <div class="spacer"></div>
      <div class="meta" id="trademeta"></div>
    </div>
    <div class="chartwrap">
      <canvas id="cv"></canvas>
      <div class="ohlc" id="ohlc"></div>
      <div class="legend">
        <div><i style="background:var(--up)"></i>up &nbsp; <i style="background:var(--down)"></i>down</div>
        <div><i style="background:var(--target-line)"></i>target zone</div>
        <div><i style="background:var(--stop-line)"></i>stop zone</div>
        <div><i style="background:var(--entry)"></i>entry &nbsp; <i style="background:var(--sweep)"></i>swept level</div>
      </div>
    </div>
  </section>
</div>

<script>
const PAYLOAD = __DATA__;
const TF = PAYLOAD.meta.timeframes;
let curTrade = 0, curTF = "5m";
let view = {start:0, count:0};   // visible candle index range (pan/zoom state)
let dragging=false, dragX=0, dragStart=0;

const cv = document.getElementById('cv');
const ctx = cv.getContext('2d');
const fmt = v => v.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
const money = v => (v<0?'-$':'$')+Math.abs(v).toLocaleString(undefined,{maximumFractionDigits:0});

function css(n){return getComputedStyle(document.documentElement).getPropertyValue(n).trim();}

// ---------- sidebar ----------
function buildSidebar(){
  const m=PAYLOAD.meta;
  document.getElementById('sidesub').innerHTML =
    `${m.count} trades · ${TF.join(' / ')}<br>${m.pre_days}d before → ${m.post_days}d after`;
  const ul=document.getElementById('tlist');
  ul.innerHTML = PAYLOAD.trades.map((t,i)=>{
    const cls = t.pnl>0?'win':(t.result==='FLAT'?'flat':'loss');
    return `<li data-i="${i}">
      <div><div class="dir">${t.direction}</div><div class="id">${t.entry_time}</div></div>
      <div style="text-align:right">
        <span class="pill ${cls}">${t.result}</span>
        <div class="id">${money(t.pnl)}</div>
      </div></li>`;
  }).join('');
  ul.querySelectorAll('li').forEach(li=>li.onclick=()=>{curTrade=+li.dataset.i;selectTrade();});
}

// ---------- timeframe buttons ----------
function buildTFs(){
  const box=document.getElementById('tfs');
  box.innerHTML = TF.map(tf=>`<button data-tf="${tf}" class="${tf===curTF?'active':''}">${tf}</button>`).join('');
  box.querySelectorAll('button').forEach(b=>b.onclick=()=>{
    curTF=b.dataset.tf;
    box.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x.dataset.tf===curTF));
    resetView(); draw();
  });
}

function frame(){ return PAYLOAD.trades[curTrade].frames[curTF] || []; }

function resetView(){
  const n=frame().length;
  view.count = Math.min(n, Math.max(30, Math.round(n)));   // show all by default
  view.start = 0;
}

function selectTrade(){
  document.querySelectorAll('.tlist li').forEach((li,i)=>li.classList.toggle('active',i===curTrade));
  const t=PAYLOAD.trades[curTrade];
  document.getElementById('trademeta').innerHTML =
    `<b>${t.direction}</b> · ${t.result} · R ${t.r_gained} · ${money(t.pnl)} ` +
    `&nbsp;|&nbsp; entry ${fmt(t.entry_price)} · stop ${fmt(t.stop_price)} · target ${fmt(t.target_price)}`;
  resetView(); draw();
}

// ---------- drawing ----------
function resize(){
  const r=cv.parentElement.getBoundingClientRect();
  const dpr=window.devicePixelRatio||1;
  cv.width=r.width*dpr; cv.height=r.height*dpr;
  cv.style.width=r.width+'px'; cv.style.height=r.height+'px';
  ctx.setTransform(dpr,0,0,dpr,0,0);
  draw();
}

let geom=null;  // store mapping for crosshair
function draw(){
  const data=frame();
  const W=cv.clientWidth, H=cv.clientHeight;
  ctx.clearRect(0,0,W,H);
  if(!data.length) return;

  const padR=66, padB=26, padT=8, padL=6;
  const plotW=W-padL-padR, plotH=H-padT-padB;

  view.count = Math.max(10, Math.min(data.length, view.count||data.length));
  view.start = Math.max(0, Math.min(data.length-view.count, view.start));
  const s=Math.floor(view.start), e=Math.min(data.length, s+Math.ceil(view.count));
  const vis=data.slice(s,e);

  const t=PAYLOAD.trades[curTrade];
  // price range includes trade levels so zones are always visible
  let lo=Math.min(...vis.map(d=>d.l)), hi=Math.max(...vis.map(d=>d.h));
  [t.entry_price,t.stop_price,t.target_price,t.exit_price,t.swept_level,t.fvg_top,t.fvg_bottom]
    .forEach(v=>{if(v!=null){lo=Math.min(lo,v);hi=Math.max(hi,v);}});
  const pad=(hi-lo)*0.06||1; lo-=pad; hi+=pad;

  const X=i=>padL+(i+0.5)*plotW/vis.length;
  const Y=p=>padT+(hi-p)/(hi-lo)*plotH;
  const cw=Math.max(1.5, plotW/vis.length*0.62);
  geom={s,vis,X,Y,lo,hi,cw,padL,padR,padT,padB,plotW,plotH,W,H};

  // grid + price axis
  ctx.font='11px sans-serif';ctx.textAlign='left';ctx.textBaseline='middle';
  ctx.strokeStyle=css('--grid'); ctx.fillStyle=css('--muted');
  for(let g=0;g<=6;g++){
    const p=lo+(hi-lo)*g/6, y=Y(p);
    ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(W-padR,y);ctx.stroke();
    ctx.fillText(fmt(p), W-padR+6, y);
  }

  // ---- trade zones (drawn behind candles) ----
  const xL=padL, xR=W-padR;
  const band=(p1,p2,color)=>{const y1=Y(Math.max(p1,p2)),y2=Y(Math.min(p1,p2));
    ctx.fillStyle=color;ctx.fillRect(xL,y1,xR-xL,Math.max(1,y2-y1));};
  // target zone (entry->target) green, stop zone (entry->stop) red
  band(t.entry_price,t.target_price,css('--target'));
  band(t.entry_price,t.stop_price,css('--stop'));
  // FVG band
  if(t.fvg_top!=null&&t.fvg_bottom!=null){
    ctx.fillStyle='rgba(91,156,255,.10)';
    const y1=Y(t.fvg_top),y2=Y(t.fvg_bottom);ctx.fillRect(xL,y1,xR-xL,Math.max(1,y2-y1));
  }

  // ---- candles ----
  vis.forEach((d,i)=>{
    const x=X(i), up=d.c>=d.o, col=up?css('--up'):css('--down');
    ctx.strokeStyle=col;ctx.fillStyle=col;
    ctx.beginPath();ctx.moveTo(x,Y(d.h));ctx.lineTo(x,Y(d.l));ctx.stroke();
    const yo=Y(d.o),yc=Y(d.c);
    ctx.fillRect(x-cw/2, Math.min(yo,yc), cw, Math.max(1,Math.abs(yc-yo)));
  });

  // ---- level lines ----
  const line=(p,color,dash,label)=>{
    if(p==null)return;const y=Y(p);
    ctx.strokeStyle=color;ctx.setLineDash(dash);ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(xL,y);ctx.lineTo(xR,y);ctx.stroke();ctx.setLineDash([]);
    ctx.fillStyle=color;ctx.fillRect(W-padR,y-8,padR,16);
    ctx.fillStyle='#0e1016';ctx.textAlign='left';ctx.textBaseline='middle';
    ctx.fillText(fmt(p),W-padR+6,y);
    ctx.fillStyle=color;ctx.textAlign='left';ctx.fillText(label,xL+4,y-7);
  };
  line(t.target_price,css('--target-line'),[6,4],'TARGET');
  line(t.stop_price,css('--stop-line'),[6,4],'STOP');
  line(t.entry_price,css('--entry'),[2,3],'ENTRY');
  if(t.swept_level!=null) line(t.swept_level,css('--sweep'),[1,4],'SWEEP '+(t.swept_source||''));

  // ---- entry & exit markers on the time axis ----
  const markerAt=(timeStr,color,dir)=>{
    const idx=vis.findIndex(d=>d.t>=timeStr);
    if(idx<0)return;
    const x=X(idx);
    ctx.fillStyle=color;ctx.beginPath();
    if(dir==='up'){ctx.moveTo(x,padT+8);ctx.lineTo(x-6,padT);ctx.lineTo(x+6,padT);}
    else{ctx.moveTo(x,H-padB-8);ctx.lineTo(x-6,H-padB);ctx.lineTo(x+6,H-padB);}
    ctx.closePath();ctx.fill();
    ctx.strokeStyle=color;ctx.setLineDash([2,4]);ctx.beginPath();
    ctx.moveTo(x,padT);ctx.lineTo(x,H-padB);ctx.stroke();ctx.setLineDash([]);
  };
  markerAt(t.entry_time, css('--entry'), 'down');
  const exitCol = t.pnl>0?css('--up'):css('--down');
  markerAt(t.exit_time, exitCol, 'up');

  // time axis labels (a few)
  ctx.fillStyle=css('--muted');ctx.textAlign='center';ctx.textBaseline='top';
  const step=Math.max(1,Math.floor(vis.length/6));
  vis.forEach((d,i)=>{if(i%step===0){const lab=curTF==='1D'?d.t.slice(0,10):d.t.slice(5,16);
    ctx.fillText(lab,X(i),H-padB+5);}});
}

// ---------- crosshair / OHLC readout ----------
cv.addEventListener('mousemove',e=>{
  if(!geom)return;
  const r=cv.getBoundingClientRect();const mx=e.clientX-r.left;
  if(dragging){
    const dxCandles=Math.round((dragX-mx)/(geom.plotW/geom.vis.length));
    view.start=dragStart+dxCandles;draw();return;
  }
  const i=Math.round((mx-geom.padL)/geom.plotW*geom.vis.length-0.5);
  const d=geom.vis[Math.max(0,Math.min(geom.vis.length-1,i))];
  if(d){const up=d.c>=d.o,col=up?css('--up'):css('--down');
    document.getElementById('ohlc').innerHTML=
      `<span style="color:${col}">${d.t}</span>`+
      `<span>O <b>${fmt(d.o)}</b></span><span>H <b>${fmt(d.h)}</b></span>`+
      `<span>L <b>${fmt(d.l)}</b></span><span style="color:${col}">C <b>${fmt(d.c)}</b></span>`;
  }
});
cv.addEventListener('mousedown',e=>{dragging=true;dragX=e.clientX-cv.getBoundingClientRect().left;dragStart=view.start;});
window.addEventListener('mouseup',()=>dragging=false);
cv.addEventListener('wheel',e=>{
  e.preventDefault();
  const factor=e.deltaY>0?1.15:0.87;
  const n=frame().length;
  view.count=Math.max(10,Math.min(n,Math.round(view.count*factor)));
  draw();
},{passive:false});

// ---------- controls ----------
document.getElementById('prev').onclick=()=>{curTrade=(curTrade-1+PAYLOAD.trades.length)%PAYLOAD.trades.length;selectTrade();};
document.getElementById('next').onclick=()=>{curTrade=(curTrade+1)%PAYLOAD.trades.length;selectTrade();};
document.getElementById('zoomin').onclick=()=>{view.count=Math.max(10,Math.round(view.count*0.8));draw();};
document.getElementById('zoomout').onclick=()=>{const n=frame().length;view.count=Math.min(n,Math.round(view.count*1.25));draw();};
document.getElementById('reset').onclick=()=>{resetView();draw();};
window.addEventListener('keydown',e=>{
  if(e.key==='ArrowRight')document.getElementById('next').click();
  if(e.key==='ArrowLeft')document.getElementById('prev').click();
});
window.addEventListener('resize',resize);

// ---------- init ----------
buildSidebar(); buildTFs(); selectTrade(); resize();
</script>
</body>
</html>
"""


def main() -> int:
    if not CHARTS_JSON.exists():
        print(f"No {CHARTS_JSON}. Run `python analysis/build_charts.py` first.")
        return 1
    payload = json.loads(CHARTS_JSON.read_text())
    html = HTML.replace("__DATA__", json.dumps(payload))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HTML_OUT.write_text(html, encoding="utf-8")
    print(f"Chart viewer written: {HTML_OUT}  ({HTML_OUT.stat().st_size/1024:.0f} KB)")
    print("Open it in any browser (offline). Use the timeframe buttons and Prev/Next.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
