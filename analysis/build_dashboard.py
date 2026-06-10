"""
analysis/build_dashboard.py — Build a self-contained, OFFLINE interactive
dashboard from the analysis aggregates. Additive only: reads analysis.json
(produced by analyze.py) and writes analysis/output/dashboard.html.

It runs analyze.py for you if analysis.json is missing, so the one command:

    python analysis/build_dashboard.py

…does the whole thing: read trade_log.csv -> aggregate -> render dashboard.

The HTML embeds all data inline and draws every chart with plain <canvas> +
vanilla JS (no CDN, no internet needed). Charts included:
  1. Monthly equity CANDLES (green/red, wicks)  -- the "trading chart" view
  2. Cumulative equity curve with drawdown shading
  3. Calendar heatmap (Year x Month P&L)
  4. P&L by day-of-week
  5. P&L by entry hour
  6. Seasonality: P&L by calendar month (all years combined)
  7. LONG vs SHORT split
  8. Yearly P&L bars
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE / "output"
JSON_PATH = OUT_DIR / "analysis.json"
HTML_PATH = OUT_DIR / "dashboard.html"


def ensure_json() -> dict:
    if not JSON_PATH.exists():
        import analyze
        trades = analyze.DEFAULT_TRADES
        if not trades.exists():
            raise SystemExit(
                f"No trade log at {trades}. Run `python run.py backtest` first."
            )
        data = analyze.analyze(trades)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        JSON_PATH.write_text(json.dumps(data, indent=2))
        return data
    return json.loads(JSON_PATH.read_text())


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Sniper Bot v2 — P&L Analysis</title>
<style>
  :root{
    --bg:#0b0e14; --panel:#121722; --panel2:#171d2b; --line:#222a3a;
    --txt:#e6edf6; --muted:#8b97ad; --grid:#1e2636;
    --green:#26a37b; --green2:#2ecf9a; --red:#e0556b; --red2:#ff6b81;
    --accent:#5b9cff; --gold:#f0b429;
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);
       font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
  header{padding:24px 28px 8px;border-bottom:1px solid var(--line)}
  h1{margin:0;font-size:20px;letter-spacing:.3px}
  .sub{color:var(--muted);font-size:13px;margin-top:4px}
  .wrap{padding:20px 28px 60px;max-width:1280px;margin:0 auto}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:18px 0 26px}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .card .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.6px}
  .card .v{font-size:22px;font-weight:650;margin-top:6px}
  .pos{color:var(--green2)} .neg{color:var(--red2)}
  .panel{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 18px 8px;margin-bottom:22px}
  .panel h2{margin:0 0 2px;font-size:15px}
  .panel p.note{margin:0 0 14px;color:var(--muted);font-size:12.5px}
  canvas{width:100%;display:block}
  .row2{display:grid;grid-template-columns:1fr 1fr;gap:22px}
  @media(max-width:860px){.row2{grid-template-columns:1fr}}
  table.cal{border-collapse:collapse;width:100%;font-size:12px}
  table.cal th,table.cal td{padding:6px 4px;text-align:center;border:1px solid var(--bg)}
  table.cal th{color:var(--muted);font-weight:500}
  table.cal td{border-radius:4px;cursor:default}
  .legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:12px;margin-top:10px;align-items:center}
  .sw{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:5px;vertical-align:-1px}
  .tip{position:fixed;pointer-events:none;background:#0f1420;border:1px solid var(--line);
       padding:8px 10px;border-radius:8px;font-size:12px;opacity:0;transition:opacity .08s;z-index:9;
       box-shadow:0 8px 30px rgba(0,0,0,.45)}
  .tip b{color:var(--txt)}
</style>
</head>
<body>
<header>
  <h1>Sniper Bot v2 — Backtest P&amp;L Analysis</h1>
  <div class="sub" id="subline"></div>
</header>
<div class="wrap">
  <div class="cards" id="cards"></div>

  <div class="panel">
    <h2>Monthly equity candles</h2>
    <p class="note">Each candle is one calendar month on the account equity curve. Body = equity at month
      start → month end (green = grew, red = shrank); wick = the highest/lowest equity reached intramonth.</p>
    <canvas id="candles" height="360"></canvas>
    <div class="legend">
      <span><span class="sw" style="background:var(--green)"></span>Profit month</span>
      <span><span class="sw" style="background:var(--red)"></span>Loss month</span>
      <span>Hover a candle for detail</span>
    </div>
  </div>

  <div class="panel">
    <h2>Cumulative equity &amp; drawdown</h2>
    <p class="note">Running account P&amp;L across all trades; shaded region below is drawdown from the prior peak.</p>
    <canvas id="equity" height="300"></canvas>
  </div>

  <div class="panel">
    <h2>Calendar heatmap — P&amp;L by year &amp; month</h2>
    <p class="note">Greener = more profit, redder = more loss. Blank = no trades that month.</p>
    <div id="calwrap"></div>
    <div class="legend">
      <span><span class="sw" style="background:var(--red)"></span>Loss</span>
      <span><span class="sw" style="background:#2a3144"></span>Flat</span>
      <span><span class="sw" style="background:var(--green)"></span>Profit</span>
    </div>
  </div>

  <div class="row2">
    <div class="panel">
      <h2>Seasonality — P&amp;L by month of year</h2>
      <p class="note">All years combined: which calendar months tend to pay.</p>
      <canvas id="moy" height="280"></canvas>
    </div>
    <div class="panel">
      <h2>P&amp;L by day of week</h2>
      <p class="note">Which weekday the bot makes its money on.</p>
      <canvas id="dow" height="280"></canvas>
    </div>
  </div>

  <div class="row2">
    <div class="panel">
      <h2>P&amp;L by entry hour (NY time)</h2>
      <p class="note">Performance grouped by the hour the trade was entered.</p>
      <canvas id="hour" height="280"></canvas>
    </div>
    <div class="panel">
      <h2>Yearly P&amp;L</h2>
      <p class="note">Net result per calendar year.</p>
      <canvas id="yearly" height="280"></canvas>
    </div>
  </div>

  <div class="panel">
    <h2>Long vs Short</h2>
    <p class="note">Net P&amp;L, trade count and win rate by trade direction.</p>
    <canvas id="dir" height="200"></canvas>
  </div>
</div>

<div class="tip" id="tip"></div>

<script>
const DATA = __DATA__;
const tip = document.getElementById('tip');
const money = v => (v<0?'-$':'$') + Math.abs(v).toLocaleString(undefined,{maximumFractionDigits:0});
const GREEN='#26a37b', GREEN2='#2ecf9a', RED='#e0556b', RED2='#ff6b81', MUTED='#8b97ad', GRID='#1e2636', AX='#3a4458';
function showTip(html,x,y){tip.innerHTML=html;tip.style.opacity=1;tip.style.left=(x+14)+'px';tip.style.top=(y+14)+'px';}
function hideTip(){tip.style.opacity=0;}

// Hi-DPI canvas setup -> returns {ctx,w,h}
function setup(id,cssH){
  const c=document.getElementById(id);
  const dpr=window.devicePixelRatio||1;
  const w=c.clientWidth, h=cssH;
  c.width=w*dpr; c.height=h*dpr; c.style.height=h+'px';
  const ctx=c.getContext('2d'); ctx.scale(dpr,dpr);
  return {c,ctx,w,h};
}
function niceBounds(min,max){
  if(min===max){min-=1;max+=1;}
  const pad=(max-min)*0.08; return [min-pad,max+pad];
}

// ---- headline cards ----
(function(){
  const h=DATA.headline;
  document.getElementById('subline').textContent =
    `${h.date_from} → ${h.date_to}  •  ${h.trades.toLocaleString()} trades`;
  const cls = v => v>=0?'pos':'neg';
  const cards=[
    ['Total P&L', money(h.total_pnl), cls(h.total_pnl)],
    ['Win rate', h.win_rate+'%',''],
    ['Profit factor', h.profit_factor,''],
    ['Total R', h.total_r,''],
    ['Avg P&L / trade', money(h.avg_pnl), cls(h.avg_pnl)],
    ['Wins / Losses', h.wins+' / '+h.losses,''],
    ['Best month', (h.best_month?h.best_month.ym+' · '+money(h.best_month.month_pnl):'—'),'pos'],
    ['Worst month', (h.worst_month?h.worst_month.ym+' · '+money(h.worst_month.month_pnl):'—'),'neg'],
  ];
  document.getElementById('cards').innerHTML = cards.map(
    ([k,v,c])=>`<div class="card"><div class="k">${k}</div><div class="v ${c}">${v}</div></div>`
  ).join('');
})();

// ---- 1. monthly candles ----
(function(){
  const d=DATA.monthly_candles; if(!d.length)return;
  const {c,ctx,w,h}=setup('candles',360);
  const padL=58,padR=12,padT=14,padB=42;
  const lo=Math.min(...d.map(x=>x.low)), hi=Math.max(...d.map(x=>x.high));
  const [y0,y1]=niceBounds(lo,hi);
  const X=i=>padL+(i+0.5)*(w-padL-padR)/d.length;
  const Y=v=>padT+(y1-v)/(y1-y0)*(h-padT-padB);
  const bw=Math.max(2,Math.min(16,(w-padL-padR)/d.length*0.6));
  // grid + y labels
  ctx.font='11px sans-serif';ctx.textAlign='right';ctx.textBaseline='middle';
  for(let g=0;g<=5;g++){const v=y0+(y1-y0)*g/5,y=Y(v);
    ctx.strokeStyle=GRID;ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(w-padR,y);ctx.stroke();
    ctx.fillStyle=MUTED;ctx.fillText(money(v),padL-8,y);}
  // zero line
  if(y0<0&&y1>0){ctx.strokeStyle='#3a4458';ctx.setLineDash([4,4]);ctx.strokeStyle=MUTED;
    ctx.beginPath();ctx.moveTo(padL,Y(0));ctx.lineTo(w-padR,Y(0));ctx.stroke();ctx.setLineDash([]);}
  // candles
  d.forEach((x,i)=>{
    const col=x.up?GREEN:RED, colb=x.up?GREEN2:RED2, cx=X(i);
    ctx.strokeStyle=col;ctx.beginPath();ctx.moveTo(cx,Y(x.high));ctx.lineTo(cx,Y(x.low));ctx.stroke();
    const top=Y(Math.max(x.open,x.close)),bot=Y(Math.min(x.open,x.close));
    ctx.fillStyle=colb;ctx.fillRect(cx-bw/2,top,bw,Math.max(1,bot-top));
  });
  // x labels (year starts)
  ctx.textAlign='center';ctx.textBaseline='top';ctx.fillStyle=MUTED;
  d.forEach((x,i)=>{if(x.ym.endsWith('-01')){ctx.fillText(x.ym.slice(0,4),X(i),h-padB+8);}});
  // hover
  c.onmousemove=e=>{const r=c.getBoundingClientRect();const mx=e.clientX-r.left;
    let i=Math.round((mx-padL)/(w-padL-padR)*d.length-0.5);i=Math.max(0,Math.min(d.length-1,i));
    const x=d[i];showTip(`<b>${x.ym}</b><br>Month P&L: ${money(x.month_pnl)}<br>Trades: ${x.trades} · WR ${x.win_rate}%<br>Equity ${money(x.open)}→${money(x.close)}`,e.clientX,e.clientY);};
  c.onmouseleave=hideTip;
})();

// ---- 2. equity curve + drawdown ----
(function(){
  const d=DATA.equity; if(!d.length)return;
  const {c,ctx,w,h}=setup('equity',300);
  const padL=58,padR=12,padT=14,padB=28;
  const cum=d.map(x=>x.cum);
  const [y0,y1]=niceBounds(Math.min(0,...cum),Math.max(0,...cum));
  const X=i=>padL+i*(w-padL-padR)/(d.length-1);
  const Y=v=>padT+(y1-v)/(y1-y0)*(h-padT-padB);
  ctx.font='11px sans-serif';ctx.textAlign='right';ctx.textBaseline='middle';
  for(let g=0;g<=5;g++){const v=y0+(y1-y0)*g/5,y=Y(v);
    ctx.strokeStyle=GRID;ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(w-padR,y);ctx.stroke();
    ctx.fillStyle=MUTED;ctx.fillText(money(v),padL-8,y);}
  // drawdown shading (peak - cum)
  ctx.beginPath();ctx.moveTo(X(0),Y(0));
  let peak=-1e18;
  d.forEach((x,i)=>{peak=Math.max(peak,x.cum);ctx.lineTo(X(i),Y(x.cum-(peak-x.cum)>=0?x.cum:x.cum));});
  // equity line
  ctx.beginPath();
  d.forEach((x,i)=>{const px=X(i),py=Y(x.cum);i?ctx.lineTo(px,py):ctx.moveTo(px,py);});
  ctx.strokeStyle=GREEN2;ctx.lineWidth=1.6;ctx.stroke();
  // fill under
  ctx.lineTo(X(d.length-1),Y(Math.max(0,y0)));ctx.lineTo(X(0),Y(Math.max(0,y0)));ctx.closePath();
  ctx.fillStyle='rgba(46,207,154,0.10)';ctx.fill();
  // x year labels
  ctx.textAlign='center';ctx.textBaseline='top';ctx.fillStyle=MUTED;
  let lastYr=null;
  d.forEach((x,i)=>{const yr=x.t.slice(0,4);if(yr!==lastYr&&x.t.slice(5,7)==='01'){ctx.fillText(yr,X(i),h-padB+6);lastYr=yr;}});
  c.onmousemove=e=>{const r=c.getBoundingClientRect();const mx=e.clientX-r.left;
    let i=Math.round((mx-padL)/(w-padL-padR)*(d.length-1));i=Math.max(0,Math.min(d.length-1,i));
    const x=d[i];showTip(`<b>${x.t}</b><br>Equity: ${money(x.cum)}<br>Drawdown: ${money(x.dd)}`,e.clientX,e.clientY);};
  c.onmouseleave=hideTip;
})();

// ---- 3. calendar heatmap ----
(function(){
  const cal=DATA.calendar;
  let max=1;
  cal.years.forEach(y=>cal.grid[y].forEach(cell=>{if(cell.pnl!=null)max=Math.max(max,Math.abs(cell.pnl));}));
  const color=v=>{
    if(v==null)return 'background:#10141d;color:#3a4458';
    const t=Math.min(1,Math.abs(v)/max);
    const c=v>=0?[38,163,123]:[224,85,107];
    const a=0.15+0.75*t;
    return `background:rgba(${c[0]},${c[1]},${c[2]},${a});color:#e6edf6`;
  };
  let html='<table class="cal"><tr><th></th>'+cal.months.map(m=>`<th>${m}</th>`).join('')+'</tr>';
  cal.years.forEach(y=>{
    html+=`<tr><th>${y}</th>`;
    cal.grid[y].forEach((cell,mi)=>{
      const lbl = cell.pnl==null?'·':(cell.pnl>=0?'':'-')+'$'+Math.round(Math.abs(cell.pnl)/1000)+'k';
      const ttl = cell.pnl==null?'no trades':`${money(cell.pnl)} · ${cell.trades} trades`;
      html+=`<td style="${color(cell.pnl)}" title="${y} ${cal.months[mi]} — ${ttl}">${lbl}</td>`;
    });
    html+='</tr>';
  });
  html+='</table>';
  document.getElementById('calwrap').innerHTML=html;
})();

// ---- generic bar chart ----
function barChart(id,items,getLabel,getVal,getTip){
  const {c,ctx,w,h}=setup(id,280);
  const padL=58,padR=12,padT=14,padB=40;
  const vals=items.map(getVal);
  const [y0,y1]=niceBounds(Math.min(0,...vals),Math.max(0,...vals));
  const Y=v=>padT+(y1-v)/(y1-y0)*(h-padT-padB);
  const bw=(w-padL-padR)/items.length;
  ctx.font='11px sans-serif';ctx.textAlign='right';ctx.textBaseline='middle';
  for(let g=0;g<=4;g++){const v=y0+(y1-y0)*g/4,y=Y(v);
    ctx.strokeStyle=GRID;ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(w-padR,y);ctx.stroke();
    ctx.fillStyle=MUTED;ctx.fillText(money(v),padL-8,y);}
  const zeroY=Y(0);
  items.forEach((it,i)=>{
    const v=getVal(it),x=padL+i*bw+bw*0.15,bwi=bw*0.7;
    ctx.fillStyle=v>=0?GREEN2:RED2;
    const yt=Math.min(zeroY,Y(v)),hh=Math.abs(Y(v)-zeroY);
    ctx.fillRect(x,yt,bwi,Math.max(1,hh));
  });
  ctx.textAlign='center';ctx.textBaseline='top';ctx.fillStyle=MUTED;
  items.forEach((it,i)=>ctx.fillText(getLabel(it),padL+i*bw+bw/2,h-padB+8));
  c.onmousemove=e=>{const r=c.getBoundingClientRect();const mx=e.clientX-r.left;
    let i=Math.floor((mx-padL)/bw);if(i<0||i>=items.length){hideTip();return;}
    showTip(getTip(items[i]),e.clientX,e.clientY);};
  c.onmouseleave=hideTip;
}

barChart('moy',DATA.month_of_year,it=>it.month,it=>it.pnl,
  it=>`<b>${it.month}</b><br>P&L ${money(it.pnl)}<br>${it.trades} trades · WR ${it.win_rate}%`);
barChart('dow',DATA.day_of_week,it=>it.label,it=>it.pnl,
  it=>`<b>${it.label}</b><br>P&L ${money(it.pnl)}<br>${it.trades} trades · WR ${it.win_rate}%`);
barChart('hour',DATA.hour_of_day,it=>it.label,it=>it.pnl,
  it=>`<b>${it.label}</b><br>P&L ${money(it.pnl)}<br>${it.trades} trades · WR ${it.win_rate}%`);
barChart('yearly',DATA.yearly,it=>it.year,it=>it.pnl,
  it=>`<b>${it.year}</b><br>P&L ${money(it.pnl)}<br>${it.trades} trades · WR ${it.win_rate}%`);

// ---- direction (horizontal-ish via bar chart) ----
barChart('dir',DATA.direction,it=>it.label,it=>it.pnl,
  it=>`<b>${it.label}</b><br>P&L ${money(it.pnl)}<br>${it.trades} trades · WR ${it.win_rate}%`);
</script>
</body>
</html>
"""


def main() -> int:
    data = ensure_json()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(data))
    HTML_PATH.write_text(html, encoding="utf-8")
    print(f"Dashboard written: {HTML_PATH}")
    print("Open it in any browser (no internet required).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
