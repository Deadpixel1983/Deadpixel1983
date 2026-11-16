#!/usr/bin/env python3
"""
Donchian breakout backtest with a stale-extreme exit.

Rules (exactly three)
  Entry : bar CLOSE above the prior N-bar high -> long
          bar CLOSE below the prior N-bar low  -> short (only with --side short/both)
          The channel is built from the N bars BEFORE the current bar.
  Exit  : n bars pass without the trade making a new high (long) or new low
          (short), measured on bar highs/lows since entry.
  Size  : one position at a time, full notional, no pyramiding.

--optimize sweeps n with an in-sample / out-of-sample split so the chosen n is
checked on data it was not fitted to.

Costs are --cost as a round-trip fraction (0.0002 = 2 bps). Fills are at the
signal bar's close by default; --fill next_open is the pessimistic variant.

Usage
  python3 donchian_breakout.py data.csv --entry 60 --side long --optimize
  python3 donchian_breakout.py data.csv --entry 60 --exit 6 --side long --by-year
"""
import argparse, math
import numpy as np
import pandas as pd


def load(path):
    df = pd.read_csv(path)
    tcol = next(c for c in df.columns if c.lower() in ("ts_utc", "date", "timestamp", "time", "ts"))
    df["t"] = pd.to_datetime(df[tcol], utc=True)
    df = df.set_index("t").sort_index()
    return df[["open", "high", "low", "close"]].astype(float).dropna(subset=["close"])


def bars_per_year(df):
    span_years = (df.index[-1] - df.index[0]).total_seconds() / (365.25 * 86400)
    return len(df) / span_years


def backtest(df, n_entry=60, n_exit=6, side="long", cost=0.0002, fill="close"):
    c, h, l, op = df["close"].values, df["high"].values, df["low"].values, df["open"].values
    dh = df["high"].rolling(n_entry).max().shift(1).values
    dl = df["low"].rolling(n_entry).min().shift(1).values
    idx = df.index
    pos, entry_px, entry_i = 0, 0.0, 0
    best, best_i = np.nan, 0            # running extreme of the trade and the bar that set it
    trades, equity = [], np.ones(len(df))
    for i in range(1, len(df)):
        equity[i] = equity[i - 1] * (1 + pos * (c[i] / c[i - 1] - 1)) if pos else equity[i - 1]
        if pos:
            if (pos > 0 and h[i] > best) or (pos < 0 and l[i] < best):
                best, best_i = (h[i] if pos > 0 else l[i]), i
            elif i - best_i >= n_exit:
                exit_px = c[i] if fill == "close" or i + 1 >= len(df) else op[i + 1]
                trades.append({"entry_time": idx[entry_i], "exit_time": idx[i], "side": "long" if pos > 0 else "short",
                               "entry": entry_px, "exit": exit_px, "bars": i - entry_i, "ret": pos * (exit_px / entry_px - 1) - cost})
                equity[i] *= (1 - cost)
                pos = 0
                continue
        if not pos and not np.isnan(dh[i]) and not np.isnan(dl[i]):
            sig = 1 if side in ("long", "both") and c[i] > dh[i] else -1 if side in ("short", "both") and c[i] < dl[i] else 0
            if sig:
                if fill == "close":
                    pos, entry_px, entry_i = sig, c[i], i
                elif i + 1 < len(df):
                    pos, entry_px, entry_i = sig, op[i + 1], i + 1
                else:
                    continue
                best, best_i = (h[i] if sig > 0 else l[i]), i
    if pos:
        trades.append({"entry_time": idx[entry_i], "exit_time": idx[-1], "side": "long" if pos > 0 else "short",
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


def fmt(k, v):
    return f"{100 * v:+.2f}%" if k in PCT else f"{v:.2f}" if isinstance(v, float) else str(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--entry", type=int, default=60, help="Donchian lookback in bars")
    ap.add_argument("--exit", type=int, default=6, help="bars without a new extreme before exiting")
    ap.add_argument("--side", choices=["long", "short", "both"], default="long")
    ap.add_argument("--cost", type=float, default=0.0002, help="round-trip friction fraction")
    ap.add_argument("--fill", choices=["close", "next_open"], default="close")
    ap.add_argument("--optimize", action="store_true", help="sweep the exit n with an in-sample / out-of-sample split")
    ap.add_argument("--split", default=None, help="ISO date for the split (default: midpoint)")
    ap.add_argument("--trades-out", default=None)
    ap.add_argument("--by-year", action="store_true")
    a = ap.parse_args()

    df = load(a.csv)
    print(f"{a.csv}: {len(df)} bars, {df.index[0].date()} -> {df.index[-1].date()}, ~{bars_per_year(df):.0f} bars/year")

    if a.optimize:
        split = pd.Timestamp(a.split, tz="UTC") if a.split else df.index[len(df) // 2]
        ins, oos = df[df.index < split], df[df.index >= split]
        grid = [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 17, 21, 25, 30, 35, 42, 50, 60, 75, 90, 120]
        print(f"\nOptimise exit n (bars without a new extreme). entry={a.entry}, side={a.side}, cost={a.cost}, fill={a.fill}")
        print(f"In-sample {ins.index[0].date()} -> {ins.index[-1].date()} ({len(ins)} bars) | Out-of-sample {oos.index[0].date()} -> {oos.index[-1].date()} ({len(oos)} bars)")
        print(f"{'n':>4} | {'IS trades':>9} {'IS win%':>7} {'IS PF':>6} {'IS ret':>8} {'IS DD':>8} {'IS Sharpe':>9} | {'OOS trades':>10} {'OOS win%':>8} {'OOS PF':>6} {'OOS ret':>8} {'OOS DD':>8} {'OOS Sharpe':>10}")
        rows = []
        cell = lambda m: f"{m['trades']:>9} {100*m.get('win_rate', float('nan')):>6.1f}% {m.get('profit_factor', float('nan')):>6.2f} {100*m['total_return']:>+7.2f}% {100*m['max_drawdown']:>+7.2f}% {m['sharpe']:>9.2f}"
        for n in grid:
            mi = metrics(*backtest(ins, a.entry, n, a.side, a.cost, a.fill), ins)
            mo = metrics(*backtest(oos, a.entry, n, a.side, a.cost, a.fill), oos)
            rows.append((n, mi, mo))
            print(f"{n:>4} | {cell(mi)} | {cell(mo):>58}")
        best = max((r for r in rows if r[1]["trades"] >= 10), key=lambda r: r[1]["sharpe"], default=None)
        if best:
            n, mi, mo = best
            print(f"\nBest in-sample n by Sharpe (>=10 trades): n={n}  IS Sharpe {mi['sharpe']:.2f} -> OOS Sharpe {mo['sharpe']:.2f}, OOS return {100*mo['total_return']:+.2f}%")
        bi, bo = metrics(pd.DataFrame(), pd.Series(np.ones(len(ins)), index=ins.index), ins), metrics(pd.DataFrame(), pd.Series(np.ones(len(oos)), index=oos.index), oos)
        print(f"Buy & hold: IS {100*bi['buy_hold_total']:+.2f}% (DD {100*bi['buy_hold_max_dd']:+.2f}%) | OOS {100*bo['buy_hold_total']:+.2f}% (DD {100*bo['buy_hold_max_dd']:+.2f}%)")
        return

    tr, eq = backtest(df, a.entry, a.exit, a.side, a.cost, a.fill)
    m = metrics(tr, eq, df)
    print(f"\nDonchian entry {a.entry} / exit after {a.exit} bars without a new extreme, side={a.side}, fill={a.fill}, cost={a.cost}")
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
