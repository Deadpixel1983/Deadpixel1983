#!/usr/bin/env python3
"""
60-day Donchian touch entry + drawdown ladder, with explicit capital accounting.

Capital
  One asset, one pot. Equity = cash + units * price. Every fill is sized from
  the cash available at that moment.

Rule 1 - breakout (from donchian_breakout.py)
  Hourly bar touches the 60-day high -> buy with 100% of available cash at the
  level. Exit when n TRADING hours pass without a new high; sell at that close.

Rule 2 - ladder
  Reference high R = the 60-day high (--ref channel, default) or the running
  all-time high (--ref ath). When the bar's low reaches R*(1-0.10) buy with
  50% of available cash at that level. At R*(1-0.20) buy 50% of what is left,
  at R*(1-0.30) 50% of what is left, and so on (--step / --fraction). Each tier
  fires once per ladder cycle; the ladder re-arms when all ladder units are
  sold.
  Ladder exit (--ladder-exit):
    recover : sell all ladder units when the bar's high reaches R again
    stale   : same n-trading-hour stale rule as the breakout, on the ladder book

Costs: --cost is round trip; half is charged on each fill.

Idle cash: with --tbill CSV (date, yield_pct; e.g. Yahoo ^IRX, the 13-week bill)
cash accrues at that annualised yield over elapsed calendar days, weekends
included, so the Monday bar receives three days of interest.

Usage
  python3 donchian_ladder.py gspc_1h.csv --daily sp500_daily.csv --exit 48
  python3 donchian_ladder.py gspc_1h.csv --daily sp500_daily.csv --exit 48 --ref ath --ladder-exit stale
  python3 donchian_ladder.py gspc_1h.csv --daily sp500_daily.csv --exit 48 --no-breakout   (ladder only)
"""
import argparse, math, importlib.util, os
import numpy as np
import pandas as pd

_spec = importlib.util.spec_from_file_location("dbo", os.path.join(os.path.dirname(os.path.abspath(__file__)), "donchian_breakout.py"))
dbo = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(dbo)


def ath_levels(hourly, daily):
    """Running all-time high as of the previous bar (daily history seeds it)."""
    seed = daily.loc[: hourly["day"].iloc[0]].iloc[:-1]["high"].max() if daily is not None else -np.inf
    run = np.maximum.accumulate(np.concatenate([[seed], hourly["high"].values[:-1]]))
    return run


def open_time(df):
    idx = df.index
    tsec = (idx - pd.Timestamp(0, tz="UTC")).total_seconds().to_numpy()
    day = df["day"].values
    dur = np.full(len(df), 1800.0)
    same = day[1:] == day[:-1]
    dur[:-1][same] = np.minimum(tsec[1:] - tsec[:-1], 3600.0)[same]
    return np.concatenate([[0.0], np.cumsum(dur)])


def load_tbill(path, df):
    """Annualised yield fraction aligned to df's bars (forward-filled), and elapsed days per bar for accrual."""
    t = pd.read_csv(path); t["date"] = pd.to_datetime(t["date"]).dt.date
    y = t.set_index("date")["yield_pct"].astype(float) / 100
    days = df["day"].values
    y = y.reindex(y.index.union(pd.Index(sorted(set(days))))).sort_index().ffill().bfill()
    yld = y.loc[days].values  # one yield per bar; hourly bars on the same date share it
    tsec = (df.index - pd.Timestamp(0, tz="UTC")).total_seconds().to_numpy()
    dt_days = np.concatenate([[0.0], np.diff(tsec) / 86400.0])
    return yld, dt_days


