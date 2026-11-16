#!/usr/bin/env python3
"""
Donchian breakout backtest (S&P 500 or anything with OHLC candles).

Rules
  Entry  : bar CLOSE breaks above the prior N-bar high  -> long
           bar CLOSE breaks below the prior N-bar low   -> short (if enabled)
           The channel uses the N bars BEFORE the current bar, so the current
           close is compared against a level it could not itself have set.
  Exit   : --exit-mode stale (default): N bars pass without the trade making a
           new high (long) / new low (short), measured on bar highs/lows since
           entry. --exit-mode channel: close through the opposite M-bar channel.
           Either can be combined with an ATR trailing stop and a max hold.
  Fill   : at the signal bar's close by default (--fill next_open to be stricter).
  Sizing : one position at a time, full notional, no pyramiding.
  Costs  : --cost is round-trip friction as a fraction (0.0002 = 2 bps/side).
  Filter : --squeeze P only takes entries when 20-bar Bollinger bandwidth is in
           the bottom P percent of its trailing 200-bar range (compression).

Usage
  python3 donchian_breakout.py data.csv --entry 60 --exit 20 --side long
  python3 donchian_breakout.py data.csv --sweep
"""
import argparse, math, sys
import numpy as np
import pandas as pd


def load(path):
    df = pd.read_csv(path)
    tcol = next(c for c in df.columns if c.lower() in ("ts_utc", "date", "timestamp", "time", "ts"))
    df["t"] = pd.to_datetime(df[tcol], utc=True)
    df = df.set_index("t").sort_index()
    df = df[["open", "high", "low", "close"] + (["volume"] if "volume" in df else [])].astype(float)
    return df.dropna(subset=["close"])


def bars_per_year(df):
    span_years = (df.index[-1] - df.index[0]).total_seconds() / (365.25 * 86400)
    return len(df) / span_years


def indicators(df, n_entry, n_exit, atr_len=14):
    o = df.copy()
    o["don_hi"] = o["high"].rolling(n_entry).max().shift(1)
    o["don_lo"] = o["low"].rolling(n_entry).min().shift(1)
    o["ex_hi"] = o["high"].rolling(n_exit).max().shift(1)
    o["ex_lo"] = o["low"].rolling(n_exit).min().shift(1)
    tr = pd.concat([o["high"] - o["low"], (o["high"] - o["close"].shift()).abs(), (o["low"] - o["close"].shift()).abs()], axis=1).max(axis=1)
    o["atr"] = tr.ewm(alpha=1 / atr_len, adjust=False).mean()
    ma, sd = o["close"].rolling(20).mean(), o["close"].rolling(20).std()
    bw = (4 * sd) / ma
    o["bw_pct"] = bw.rolling(200).rank(pct=True) * 100
    return o


def backtest(df, n_entry=60, n_exit=20, side="long", atr_stop=None, max_hold=None, cost=0.0002, squeeze=None, fill="close", exit_mode="stale"):
    d = indicators(df, n_entry, n_exit)
    c, h, l, op = d["close"].values, d["high"].values, d["low"].values, d["open"].values
    dh, dl, xh, xl, atr, bw = d["don_hi"].values, d["don_lo"].values, d["ex_hi"].values, d["ex_lo"].values, d["atr"].values, d["bw_pct"].values
    idx = d.index
    pos, entry_px, entry_i, stop = 0, 0.0, 0, np.nan
    best, best_i = np.nan, 0   # running extreme of the trade and the bar it was set
    trades = []
    equity = np.ones(len(d))
    for i in range(1, len(d)):
        # mark to market
        if pos != 0:
            equity[i] = equity[i - 1] * (1 + pos * (c[i] / c[i - 1] - 1))
        else:
            equity[i] = equity[i - 1]
        if pos != 0:
            # trailing stop update
            if atr_stop is not None and not np.isnan(atr[i]):
                stop = max(stop, c[i] - atr_stop * atr[i]) if pos > 0 else min(stop, c[i] + atr_stop * atr[i])
            reason = None
            if exit_mode == "channel":
                if pos > 0 and not np.isnan(xl[i]) and c[i] < xl[i]: reason = "channel"
                if pos < 0 and not np.isnan(xh[i]) and c[i] > xh[i]: reason = "channel"
            else:  # stale: n_exit bars without a new extreme
                if (pos > 0 and h[i] > best) or (pos < 0 and l[i] < best):
                    best, best_i = (h[i] if pos > 0 else l[i]), i
                elif i - best_i >= n_exit:
                    reason = "stale"
            if atr_stop is not None and ((pos > 0 and c[i] < stop) or (pos < 0 and c[i] > stop)): reason = "atr_stop"
            if max_hold is not None and i - entry_i >= max_hold: reason = "max_hold"
            if reason:
                exit_px = c[i] if fill == "close" or i + 1 >= len(d) else op[i + 1]
                ret = pos * (exit_px / entry_px - 1) - cost
                trades.append({"entry_time": idx[entry_i], "exit_time": idx[i], "side": "long" if pos > 0 else "short", "entry": entry_px, "exit": exit_px, "bars": i - entry_i, "ret": ret, "reason": reason})
                equity[i] *= (1 - cost)
                pos = 0
                continue
        if pos == 0 and not np.isnan(dh[i]) and not np.isnan(dl[i]):
            if squeeze is not None and (np.isnan(bw[i]) or bw[i] > squeeze):
                continue
            sig = 0
            if side in ("long", "both") and c[i] > dh[i]: sig = 1
            elif side in ("short", "both") and c[i] < dl[i]: sig = -1
            if sig:
                if fill == "close":
                    pos, entry_px, entry_i = sig, c[i], i
                elif i + 1 < len(d):
                    pos, entry_px, entry_i = sig, op[i + 1], i + 1
                else:
                    continue
                best, best_i = (h[i] if sig > 0 else l[i]), i
                stop = (entry_px - atr_stop * atr[i]) if (atr_stop is not None and sig > 0) else (entry_px + atr_stop * atr[i]) if atr_stop is not None else np.nan
    if pos != 0:  # close open position at the end
        ret = pos * (c[-1] / entry_px - 1) - cost
        trades.append({"entry_time": idx[entry_i], "exit_time": idx[-1], "side": "long" if pos > 0 else "short", "entry": entry_px, "exit": c[-1], "bars": len(d) - 1 - entry_i, "ret": ret, "reason": "end"})
    eq = pd.Series(equity, index=idx)
    return pd.DataFrame(trades), eq, d


