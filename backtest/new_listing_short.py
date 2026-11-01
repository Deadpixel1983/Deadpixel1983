#!/usr/bin/env python3
"""
Backtest: short every new Binance USDT-margined perpetual N days after listing.

Rule under test
  - Universe: Binance USD(S)-M perpetual contracts on crypto underlyings
    (tokenised stocks / ETFs / commodities excluded via underlyingType).
  - Entry: short at the close of daily candle index ENTRY_DAY, where candle 0 is
    the listing day. ENTRY_DAY=5 means "5 days after listing".
  - Exit: close of candle ENTRY_DAY + H for each horizon H in --horizons.
  - Optional filter (--drop-filter 0.05): only enter if the entry price is at
    least 5% below the launch price (launch price = close of candle 0 by
    default; see --launch-ref).
  - Optional stop (--stop 0.15): exit at the stop level if any daily high in the
    hold window exceeds entry * (1 + stop). Conservative: assumes the stop fills
    at the stop level, not the open of that day.
  - Costs: funding actually paid/received over the hold window (from Binance's
    funding-rate history; positive rate = longs pay shorts) and taker fees on
    entry and exit.

Net return per trade (as a fraction of notional):
    (entry - exit) / entry  +  sum(funding rates in window)  -  2 * fee

Data: only public endpoints on fapi.binance.com, no API key needed. Raw JSON is
cached under --cache so reruns are free.

Known limitation: exchangeInfo only lists contracts that still exist, so perps
that were delisted later are missing (survivorship bias). Delisted new coins
are usually the ones that dumped hardest, so this bias works AGAINST the short
thesis, not for it.

Usage:
    python3 new_listing_short.py --since 2024-01-01
    python3 new_listing_short.py --since 2024-01-01 --drop-filter 0.05 --stop 0.20
"""
import argparse
import csv
import json
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BASE = "https://fapi.binance.com"
DAY_MS = 86_400_000