def simulate(df, ch_hi, ref_hi, n_exit=48, step=0.10, fraction=0.5, ladder_exit="recover", use_breakout=True, use_ladder=True, cost=0.0002, tbill=None):
    c, h, l, o = df["close"].values, df["high"].values, df["low"].values, df["open"].values
    oc = open_time(df)
    half = cost / 2
    cash, bu, lu = 1.0, 0.0, 0.0            # cash, breakout units, ladder units
    b_best, b_best_i = np.nan, 0
    l_best, l_best_i = np.nan, 0
    tiers_filled = set()
    equity = np.empty(len(df)); invested = np.empty(len(df))
    fills = []

    def buy(i, px, amount, kind, note=""):
        nonlocal cash
        amount = min(amount, cash)
        if amount <= 0: return 0.0
        units = amount * (1 - half) / px
        cash -= amount
        fills.append({"time": df.index[i], "kind": kind, "side": "buy", "price": px, "amount": amount, "units": units, "note": note})
        return units

    def sell(i, px, units, kind, note=""):
        nonlocal cash
        if units <= 0: return
        cash += units * px * (1 - half)
        fills.append({"time": df.index[i], "kind": kind, "side": "sell", "price": px, "amount": units * px, "units": units, "note": note})

    yld, dt_days = tbill if tbill is not None else (None, None)
    for i in range(len(df)):
        R = ref_hi[i]; CH = ch_hi[i]
        if yld is not None and cash > 0:
            cash *= 1 + yld[i] * dt_days[i] / 365.0   # interest on idle cash for the time since the previous bar
        # ---- exits first
        if bu > 0:
            if h[i] > b_best: b_best, b_best_i = h[i], i
            elif oc[i + 1] - oc[b_best_i + 1] >= n_exit * 3600:
                sell(i, c[i], bu, "breakout", "stale"); bu = 0.0
        if lu > 0:
            if ladder_exit == "recover":
                if not np.isnan(R) and h[i] >= R:
                    sell(i, max(R, o[i]), lu, "ladder", "recovered to ref high"); lu = 0.0; tiers_filled.clear(); l_best = np.nan
            else:
                if h[i] > l_best: l_best, l_best_i = h[i], i
                elif oc[i + 1] - oc[l_best_i + 1] >= n_exit * 3600:
                    sell(i, c[i], lu, "ladder", "stale"); lu = 0.0; tiers_filled.clear(); l_best = np.nan
        # ---- ladder entries (deepest reachable tiers this bar, in order)
        if use_ladder and not np.isnan(R):
            k = 1
            while True:
                lvl = R * (1 - step * k)
                if lvl <= 0 or l[i] > lvl: break
                if k not in tiers_filled:
                    px = min(lvl, o[i])  # gapped below the level: fill at the open
                    u = buy(i, px, fraction * cash, "ladder", f"tier {k}: {100*step*k:.0f}% below ref")
                    if u > 0:
                        lu += u; tiers_filled.add(k)
                        if np.isnan(l_best) or h[i] > l_best: l_best, l_best_i = h[i], i
                k += 1
        # ---- breakout entry
        if use_breakout and bu == 0 and not np.isnan(CH) and h[i] >= CH and cash > 0:
            px = max(CH, o[i])
            u = buy(i, px, cash, "breakout", "touched 60-day high")
            if u > 0: bu = u; b_best, b_best_i = h[i], i
        equity[i] = cash + (bu + lu) * c[i]
        invested[i] = (bu + lu) * c[i] / equity[i]
    return pd.Series(equity, index=df.index), pd.Series(invested, index=df.index), pd.DataFrame(fills)