def metrics(trades, eq, df):
    bpy = bars_per_year(df)
    years = len(eq) / bpy
    out = {}
    out["trades"] = len(trades)
    if len(trades):
        r = trades["ret"]
        out["win_rate"] = (r > 0).mean()
        out["avg_ret"] = r.mean()
        out["median_ret"] = r.median()
        gp, gl = r[r > 0].sum(), -r[r < 0].sum()
        out["profit_factor"] = gp / gl if gl > 0 else float("inf")
        out["avg_bars_held"] = trades["bars"].mean()
        out["exposure"] = trades["bars"].sum() / len(eq)
    out["total_return"] = eq.iloc[-1] - 1
    out["cagr"] = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else float("nan")
    dd = eq / eq.cummax() - 1
    out["max_drawdown"] = dd.min()
    rets = eq.pct_change().dropna()
    out["sharpe"] = rets.mean() / rets.std() * math.sqrt(bpy) if rets.std() > 0 else float("nan")
    bh = df["close"].iloc[-1] / df["close"].iloc[0]
    out["buy_hold_total"] = bh - 1
    out["buy_hold_cagr"] = bh ** (1 / years) - 1
    bh_eq = df["close"] / df["close"].iloc[0]
    out["buy_hold_max_dd"] = (bh_eq / bh_eq.cummax() - 1).min()
    out["years"] = years
    return out