# ----------------------------------------------------------------------------
# Data access
# ----------------------------------------------------------------------------
def get_json(path, params=None, cache_dir=None, sleep=0.15):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    key = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        key = os.path.join(cache_dir, urllib.parse.quote(url, safe="") + ".json")
        if os.path.exists(key):
            with open(key) as f:
                return json.load(f)
    req = urllib.request.Request(url, headers={"User-Agent": "new-listing-short-backtest/1.0"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.load(r)
            break
        except Exception as e:  # noqa: BLE001
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)
            print(f"  retry {attempt + 1} for {url}: {e}", file=sys.stderr)
    if key:
        with open(key, "w") as f:
            json.dump(data, f)
    time.sleep(sleep)
    return data


def list_new_perps(since_ms, min_age_days, cache_dir):
    info = get_json("/fapi/v1/exchangeInfo", cache_dir=cache_dir, sleep=0)
    now_ms = int(time.time() * 1000)
    out = []
    for s in info["symbols"]:
        if s.get("contractType") != "PERPETUAL":
            continue
        if s.get("quoteAsset") != "USDT":
            continue
        if s.get("underlyingType") not in (None, "COIN"):
            continue  # skip tokenised stocks, indices, etc.
        onboard = int(s.get("onboardDate", 0))
        if onboard < since_ms:
            continue
        if now_ms - onboard < min_age_days * DAY_MS:
            continue  # too young to evaluate the longest horizon
        out.append({"symbol": s["symbol"], "onboard": onboard, "status": s.get("status")})
    out.sort(key=lambda x: x["onboard"])
    return out


def daily_klines(symbol, start_ms, limit, cache_dir):
    rows = get_json(
        "/fapi/v1/klines",
        {"symbol": symbol, "interval": "1d", "startTime": start_ms, "limit": limit},
        cache_dir=cache_dir,
    )
    # [openTime, open, high, low, close, volume, closeTime, ...]
    return [
        {
            "open_time": int(r[0]),
            "open": float(r[1]),
            "high": float(r[2]),
            "low": float(r[3]),
            "close": float(r[4]),
            "volume": float(r[5]),
            "close_time": int(r[6]),
        }
        for r in rows
    ]


def funding_history(symbol, start_ms, end_ms, cache_dir):
    out = []
    cursor = start_ms
    while True:
        rows = get_json(
            "/fapi/v1/fundingRate",
            {"symbol": symbol, "startTime": cursor, "endTime": end_ms, "limit": 1000},
            cache_dir=cache_dir,
        )
        if not rows:
            break
        out.extend({"time": int(r["fundingTime"]), "rate": float(r["fundingRate"])} for r in rows)
        if len(rows) < 1000:
            break
        cursor = int(rows[-1]["fundingTime"]) + 1
    return out


# ----------------------------------------------------------------------------
# Simulation (pure, testable)
# ----------------------------------------------------------------------------
def simulate(candles, funding, entry_day, horizons, fee, drop_filter=None, launch_ref="close0", stop=None):
    """Return a list of trade dicts, one per horizon, or [] if no entry.

    candles: daily candles starting at the listing day (index 0).
    funding: list of {time, rate} covering the whole period.
    """
    if len(candles) <= entry_day:
        return []
    c0 = candles[0]
    launch = {"close0": c0["close"], "open0": c0["open"], "high0": c0["high"]}[launch_ref]
    entry_c = candles[entry_day]
    entry = entry_c["close"]
    entry_time = entry_c["close_time"]
    if drop_filter is not None and entry > launch * (1 - drop_filter):
        return []

    trades = []
    for h in horizons:
        exit_idx = entry_day + h
        if exit_idx >= len(candles):
            continue
        exit_c = candles[exit_idx]
        exit_price = exit_c["close"]
        exit_time = exit_c["close_time"]
        stopped = False
        if stop is not None:
            stop_px = entry * (1 + stop)
            for c in candles[entry_day + 1 : exit_idx + 1]:
                if c["high"] >= stop_px:
                    exit_price, exit_time, stopped = stop_px, c["close_time"], True
                    break
        fund = sum(f["rate"] for f in funding if entry_time < f["time"] <= exit_time)
        price_ret = (entry - exit_price) / entry
        trades.append(
            {
                "horizon": h,
                "entry": entry,
                "exit": exit_price,
                "stopped": stopped,
                "price_ret": price_ret,
                "funding": fund,
                "fees": 2 * fee,
                "net": price_ret + fund - 2 * fee,
                "launch": launch,
                "entry_vs_launch": entry / launch - 1,
            }
        )
    return trades


# ----------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------
def pct(x):
    return f"{100 * x:+.2f}%"


def summarise(rows, horizons):
    print()
    print(f"{'H(days)':>8} {'n':>5} {'mean net':>10} {'median':>10} {'win%':>7} {'avg fund':>10} {'avg px':>10} {'worst':>9} {'best':>9} {'stop%':>6}")
    for h in horizons:
        r = [t for t in rows if t["horizon"] == h]
        if not r:
            continue
        nets = [t["net"] for t in r]
        print(
            f"{h:>8} {len(r):>5} {pct(statistics.mean(nets)):>10} {pct(statistics.median(nets)):>10} "
            f"{100 * sum(n > 0 for n in nets) / len(nets):>6.1f}% "
            f"{pct(statistics.mean(t['funding'] for t in r)):>10} "
            f"{pct(statistics.mean(t['price_ret'] for t in r)):>10} "
            f"{pct(min(nets)):>9} {pct(max(nets)):>9} "
            f"{100 * sum(t['stopped'] for t in r) / len(r):>5.0f}%"
        )


def summarise_by_year(rows, horizon):
    r = [t for t in rows if t["horizon"] == horizon]
    years = sorted({t["year"] for t in r})
    print(f"\nBy listing year, horizon {horizon}d:")
    for y in years:
        ry = [t for t in r if t["year"] == y]
        nets = [t["net"] for t in ry]
        print(f"  {y}: n={len(ry):>3} mean={pct(statistics.mean(nets))} median={pct(statistics.median(nets))} win={100 * sum(n > 0 for n in nets) / len(nets):.0f}%")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2024-01-01", help="only perps onboarded on/after this date (UTC)")
    ap.add_argument("--entry-day", type=int, default=5, help="enter at close of this candle index (0 = listing day)")
    ap.add_argument("--horizons", default="1,2,3,5,7,10,15,20,30", help="hold lengths in days")
    ap.add_argument("--drop-filter", type=float, default=None, help="e.g. 0.05: only enter if entry <= launch*(1-0.05)")
    ap.add_argument("--launch-ref", choices=["close0", "open0", "high0"], default="close0")
    ap.add_argument("--stop", type=float, default=None, help="e.g. 0.2: stop out if price rises 20%% above entry")
    ap.add_argument("--fee", type=float, default=0.0005, help="taker fee per side (0.0005 = 0.05%%)")
    ap.add_argument("--cache", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache"))
    ap.add_argument("--out", default="trades.csv")
    args = ap.parse_args()

    horizons = sorted(int(h) for h in args.horizons.split(","))
    since_ms = int(datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)
    need_days = args.entry_day + max(horizons) + 1

    perps = list_new_perps(since_ms, min_age_days=args.entry_day + 1, cache_dir=args.cache)
    print(f"{len(perps)} USDT perps onboarded since {args.since} (crypto underlyings only)")

    rows = []
    skipped = 0
    for i, p in enumerate(perps, 1):
        sym = p["symbol"]
        candles = daily_klines(sym, p["onboard"], need_days, args.cache)
        if not candles:
            skipped += 1
            continue
        end_ms = candles[-1]["close_time"]
        funding = funding_history(sym, p["onboard"], end_ms, args.cache)
        trades = simulate(candles, funding, args.entry_day, horizons, args.fee, args.drop_filter, args.launch_ref, args.stop)
        listed = datetime.fromtimestamp(p["onboard"] / 1000, tz=timezone.utc)
        for t in trades:
            t.update({"symbol": sym, "listed": listed.strftime("%Y-%m-%d"), "year": listed.year})
        rows.extend(trades)
        if i % 25 == 0:
            print(f"  {i}/{len(perps)} processed", file=sys.stderr)

    if not rows:
        print("No trades generated.")
        return
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    n_syms = len({t["symbol"] for t in rows})
    print(f"\nEntered {n_syms} of {len(perps)} listings (skipped {skipped} with no candles). Entry = close of day {args.entry_day}.")
    if args.drop_filter is not None:
        print(f"Filter: entry must be >= {100 * args.drop_filter:.0f}% below launch ({args.launch_ref}).")
    print(f"Fees: {100 * args.fee:.3f}% per side. Stop: {args.stop if args.stop is not None else 'none'}.")
    print("Columns: mean/median net = price move + funding - fees, as % of notional, from the short's point of view.")
    summarise(rows, horizons)
    for h in (horizons[len(horizons) // 2], horizons[-1]):
        summarise_by_year(rows, h)
    print(f"\nPer-trade rows written to {args.out}")


if __name__ == "__main__":
    main()
