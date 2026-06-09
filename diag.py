from engine.data_loader import load_candles
from engine.strategy import find_setup_at, in_entry_window
from engine.htf_bias import FourHourBias
from engine.config import CONFIG
from collections import Counter
import statistics

DATA = "data/history/NQ_5min.csv"
candles = load_candles(DATA, source_tz="America/New_York")
print(f"Loaded {len(candles)} candles  {candles[0].time.date()} -> {candles[-1].time.date()}")
print(f"bias_method={CONFIG.bias_method} entryN={CONFIG.fractal_n_entry} "
      f"biasN={CONFIG.fractal_n_bias} filter={CONFIG.bias_filter_enabled}")
print("="*70)

idx_by_day = {}
for i, c in enumerate(candles):
    idx_by_day.setdefault(c.time.date(), []).append(i)
days = sorted(idx_by_day)

fb = FourHourBias(candles) if CONFIG.bias_method == "4h_structure" else None

f = Counter()
bias_seen = Counter()
disp_sizes = []
retrace_gap = []

for d in days:
    f['total_days'] += 1
    morning = [i for i in idx_by_day[d]
               if 7*60+30 <= candles[i].time.hour*60+candles[i].time.minute < 11*60]
    if not morning:
        f['drop_no_morning'] += 1
        continue

    # arm first setup of the day (engine behaviour)
    armed = None
    for i in morning:
        s = find_setup_at(candles, i)
        if s:
            armed = s
            break
    if armed is None:
        f['drop_no_setup'] += 1
        continue
    f['1_armed'] += 1

    want = "BULLISH" if armed.direction == "LONG" else "BEARISH"

    # bias at arm time
    if fb is not None:
        b = fb.bias_as_of(armed.armed_time)
        bias_seen[b] += 1
        if b == "NEUTRAL":
            f['drop_bias_neutral'] += 1
            continue
        if b != want:
            f['drop_bias_conflict'] += 1
            continue
        f['2_bias_ok'] += 1
    else:
        f['2_bias_ok'] += 1

    disp_sizes.append(abs(armed.displacement_end - armed.displacement_start))

    # fill within window
    edge = armed.entry_price
    filled = False
    closest = None
    for j in range(armed.mss_index+1, len(candles)):
        cj = candles[j]
        if cj.time.date() != armed.armed_time.date():
            break
        if not in_entry_window(cj.time):
            if cj.time.hour*60+cj.time.minute >= 11*60:
                break
            continue
        dist = min(abs(cj.high-edge), abs(cj.low-edge))
        closest = dist if closest is None else min(closest, dist)
        if cj.low <= edge <= cj.high:
            filled = True
            break
    if filled:
        f['3_FILLED'] += 1
    else:
        f['drop_no_retrace'] += 1
        if closest is not None:
            retrace_gap.append(closest)

print("FUNNEL")
for k in ['total_days','drop_no_morning','drop_no_setup','1_armed',
          'drop_bias_neutral','drop_bias_conflict','2_bias_ok',
          'drop_no_retrace','3_FILLED']:
    print(f"  {k:22} {f[k]}")

print("\n4H BIAS AT ARM TIME (of armed setups)")
for k, v in bias_seen.items():
    print(f"  {k:10} {v}")

print("\nDISTRIBUTIONS")
if disp_sizes:
    print(f"  Displacement leg: median ${statistics.median(disp_sizes):.1f} "
          f"(min ${min(disp_sizes):.1f}, max ${max(disp_sizes):.1f})")
if retrace_gap:
    print(f"  No-retrace gap to FVG: median ${statistics.median(retrace_gap):.1f}")