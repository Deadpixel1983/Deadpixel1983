#!/usr/bin/env python3
"""
60-day Donchian touch, traded on hourly bars, with a stale-high exit.

Rules
  Channel : the highest HIGH of the previous D completed trading days (default
            60). Built from DAILY candles, so it is the level a daily Donchian
            chart would show. It never includes the current day.
  Entry   : the first hourly bar whose HIGH reaches that level enters long at
            the level itself (--trigger touch, default). --trigger close enters
            at the hourly close when the close is above the level.
            Shorts mirror this on the D-day low (--side short/both).
  Exit    : n hourly bars pass without the trade making a new high (long) /
            new low (short), measured on hourly highs/lows since entry. Exit at
            that bar's close.
  Size    : one position at a time, full notional, no pyramiding.

Inputs
  hourly CSV  : ts_utc, open, high, low, close
  --daily CSV : date, open, high, low, close (optional). Used for the channel so
                the first hourly bars already have a full D-day history. If
                omitted the daily bars are built from the hourly file and the
                first D days are warm-up.

Usage
  python3 donchian_breakout.py gspc_1h.csv --daily sp500_daily.csv --optimize
  python3 donchian_breakout.py gspc_1h.csv --daily sp500_daily.csv --exit 6 --by-year
"""
import argparse, math
import numpy as np
import pandas as pd


def load_hourly(path):
    df = pd.read_csv(path)
    tcol = next(c for c in df.columns if c.lower() in ("ts_utc", "timestamp", "time", "ts", "date"))
    df["t"] = pd.to_datetime(df[tcol], utc=True)
    df = df.set_index("t").sort_index()[["open", "high", "low", "close"]].astype(float).dropna(subset=["close"])
    # trading day in New York time, so the 15:30 ET bar and the 09:30 ET bar belong to the same session
    df["day"] = df.index.tz_convert("America/New_York").date
    return df


def load_daily(path):
    d = pd.read_csv(path)
    d["day"] = pd.to_datetime(d["date"]).dt.date
    return d.set_index("day").sort_index()[["open", "high", "low", "close"]].astype(float)


def channel_levels(hourly, daily, days):
    """Per trading day: highest high / lowest low of the previous `days` completed days."""
    if daily is None:
        daily = hourly.groupby("day").agg(high=("high", "max"), low=("low", "min"))
    hi = daily["high"].rolling(days).max().shift(1)
    lo = daily["low"].rolling(days).min().shift(1)
    lv = pd.DataFrame({"ch_hi": hi, "ch_lo": lo})
    # map onto hourly bars; a session missing from the daily file falls back to the last known level
    sessions = pd.Index(sorted(hourly["day"].unique()))
    lv = lv.reindex(lv.index.union(sessions)).sort_index().ffill()
    return lv.loc[hourly["day"].values, "ch_hi"].values, lv.loc[hourly["day"].values, "ch_lo"].values


def bars_per_year(df):
    return len(df) / ((df.index[-1] - df.index[0]).total_seconds() / (365.25 * 86400))