def fmt(k, v):
    pct_keys = {"win_rate", "avg_ret", "median_ret", "exposure", "total_return", "cagr", "max_drawdown", "buy_hold_total", "buy_hold_cagr", "buy_hold_max_dd"}
    if k in pct_keys: return f"{100 * v:+.2f}%"
    if isinstance(v, float): return f"{v:.2f}"
    return str(v)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--entry", type=int, default=60)
    ap.add_argument("--exit", type=int, default=20, help="stale: bars without a new extreme; channel: channel length")
    ap.add_argument("--exit-mode", choices=["stale", "channel"], default="stale")
    ap.add_argument("--optimize", action="store_true", help="sweep the exit n with an in-sample / out-of-sample split")
    ap.add_argument("--split", default=None, help="ISO date for the in/out-of-sample split (default: midpoint)")
    ap.add_argument("--side", choices=["long", "short", "both"], default="long")
    ap.add_argument("--atr-stop", type=float, default=None, help="trailing stop in ATR multiples, e.g. 3")
    ap.add_argument("--max-hold", type=int, default=None, help="max bars in a trade")
    ap.add_argument("--cost", type=float, default=0.0002, help="round-trip friction fraction")
    ap.add_argument("--squeeze", type=float, default=None, help="only enter when BB bandwidth percentile <= this (0-100)")
    ap.add_argument("--fill", choices=["close", "next_open"], default="close")
    ap.add_argument("--sweep", action="store_true", help="grid over entry/exit lengths instead of a single run")
    ap.add_argument("--trades-out", default=None)
    ap.add_argument("--by-year", action="store_true")
    a = ap.parse_args()

    df = load(a.csv)
    print(f"{a.csv}: {len(df)} bars, {df.index[0].date()} -> {df.index[-1].date()}, ~{bars_per_year(df):.0f} bars/year")

    if a.sweep:
        print(f"\nSweep (side={a.side}, cost={a.cost}, squeeze={a.squeeze}):")
        print(f"{'entry':>6} {'exit':>5} {'trades':>7} {'win%':>7} {'PF':>6} {'CAGR':>8} {'maxDD':>8} {'Sharpe':>7} {'expo':>6}")
        for ne in (20, 40, 60, 80, 100, 150):
            for nx in (10, 20, 30, 60):
                if nx > ne: continue
                tr, eq, _ = backtest(df, ne, nx, a.side, a.atr_stop, a.max_hold, a.cost, a.squeeze, a.fill, a.exit_mode)
                m = metrics(tr, eq, df)
                if not len(tr): continue
                print(f"{ne:>6} {nx:>5} {m['trades']:>7} {100*m['win_rate']:>6.1f}% {m['profit_factor']:>6.2f} {100*m['cagr']:>+7.2f}% {100*m['max_drawdown']:>+7.2f}% {m['sharpe']:>7.2f} {100*m['exposure']:>5.0f}%")
        m0 = metrics(pd.DataFrame(), pd.Series(np.ones(len(df)), index=df.index), df)
        print(f"\nBuy & hold: CAGR {100*m0['buy_hold_cagr']:+.2f}%, max DD {100*m0['buy_hold_max_dd']:+.2f}%")
        return


    if a.optimize:
        split = pd.Timestamp(a.split, tz="UTC") if a.split else df.index[len(df) // 2]
        ins, oos = df[df.index < split], df[df.index >= split]
        grid = [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 17, 21, 25, 30, 35, 42, 50, 60, 75, 90, 120]
        print(f"\nOptimise exit n (bars without a new extreme). entry={a.entry}, side={a.side}, cost={a.cost}, squeeze={a.squeeze}")
        print(f"In-sample {ins.index[0].date()} -> {ins.index[-1].date()} ({len(ins)} bars) | Out-of-sample {oos.index[0].date()} -> {oos.index[-1].date()} ({len(oos)} bars)")
        print(f"{'n':>4} | {'IS trades':>9} {'IS win%':>7} {'IS PF':>6} {'IS ret':>8} {'IS DD':>8} {'IS Sharpe':>9} | {'OOS trades':>10} {'OOS win%':>8} {'OOS PF':>6} {'OOS ret':>8} {'OOS DD':>8} {'OOS Sharpe':>10}")
        rows = []
        for n in grid:
            cells = []
            for part in (ins, oos):
                tr, eq, _ = backtest(part, a.entry, n, a.side, a.atr_stop, a.max_hold, a.cost, a.squeeze, a.fill, "stale")
                m = metrics(tr, eq, part)
                cells.append(m)
            mi, mo = cells
            rows.append((n, mi, mo))
            f = lambda m: f"{m['trades']:>9} {100*m.get('win_rate', float('nan')):>6.1f}% {m.get('profit_factor', float('nan')):>6.2f} {100*m['total_return']:>+7.2f}% {100*m['max_drawdown']:>+7.2f}% {m['sharpe']:>9.2f}"
            print(f"{n:>4} | {f(mi)} | {f(mo).replace(' ', ' ', 1):>58}")
        best = max((r for r in rows if r[1]["trades"] >= 10), key=lambda r: r[1]["sharpe"], default=None)
        if best:
            n, mi, mo = best
            print(f"\nBest in-sample n by Sharpe (>=10 trades): n={n}  IS Sharpe {mi['sharpe']:.2f} -> OOS Sharpe {mo['sharpe']:.2f}, OOS return {100*mo['total_return']:+.2f}%")
        bh_i, bh_o = metrics(pd.DataFrame(), pd.Series(np.ones(len(ins)), index=ins.index), ins), metrics(pd.DataFrame(), pd.Series(np.ones(len(oos)), index=oos.index), oos)
        print(f"Buy & hold: IS {100*bh_i['buy_hold_total']:+.2f}% (DD {100*bh_i['buy_hold_max_dd']:+.2f}%) | OOS {100*bh_o['buy_hold_total']:+.2f}% (DD {100*bh_o['buy_hold_max_dd']:+.2f}%)")
        return

    tr, eq, _ = backtest(df, a.entry, a.exit, a.side, a.atr_stop, a.max_hold, a.cost, a.squeeze, a.fill, a.exit_mode)
    m = metrics(tr, eq, df)
    print(f"\nDonchian entry {a.entry} / exit {a.exit} ({a.exit_mode}), side={a.side}, atr_stop={a.atr_stop}, max_hold={a.max_hold}, squeeze={a.squeeze}, fill={a.fill}, cost={a.cost}")
    for k, v in m.items():
        print(f"  {k:<16} {fmt(k, v)}")
    if len(tr):
        print("\nExit reasons:", tr["reason"].value_counts().to_dict())
        if a.by_year:
            tr["year"] = pd.to_datetime(tr["exit_time"]).dt.year
            g = tr.groupby("year")["ret"]
            print("\nBy exit year:")
            for y, s in g:
                print(f"  {y}: n={len(s):>3} sum={100*s.sum():+7.2f}% win={100*(s>0).mean():5.1f}%")
        if a.trades_out:
            tr.to_csv(a.trades_out, index=False)
            print(f"\nTrades written to {a.trades_out}")


if __name__ == "__main__":
    main()