def report(name, eq, inv, fills, df):
    bpy = dbo.bars_per_year(df); years = len(eq) / bpy
    rets = eq.pct_change().dropna()
    dd = (eq / eq.cummax() - 1).min()
    bh = df["close"] / df["close"].iloc[0]
    buys = fills[fills.side == "buy"] if len(fills) else fills
    nb = (buys.kind == "breakout").sum() if len(buys) else 0
    nl = (buys.kind == "ladder").sum() if len(buys) else 0
    print(f"{name:<34} ret {100*(eq.iloc[-1]-1):>+7.2f}%  CAGR {100*(eq.iloc[-1]**(1/years)-1):>+6.2f}%  maxDD {100*dd:>+7.2f}%  Sharpe {rets.mean()/rets.std()*math.sqrt(bpy) if rets.std()>0 else float('nan'):>5.2f}  avg invested {100*inv.mean():>4.0f}%  buys: breakout {nb:>2}, ladder {nl:>2}")
    return bh


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hourly_csv"); ap.add_argument("--daily", default=None)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--exit", type=int, default=48, help="trading hours without a new high")
    ap.add_argument("--ref", choices=["channel", "ath"], default="channel", help="reference high for the ladder")
    ap.add_argument("--step", type=float, default=0.10); ap.add_argument("--fraction", type=float, default=0.5)
    ap.add_argument("--ladder-exit", choices=["recover", "stale"], default="recover")
    ap.add_argument("--no-breakout", action="store_true"); ap.add_argument("--no-ladder", action="store_true")
    ap.add_argument("--cost", type=float, default=0.0002)
    ap.add_argument("--fills-out", default=None)
    ap.add_argument("--tbill", default=None, help="CSV of date,yield_pct for interest on idle cash (e.g. Yahoo ^IRX)")
    ap.add_argument("--compare", action="store_true", help="run breakout-only, ladder-only and combined, for both refs and both ladder exits")
    a = ap.parse_args()

    df = dbo.load_hourly(a.hourly_csv)
    daily = dbo.load_daily(a.daily) if a.daily else None
    ch_hi, _ = dbo.channel_levels(df, daily, a.days)
    refs = {"channel": ch_hi, "ath": ath_levels(df, daily)}
    tb = load_tbill(a.tbill, df) if a.tbill else None
    if tb is not None: print(f"idle cash earns the 13-week T-bill yield from {a.tbill} (avg {100*tb[0].mean():.2f}% over this sample)")
    print(f"{len(df)} hourly bars {df.index[0].date()} -> {df.index[-1].date()}; exit n = {a.exit} trading hours; ladder step {100*a.step:.0f}%, {100*a.fraction:.0f}% of available cash per tier; cost {a.cost} round trip")
    split = df.index[len(df) // 2]
    if a.compare:
        for label, sl in (("FULL 2 YEARS", slice(None)), ("YEAR 1", df.index < split), ("YEAR 2", df.index >= split)):
            d = df[sl]; print(f"\n--- {label}: {d.index[0].date()} -> {d.index[-1].date()} ---")
            tbs = (tb[0][sl], tb[1][sl]) if tb is not None else None
            if tb is not None:
                cash_eq = pd.Series(np.cumprod(1 + tbs[0] * tbs[1] / 365.0), index=d.index)
                print(f"{'cash only (T-bills)':<34} ret {100*(cash_eq.iloc[-1]-1):>+7.2f}%")
            eq, inv, f = simulate(d, ch_hi[sl], refs["channel"][sl], a.exit, a.step, a.fraction, "recover", True, False, a.cost, tbs)
            bh = report("breakout only (100% capital)", eq, inv, f, d)
            for ref in ("channel", "ath"):
                for lx in ("recover", "stale"):
                    eq, inv, f = simulate(d, ch_hi[sl], refs[ref][sl], a.exit, a.step, a.fraction, lx, False, True, a.cost, tbs)
                    report(f"ladder only   ref={ref:<7} exit={lx}", eq, inv, f, d)
                    eq, inv, f = simulate(d, ch_hi[sl], refs[ref][sl], a.exit, a.step, a.fraction, lx, True, True, a.cost, tbs)
                    report(f"breakout+ladder ref={ref:<7} exit={lx}", eq, inv, f, d)
            print(f"{'buy & hold':<34} ret {100*(bh.iloc[-1]-1):>+7.2f}%  maxDD {100*(bh/bh.cummax()-1).min():>+7.2f}%")
        return
    eq, inv, fills = simulate(df, ch_hi, refs[a.ref], a.exit, a.step, a.fraction, a.ladder_exit, not a.no_breakout, not a.no_ladder, a.cost, tb)
    report(f"ref={a.ref} ladder-exit={a.ladder_exit}", eq, inv, fills, df)
    if len(fills):
        print("\nFills:"); print(fills.to_string(index=False, formatters={"price": "{:.2f}".format, "amount": "{:.4f}".format, "units": "{:.6f}".format}))
    if a.fills_out and len(fills):
        fills.to_csv(a.fills_out, index=False); print(f"\nfills written to {a.fills_out}")


if __name__ == "__main__":
    main()
