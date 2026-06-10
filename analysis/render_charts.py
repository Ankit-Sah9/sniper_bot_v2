"""
analysis/render_charts.py — TradingView-style trade viewer (lightweight-charts,
Apache-2.0). Clean trade marking: shaded risk/reward boxes + FVG rectangle +
entry/exit markers, anchored to the trade (no full-width lines or axis labels).
"""
from __future__ import annotations
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
CHARTS_JSON = OUT_DIR / "charts.json"
HTML_OUT = OUT_DIR / "chart_viewer.html"
LIB_LOCAL = OUT_DIR / "lightweight-charts.standalone.production.js"
LIB_CDN = "https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Trade Chart Viewer</title>
<style>
  :root{--bg:#0e1016;--panel:#131722;--line:#2a2e39;--txt:#d1d4dc;--muted:#787b86;
    --up:#26a69a;--down:#ef5350;--accent:#2962ff;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font:13px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif}
  .app{display:grid;grid-template-columns:260px 1fr;height:100vh}
  .side{background:var(--panel);border-right:1px solid var(--line);overflow-y:auto}
  .side h1{font-size:14px;margin:0;padding:14px 16px;border-bottom:1px solid var(--line)}
  .side .sub{padding:8px 16px;color:var(--muted);font-size:12px;border-bottom:1px solid var(--line)}
  .tlist{list-style:none;margin:0;padding:0}
  .tlist li{padding:9px 16px;border-bottom:1px solid #1c1f2a;cursor:pointer;display:flex;
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
  .topbar{display:flex;align-items:center;gap:10px;padding:8px 14px;border-bottom:1px solid var(--line);
          background:var(--panel);flex-wrap:wrap}
  .tfs{display:flex;gap:4px}
  .tfs button,.nav button{background:#1c2030;color:var(--txt);border:1px solid var(--line);
        border-radius:6px;padding:5px 11px;cursor:pointer;font-size:12px}
  .tfs button.active{background:var(--accent);border-color:var(--accent);color:#fff}
  .tfs button:hover,.nav button:hover{border-color:#3a4458}
  .spacer{flex:1}
  .meta{color:var(--muted);font-size:12px}.meta b{color:var(--txt)}
  .chartwrap{position:relative;flex:1;min-height:0}
  #chart{position:absolute;inset:0}
  #overlay{position:absolute;inset:0;pointer-events:none;z-index:2}
  .legend{position:absolute;left:12px;top:8px;z-index:3;font-size:12px;pointer-events:none;text-shadow:0 1px 2px #000}
  .legend .row2{margin-top:2px;font-size:11px;color:var(--muted)}
  .err{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;text-align:center;
       color:var(--muted);padding:30px;z-index:5;line-height:1.7}
  code{background:#1c2030;padding:2px 6px;border-radius:4px;color:#cdd}
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
        <button id="prev">&lsaquo; Prev</button>
        <button id="next">Next &rsaquo;</button>
        <button id="fit">Fit</button>
      </div>
      <div class="spacer"></div>
      <div class="meta" id="trademeta"></div>
    </div>
    <div class="chartwrap">
      <div id="chart"></div>
      <canvas id="overlay"></canvas>
      <div class="legend" id="legend"></div>
    </div>
  </section>
</div>
<script src="__LIB_SRC__"></script>
<script>
const PAYLOAD = __DATA__;
const TF = PAYLOAD.meta.timeframes;
let curTrade = 0, curTF = "5m";
let chart=null, series=null;
const overlay=document.getElementById('overlay');
const octx=overlay.getContext('2d');
const money = v => (v<0?'-$':'$')+Math.abs(v).toLocaleString(undefined,{maximumFractionDigits:0});
const fmt = v => v.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});

if(typeof LightweightCharts === 'undefined'){
  document.getElementById('chart').insertAdjacentHTML('beforebegin',
    '<div class="err">Could not load the charting library offline.<br>'+
    'Download it once:<br><br><code>Invoke-WebRequest -Uri "https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js" -OutFile "analysis/output/lightweight-charts.standalone.production.js"</code>'+
    '<br><br>then refresh.</div>');
}
function toTs(s){
  const [d,t]=s.split(' ');const [Y,Mo,D]=d.split('-').map(Number);
  const [H,Mi]=(t||'00:00').split(':').map(Number);
  return Math.floor(Date.UTC(Y,Mo-1,D,H,Mi||0)/1000);
}
function buildSidebar(){
  const m=PAYLOAD.meta;
  document.getElementById('sidesub').innerHTML =
    `${m.count} trades &middot; ${TF.join(' / ')}<br>${m.pre_days}d before &rarr; ${m.post_days}d after`;
  const ul=document.getElementById('tlist');
  ul.innerHTML = PAYLOAD.trades.map((t,i)=>{
    const cls = t.pnl>0?'win':(t.result==='FLAT'?'flat':'loss');
    return `<li data-i="${i}"><div><div class="dir">${t.direction}</div>`+
      `<div class="id">${t.entry_time}</div></div><div style="text-align:right">`+
      `<span class="pill ${cls}">${t.result}</span><div class="id">${money(t.pnl)}</div></div></li>`;
  }).join('');
  ul.querySelectorAll('li').forEach(li=>li.onclick=()=>{curTrade=+li.dataset.i;selectTrade();});
}
function buildTFs(){
  const box=document.getElementById('tfs');
  box.innerHTML = TF.map(tf=>`<button data-tf="${tf}" class="${tf===curTF?'active':''}">${tf}</button>`).join('');
  box.querySelectorAll('button').forEach(b=>b.onclick=()=>{
    curTF=b.dataset.tf;
    box.querySelectorAll('button').forEach(x=>x.classList.toggle('active',x.dataset.tf===curTF));
    renderTrade();
  });
}
function baseLegend(t){
  return `<div><b>${t.direction}</b> ${t.result} &middot; R ${t.r_gained} &middot; ${money(t.pnl)}</div>`+
         `<div class="row2">entry ${fmt(t.entry_price)} &middot; stop ${fmt(t.stop_price)} &middot; target ${fmt(t.target_price)}</div>`;
}
function initChart(){
  if(typeof LightweightCharts==='undefined') return;
  const el=document.getElementById('chart');
  chart = LightweightCharts.createChart(el,{
    layout:{background:{type:'solid',color:'#0e1016'},textColor:'#d1d4dc'},
    grid:{vertLines:{color:'#1c1f2a'},horzLines:{color:'#1c1f2a'}},
    crosshair:{mode:LightweightCharts.CrosshairMode.Normal},
    rightPriceScale:{borderColor:'#2a2e39',scaleMargins:{top:0.12,bottom:0.12}},
    timeScale:{borderColor:'#2a2e39',timeVisible:true,secondsVisible:false,rightOffset:6,barSpacing:7},
    handleScroll:true,handleScale:true,
  });
  series = chart.addCandlestickSeries({
    upColor:'#26a69a',downColor:'#ef5350',wickUpColor:'#26a69a',
    wickDownColor:'#ef5350',borderVisible:false,
  });
  chart.subscribeCrosshairMove(param=>{
    const lg=document.getElementById('legend');const t=PAYLOAD.trades[curTrade];
    if(!param||!param.time||!param.seriesData||!param.seriesData.get(series)){lg.innerHTML=baseLegend(t);return;}
    const d=param.seriesData.get(series);const up=d.close>=d.open,col=up?'#26a69a':'#ef5350';
    lg.innerHTML=`<div><b>${t.direction}</b> ${t.result} &middot; ${money(t.pnl)}</div>`+
      `<div class="row2" style="color:${col}">O ${fmt(d.open)}  H ${fmt(d.high)}  L ${fmt(d.low)}  C ${fmt(d.close)}</div>`;
  });
  // redraw overlay boxes whenever the visible range or size changes
  chart.timeScale().subscribeVisibleTimeRangeChange(drawOverlay);
  new ResizeObserver(()=>{sizeOverlay();drawOverlay();}).observe(el);
}
function sizeOverlay(){
  const el=document.getElementById('chart');const r=el.getBoundingClientRect();
  const dpr=window.devicePixelRatio||1;
  overlay.width=r.width*dpr;overlay.height=r.height*dpr;
  overlay.style.width=r.width+'px';overlay.style.height=r.height+'px';
  octx.setTransform(dpr,0,0,dpr,0,0);
}
function box(x1,x2,p1,p2,fill){
  const ts=chart.timeScale();
  let X1=ts.timeToCoordinate(x1), X2=ts.timeToCoordinate(x2);
  let Y1=series.priceToCoordinate(p1), Y2=series.priceToCoordinate(p2);
  if(X1==null||X2==null||Y1==null||Y2==null) return;
  const x=Math.min(X1,X2), w=Math.max(2,Math.abs(X2-X1));
  const y=Math.min(Y1,Y2), h=Math.max(1,Math.abs(Y2-Y1));
  octx.fillStyle=fill; octx.fillRect(x,y,w,h);
}
function drawOverlay(){
  if(!chart||!series) return;
  octx.clearRect(0,0,overlay.width,overlay.height);
  const t=PAYLOAD.trades[curTrade];
  const eTs=toTs(t.entry_time), xTs=toTs(t.exit_time);
  const rightTs = xTs>eTs?xTs:eTs+5*60; // ensure a visible width even if instant
  // Risk box (entry->stop) red; Reward box (entry->target) green
  box(eTs,rightTs,t.entry_price,t.stop_price,'rgba(239,83,80,0.16)');
  box(eTs,rightTs,t.entry_price,t.target_price,'rgba(38,166,154,0.16)');
  // thin border lines on entry/stop/target across the box only
  const ts=chart.timeScale();
  const drawSeg=(price,color,dash)=>{
    const Y=series.priceToCoordinate(price); let X1=ts.timeToCoordinate(eTs),X2=ts.timeToCoordinate(rightTs);
    if(Y==null||X1==null||X2==null)return;
    octx.strokeStyle=color;octx.lineWidth=1.2;octx.setLineDash(dash||[]);
    octx.beginPath();octx.moveTo(Math.min(X1,X2),Y);octx.lineTo(Math.max(X1,X2),Y);octx.stroke();octx.setLineDash([]);
  };
  drawSeg(t.entry_price,'#e0b84c',[4,3]);
  drawSeg(t.stop_price,'#ef5350',[]);
  drawSeg(t.target_price,'#26a69a',[]);
  // FVG rectangle: small box around the armed (MSS) time, between fvg top/bottom
  if(t.fvg_top!=null && t.fvg_bottom!=null){
    const fTs = toTs(t.armed_time||t.entry_time);
    box(fTs, eTs>fTs?eTs:fTs+5*60, t.fvg_top, t.fvg_bottom, 'rgba(91,156,255,0.22)');
    // outline
    const X1=ts.timeToCoordinate(fTs), X2=ts.timeToCoordinate(eTs>fTs?eTs:fTs+5*60);
    const Y1=series.priceToCoordinate(t.fvg_top), Y2=series.priceToCoordinate(t.fvg_bottom);
    if(X1!=null&&X2!=null&&Y1!=null&&Y2!=null){
      octx.strokeStyle='rgba(91,156,255,0.7)';octx.lineWidth=1;
      octx.strokeRect(Math.min(X1,X2),Math.min(Y1,Y2),Math.max(2,Math.abs(X2-X1)),Math.max(1,Math.abs(Y2-Y1)));
    }
  }
  // Swept level: a SHORT dashed segment near the trade (not full width)
  if(t.swept_level!=null){
    const sTs = toTs(t.armed_time||t.entry_time);
    const Y=series.priceToCoordinate(t.swept_level);
    let X1=ts.timeToCoordinate(sTs), X2=ts.timeToCoordinate(rightTs);
    if(Y!=null&&X1!=null&&X2!=null){
      octx.strokeStyle='#5b9cff';octx.lineWidth=1;octx.setLineDash([5,4]);
      octx.beginPath();octx.moveTo(Math.min(X1,X2),Y);octx.lineTo(Math.max(X1,X2),Y);octx.stroke();octx.setLineDash([]);
    }
  }
}
function renderTrade(){
  if(!chart) return;
  const t=PAYLOAD.trades[curTrade];
  const seen=new Set(),clean=[];
  (t.frames[curTF]||[]).forEach(c=>{const ts=toTs(c.t);
    if(!seen.has(ts)){seen.add(ts);clean.push({time:ts,open:c.o,high:c.h,low:c.l,close:c.c});}});
  clean.sort((a,b)=>a.time-b.time);
  series.setData(clean);
  series.setMarkers([]); // we mark with boxes; keep tiny entry/exit dots below
  const longish=t.direction==='LONG',win=t.pnl>0;
  const markers=[
    {time:toTs(t.entry_time),position:longish?'belowBar':'aboveBar',color:'#e0b84c',
     shape:longish?'arrowUp':'arrowDown',text:'Entry'},
    {time:toTs(t.exit_time),position:longish?'aboveBar':'belowBar',color:win?'#26a69a':'#ef5350',
     shape:'circle',text:win?'Win':'Exit'},
  ].sort((a,b)=>a.time-b.time);
  try{series.setMarkers(markers);}catch(e){}
  chart.timeScale().fitContent();
  sizeOverlay(); drawOverlay();
  document.getElementById('legend').innerHTML=baseLegend(t);
  document.getElementById('trademeta').innerHTML=
    `<b>${t.direction}</b> &middot; ${t.result} &middot; R ${t.r_gained} &middot; ${money(t.pnl)}`;
}
function selectTrade(){
  document.querySelectorAll('.tlist li').forEach((li,i)=>li.classList.toggle('active',i===curTrade));
  renderTrade();
}
document.getElementById('prev').onclick=()=>{curTrade=(curTrade-1+PAYLOAD.trades.length)%PAYLOAD.trades.length;selectTrade();};
document.getElementById('next').onclick=()=>{curTrade=(curTrade+1)%PAYLOAD.trades.length;selectTrade();};
document.getElementById('fit').onclick=()=>{if(chart){chart.timeScale().fitContent();drawOverlay();}};
window.addEventListener('keydown',e=>{
  if(e.key==='ArrowLeft')document.getElementById('prev').click();
  else if(e.key==='ArrowRight')document.getElementById('next').click();
});
buildSidebar();buildTFs();initChart();selectTrade();
</script>
</body>
</html>
"""

def main() -> int:
    if not CHARTS_JSON.exists():
        print(f"No chart data at {CHARTS_JSON}. Run: python analysis/build_charts.py")
        return 1
    data = json.loads(CHARTS_JSON.read_text(encoding="utf-8"))
    if LIB_LOCAL.exists():
        lib_src = LIB_LOCAL.name; offline = True
    else:
        lib_src = LIB_CDN; offline = False
    html = (HTML.replace("__DATA__", json.dumps(data, separators=(",", ":")))
                .replace("__LIB_SRC__", lib_src))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    HTML_OUT.write_text(html, encoding="utf-8")
    kb = HTML_OUT.stat().st_size / 1024
    print(f"Chart viewer written: {HTML_OUT}  ({kb:.0f} KB)")
    print("Using LOCAL library (offline)." if offline else
          "NOTE: library not found locally; viewer will use the CDN (needs internet).")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())