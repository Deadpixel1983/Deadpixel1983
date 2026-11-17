#!/usr/bin/env python3
"""Decode Dukascopy one-minute USA500IDXUSD files and build US-session hourly bars
in the same layout as the Yahoo hourly file (ts_utc, open, high, low, close, volume).

Bars start at 09:30 New York time and run 09:30-10:30 ... 14:30-15:30, 15:30-16:00,
matching Yahoo's seven bars per session. Prices in the files are scaled by 1000.

    python3 duka_to_hourly.py RAW_DIR OUT_CSV [CALENDAR_DAILY_CSV]

With a daily OHLC file as the third argument, only sessions that appear in it are
kept, which removes the thin CFD sessions on US market holidays.
"""
import datetime as dt, glob, lzma, os, struct, sys
from zoneinfo import ZoneInfo

RAW, OUT = sys.argv[1], sys.argv[2]
CAL = None
if len(sys.argv) > 3:
    import csv
    CAL = {dt.date.fromisoformat(r["date"]) for r in csv.DictReader(open(sys.argv[3]))}
NY = ZoneInfo("America/New_York")
rows = []
bad = 0
for path in sorted(glob.glob(os.path.join(RAW, "*.bi5"))):
    day = dt.date.fromisoformat(os.path.basename(path)[:10])
    if CAL is not None and day not in CAL:
        continue
    try:
        raw = lzma.decompress(open(path, "rb").read())
    except Exception:
        bad += 1
        continue
    base = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)
    sess_open = dt.datetime(day.year, day.month, day.day, 9, 30, tzinfo=NY)
    sess_close = dt.datetime(day.year, day.month, day.day, 16, 0, tzinfo=NY)
    buckets = {}
    for k in range(0, len(raw), 24):
        off, o, c, l, h, v = struct.unpack(">iiiiif", raw[k : k + 24])
        if v == 0 and o == c == l == h:
            continue  # untraded minute padding
        t = base + dt.timedelta(seconds=off)
        if not (sess_open <= t < sess_close):
            continue
        b = int((t - sess_open).total_seconds() // 3600)  # 0..6, bucket 6 is the 15:30-16:00 half hour
        bk = buckets.setdefault(b, [o, h, l, c, 0.0])
        bk[1] = max(bk[1], h); bk[2] = min(bk[2], l); bk[3] = c; bk[4] += v
    for b in sorted(buckets):
        o, h, l, c, v = buckets[b]
        ts = (sess_open + dt.timedelta(hours=b)).astimezone(dt.timezone.utc)
        rows.append((ts.isoformat(), o / 1000, h / 1000, l / 1000, c / 1000, round(v, 4)))

with open(OUT, "w") as f:
    f.write("ts_utc,open,high,low,close,volume\n")
    for r in rows:
        f.write(",".join(str(x) for x in r) + "\n")
print(f"{len(rows)} hourly bars from {len(glob.glob(os.path.join(RAW, '*.bi5')))} day files ({bad} undecodable); {rows[0][0] if rows else ''} -> {rows[-1][0] if rows else ''}")