def backtest(df, ch_hi, ch_lo, n_exit=6, side="long", trigger="touch", cost=0.0002):
    c, h, l = df["close"].values, df["high"].values, df["low"].values
    idx = df.index
    pos, entry_px, entry_i, best, best_i = 0, 0.0, 0, np.nan, 0
    trades, equity = [], np.ones(len(df))
    for i in range(1, len(df)):
        equity[i] = equity[i - 1] * (1 + pos * (c[i] / c[i - 1] - 1)) if pos else equity[i - 1]
        if pos:
            if (pos > 0 and h[i] > best) or (pos < 0 and l[i] < best):
                best, best_i = (h[i] if pos > 0 else l[i]), i
            elif i - best_i >= n_exit:
                trades.append({"entry_time": idx[entry_i], "exit_time": idx[i], "side": "long" if pos > 0 else "short",
                               "level": lvl, "entry": entry_px, "exit": c[i], "bars": i - entry_i,
                               "ret": pos * (c[i] / entry_px - 1) - cost})
                equity[i] *= (1 - cost)
                pos = 0
                continue
        if not pos and not np.isnan(ch_hi[i]) and not np.isnan(ch_lo[i]):
            sig, px = 0, 0.0
            if side in ("long", "both"):
                if trigger == "touch" and h[i] >= ch_hi[i]:
                    sig, px, lvl = 1, max(ch_hi[i], df["open"].values[i]), ch_hi[i]   # gap above the level fills at the open
                elif trigger == "close" and c[i] > ch_hi[i]:
                    sig, px, lvl = 1, c[i], ch_hi[i]
            if not sig and side in ("short", "both"):
                if trigger == "touch" and l[i] <= ch_lo[i]:
                    sig, px, lvl = -1, min(ch_lo[i], df["open"].values[i]), ch_lo[i]
                elif trigger == "close" and c[i] < ch_lo[i]:
                    sig, px, lvl = -1, c[i], ch_lo[i]
            if sig:
                pos, entry_px, entry_i = sig, px, i
                best, best_i = (h[i] if sig > 0 else l[i]), i
                # mark the entry bar from the fill price, not the previous close
                equity[i] = equity[i - 1] * (1 + sig * (c[i] / px - 1))
    if pos:
        trades.append({"entry_time": idx[entry_i], "exit_time": idx[-1], "side": "long" if pos > 0 else "short", "level": lvl,
                       "entry": entry_px, "exit": c[-1], "bars": len(df) - 1 - entry_i, "ret": pos * (c[-1] / entry_px - 1) - cost})
    return pd.DataFrame(trades), pd.Series(equity, index=idx)


def metrics(trades, eq, df):
    bpy = bars_per_year(df)
    years = len(eq) / bpy
    m = {"trades": len(trades)}
    if len(trades):
        r = trades["ret"]
        gp, gl = r[r > 0].sum(), -r[r < 0].sum()
        m.update(win_rate=(r > 0).mean(), avg_ret=r.mean(), median_ret=r.median(),
                 profit_factor=gp / gl if gl > 0 else float("inf"),
                 avg_bars_held=trades["bars"].mean(), exposure=trades["bars"].sum() / len(eq))
    rets = eq.pct_change().dropna()
    bh = df["close"] / df["close"].iloc[0]
    m.update(total_return=eq.iloc[-1] - 1, cagr=eq.iloc[-1] ** (1 / years) - 1, max_drawdown=(eq / eq.cummax() - 1).min(),
             sharpe=rets.mean() / rets.std() * math.sqrt(bpy) if rets.std() > 0 else float("nan"),
             buy_hold_total=bh.iloc[-1] - 1, buy_hold_cagr=bh.iloc[-1] ** (1 / years) - 1,
             buy_hold_max_dd=(bh / bh.cummax() - 1).min(), years=years)
    return m


