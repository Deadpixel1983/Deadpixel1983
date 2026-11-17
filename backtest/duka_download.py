#!/usr/bin/env python3
"""Resumable downloader for Dukascopy's free historical feed: USA500IDXUSD one-minute
BID candles, one LZMA file per day. The month in the URL is zero-based. Minute data
for this symbol begins 19 September 2011.

    python3 duka_download.py OUT_DIR 2011-09-19 2026-11-16 [workers]

Files already present (or marked .404) are skipped, so the run can be repeated.
"""
import datetime as dt, os, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

OUT = sys.argv[1]
start = dt.date.fromisoformat(sys.argv[2])
end = dt.date.fromisoformat(sys.argv[3])
workers = int(sys.argv[4]) if len(sys.argv) > 4 else 4
UA = "sp500-backtest-data-fetch/1.0"
os.makedirs(OUT, exist_ok=True)


def fetch(d):
    dest = os.path.join(OUT, f"{d.isoformat()}.bi5")
    marker = dest + ".404"
    if os.path.exists(dest) or os.path.exists(marker):
        return "skip"
    url = f"https://datafeed.dukascopy.com/datafeed/USA500IDXUSD/{d.year}/{d.month - 1:02d}/{d.day:02d}/BID_candles_min_1.bi5"
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read()
            if len(data) == 0:
                open(marker, "w").close(); return "missing"
            open(dest, "wb").write(data); return "ok"
        except urllib.error.HTTPError as e:
            if e.code == 404:
                open(marker, "w").close(); return "missing"
            time.sleep(2 * (attempt + 1))
        except Exception:
            time.sleep(2 * (attempt + 1))
    return "failed"


days = []
d = start
while d <= end:
    if d.weekday() < 5:
        days.append(d)
    d += dt.timedelta(days=1)

counts = {"ok": 0, "missing": 0, "failed": 0, "skip": 0}
failed = []
with ThreadPoolExecutor(max_workers=workers) as ex:
    for day, res in zip(days, ex.map(fetch, days)):
        counts[res] += 1
        if res == "failed":
            failed.append(day.isoformat())
        done = sum(counts.values())
        if done % 250 == 0:
            print(f"{done}/{len(days)} {counts}", flush=True)

with open(os.path.join(OUT, "download.log"), "a") as log:
    log.write(f"DONE {start}->{end} {counts} failed_days={failed}\n")
print(f"done {counts}")
if failed:
    print("failed days:", failed)
