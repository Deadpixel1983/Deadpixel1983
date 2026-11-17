#!/usr/bin/env python3
"""
Intraday-only variants: every position is closed at the session's final bar.

  Breakout day : an hourly bar touches the 60-day high -> buy 100% of cash at the
                 level (or the open if it gapped above), sell at the session close.
  Session dip  : price falls 10% from the SESSION's running high within the same
                 session -> buy 50% of cash at that level; 20% below -> 50% of what
                 is left; sell everything at the session close. A drop that happens
                 overnight (gap) does not count; only the fall inside the session.

On DAILY candles the same rules are approximated from O/H/L/C: a day touched the
60-day high if high >= level; a session dip happened if low <= high*(1-0.10).
Daily data cannot show whether the high came before the low, so the daily dip
count is an upper bound.

Idle cash earns the T-bill yield when --tbill is given.
"""
import argparse, math, importlib.util, os
import numpy as np
import pandas as pd

_spec = importlib.util.spec_from_file_location("dbo", os.path.join(os.path.dirname(os.path.abspath(__file__)), "donchian_breakout.py"))
dbo = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(dbo)
_spec2 = importlib.util.spec_from_file_location("lad", os.path.join(os.path.dirname(os.path.abspath(__file__)), "donchian_ladder.py"))
lad = importlib.util.module_from_spec(_spec2); _spec2.loader.exec_module(lad)


def simulate(df, ch_hi, use_breakout, use_dip, step=0.10, fraction=0.5, cost=0.0002, tbill=None):
    c, h, l, o = df["close"].values, df["high"].values, df["low"].values, df["open"].values
    day = df["day"].values
    last_of_day = np.concatenate([day[1:] != day[:-1], [True]])
    half = cost / 2
    cash, units = 1.0, 0.0
    sess_hi, tiers, in_bo = -np.inf, set(), False
    equity = np.empty(len(df)); invested = np.empty(len(df)); fills = []
    yld, dt_days = tbill if tbill is not None else (None, None)
    for i in range(len(df)):
        if yld is not None and cash > 0: cash *= 1 + yld[i] * dt_days[i] / 365.0
        if i == 0 or day[i] != day[i - 1]:
            sess_hi, tiers, in_bo = -np.inf, set(), False
        sess_hi = max(sess_hi, h[i])
        # session dip tiers: fall from the session high, inside the session
        if use_dip and np.isfinite(sess_hi):
            k = 1
            while True:
                lvl = sess_hi * (1 - step * k)
                if l[i] > lvl: break
                if k not in tiers:
                    px = lvl if o[i] >= lvl else o[i]
                    amt = fraction * cash
                    if amt > 0:
                        units += amt * (1 - half) / px; cash -= amt; tiers.add(k)
                        fills.append({"time": df.index[i], "kind": f"dip tier {k}", "price": px, "amount": amt})
                k += 1
        # breakout day
        if use_breakout and not in_bo and not np.isnan(ch_hi[i]) and h[i] >= ch_hi[i] and cash > 0:
            px = max(ch_hi[i], o[i]); amt = cash
            units += amt * (1 - half) / px; cash -= amt; in_bo = True
            fills.append({"time": df.index[i], "kind": "breakout", "price": px, "amount": amt})
        # flat at the close
        if last_of_day[i] and units > 0:
            cash += units * c[i] * (1 - half)
            fills.append({"time": df.index[i], "kind": "close-out", "price": c[i], "amount": units * c[i]})
            units = 0.0
        equity[i] = cash + units * c[i]; invested[i] = units * c[i] / equity[i]
    return pd.Series(equity, index=df.index), pd.Series(invested, index=df.index), pd.DataFrame(fills)


def line(name, eq, inv, df, fills, bpy):
    yrs = len(eq) / bpy; r = eq.pct_change().dropna()
    n_bo = (fills["kind"] == "breakout").sum() if len(fills) else 0
    n_dip = fills["kind"].str.startswith("dip").sum() if len(fills) else 0
    sh = r.mean() / r.std() * math.sqrt(bpy) if r.std() > 0 else float("nan")
    return f"  {name:<36} total {100*(eq.iloc[-1]-1):>+8.2f}%  CAGR {100*(eq.iloc[-1]**(1/yrs)-1):>+6.2f}%  maxDD {100*(eq/eq.cummax()-1).min():>+6.2f}%  Sharpe {sh:>5.2f}  avg invested {100*inv.mean():>3.0f}%  breakout days {n_bo:>4}  dip fills {n_dip:>3}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv"); ap.add_argument("--daily", default=None); ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--tbill", default=None); ap.add_argument("--cost", type=float, default=0.0002)
    ap.add_argument("--split", default=None); ap.add_argument("--list-dips", action="store_true")
    a = ap.parse_args()
    df = dbo.load_hourly(a.csv); daily = dbo.load_daily(a.daily) if a.daily else None
    ch_hi, _ = dbo.channel_levels(df, daily, a.days)
    tb = lad.load_tbill(a.tbill, df) if a.tbill else None
    bpy = dbo.bars_per_year(df)
    print(f"{a.csv}: {len(df)} bars {df.index[0].date()} -> {df.index[-1].date()}; all positions closed at the session's last bar; T-bills on cash: {'yes' if tb else 'no'}")
    split = pd.Timestamp(a.split, tz="UTC") if a.split else df.index[len(df) // 2]
    for label, sl in (("FULL", slice(None)), ("FIRST HALF", df.index < split), ("SECOND HALF", df.index >= split)):
        d = df[sl]; tbs = (tb[0][sl], tb[1][sl]) if tb else None
        print(f"\n--- {label}: {d.index[0].date()} -> {d.index[-1].date()} ---")
        for name, bo, dp in (("breakout day only", True, False), ("session dip only", False, True), ("breakout day + session dip", True, True)):
            eq, inv, f = simulate(d, ch_hi[sl], bo, dp, cost=a.cost, tbill=tbs)
            print(line(name, eq, inv, d, f, bpy))
        if tbs is not None:
            cash = pd.Series(np.cumprod(1 + tbs[0] * tbs[1] / 365.0), index=d.index); print(line("cash only (T-bills)", cash, pd.Series(0.0, index=d.index), d, pd.DataFrame(), bpy))
        bh = d["close"] / d["close"].iloc[0]; print(line("buy & hold", bh, pd.Series(1.0, index=d.index), d, pd.DataFrame(), bpy))
    if a.list_dips:
        eq, inv, f = simulate(df, ch_hi, False, True, cost=a.cost, tbill=tb)
        dips = f[f["kind"].str.startswith("dip")] if len(f) else f
        print(f"\nSession-dip fills ({len(dips)}):")
        if len(dips): print(dips.to_string(index=False))


if __name__ == "__main__":
    main()