PCT = {"win_rate", "avg_ret", "median_ret", "exposure", "total_return", "cagr", "max_drawdown", "buy_hold_total", "buy_hold_cagr", "buy_hold_max_dd"}
fmt = lambda k, v: f"{100 * v:+.2f}%" if k in PCT else f"{v:.2f}" if isinstance(v, float) else str(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hourly_csv")
    ap.add_argument("--daily", default=None, help="daily OHLC CSV used to build the channel")
    ap.add_argument("--days", type=int, default=60, help="Donchian lookback in trading days")
    ap.add_argument("--exit", type=int, default=6, help="hourly bars without a new extreme before exiting")
    ap.add_argument("--side", choices=["long", "short", "both"], default="long")
    ap.add_argument("--trigger", choices=["touch", "close"], default="touch")
    ap.add_argument("--cost", type=float, default=0.0002, help="round-trip friction fraction")
    ap.add_argument("--optimize", action="store_true")
    ap.add_argument("--split", default=None, help="ISO date for the in/out-of-sample split (default midpoint)")
    ap.add_argument("--trades-out", default=None)
    ap.add_argument("--by-year", action="store_true")
    a = ap.parse_args()

    df = load_hourly(a.hourly_csv)
    daily = load_daily(a.daily) if a.daily else None
    ch_hi, ch_lo = channel_levels(df, daily, a.days)
    ok = ~np.isnan(ch_hi)
    print(f"{a.hourly_csv}: {len(df)} hourly bars, {df.index[0].date()} -> {df.index[-1].date()}; channel = {a.days}-day high/low from {'daily file' if daily is not None else 'hourly data'}; {ok.sum()} bars have a level")

    if a.optimize:
        split = pd.Timestamp(a.split, tz="UTC") if a.split else df.index[len(df) // 2]
        mask = df.index < split
        parts = [(df[mask], ch_hi[mask], ch_lo[mask]), (df[~mask], ch_hi[~mask], ch_lo[~mask])]
        grid = [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 17, 21, 25, 30, 35, 42, 50, 60, 75, 90, 120]
        print(f"\nOptimise exit n. side={a.side}, trigger={a.trigger}, cost={a.cost}")
        print(f"In-sample {parts[0][0].index[0].date()} -> {parts[0][0].index[-1].date()} ({len(parts[0][0])} bars) | Out-of-sample {parts[1][0].index[0].date()} -> {parts[1][0].index[-1].date()} ({len(parts[1][0])} bars)")
        print(f"{'n':>4} | {'IS trades':>9} {'IS win%':>7} {'IS PF':>6} {'IS ret':>8} {'IS DD':>8} {'IS Sharpe':>9} | {'OOS trades':>10} {'OOS win%':>8} {'OOS PF':>6} {'OOS ret':>8} {'OOS DD':>8} {'OOS Sharpe':>10}")
        cell = lambda m: f"{m['trades']:>9} {100*m.get('win_rate', float('nan')):>6.1f}% {m.get('profit_factor', float('nan')):>6.2f} {100*m['total_return']:>+7.2f}% {100*m['max_drawdown']:>+7.2f}% {m['sharpe']:>9.2f}"
        rows = []
        for n in grid:
            mi, mo = [metrics(*backtest(p, hi, lo, n, a.side, a.trigger, a.cost), p) for p, hi, lo in parts]
            rows.append((n, mi, mo))
            print(f"{n:>4} | {cell(mi)} | {cell(mo):>58}")
        best = max((r for r in rows if r[1]["trades"] >= 10), key=lambda r: r[1]["sharpe"], default=None)
        if best:
            n, mi, mo = best
            print(f"\nBest in-sample n by Sharpe (>=10 trades): n={n}  IS Sharpe {mi['sharpe']:.2f} -> OOS Sharpe {mo['sharpe']:.2f}, OOS return {100*mo['total_return']:+.2f}%")
        for name, (p, _, _) in zip(("IS", "OOS"), parts):
            b = metrics(pd.DataFrame(), pd.Series(np.ones(len(p)), index=p.index), p)
            print(f"Buy & hold {name}: {100*b['buy_hold_total']:+.2f}% (DD {100*b['buy_hold_max_dd']:+.2f}%)")
        return

    tr, eq = backtest(df, ch_hi, ch_lo, a.exit, a.side, a.trigger, a.cost)
    m = metrics(tr, eq, df)
    print(f"\n{a.days}-day Donchian {a.trigger} entry, exit after {a.exit} hourly bars without a new extreme, side={a.side}, cost={a.cost}")
    for k, v in m.items():
        print(f"  {k:<16} {fmt(k, v)}")
    if len(tr) and a.by_year:
        tr["year"] = pd.to_datetime(tr["exit_time"]).dt.year
        print("\nBy exit year:")
        for y, s in tr.groupby("year")["ret"]:
            print(f"  {y}: n={len(s):>3} sum={100*s.sum():+7.2f}% win={100*(s>0).mean():5.1f}%")
    if len(tr) and a.trades_out:
        tr.drop(columns=["year"], errors="ignore").to_csv(a.trades_out, index=False)
        print(f"\nTrades written to {a.trades_out}")


if __name__ == "__main__":
    main()
