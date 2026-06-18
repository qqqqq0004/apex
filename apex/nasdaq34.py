"""Top-34 Nasdaq-100 — equal-weight buy & hold.

A second, deliberately simple portfolio: buy the 34 largest Nasdaq-100 names in
equal weight on 2026-05-22 and hold. Stateless — the whole track record is
recomputed from fixed share counts + price history each run, then written to
data/nasdaq34_state.json in the same format the dashboard reads.

Benchmark: QQQ (the Nasdaq-100 itself) — the honest "did picking 34 of the 100
beat just holding all of them" test.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

import pandas as pd
import yfinance as yf

from .paths import DATA_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATE = DATA_DIR / "nasdaq34_state.json"
SEED_DATE = "2026-05-22"
CAPITAL = 100000.0
BENCH = "QQQ"
BENCH_LABEL = "Nasdaq-100"
STRATEGY = "Top 34 Nasdaq-100 · equal-weight buy & hold"
REBAL_AMOUNT = 150.0   # tiny monthly trim->add: registers as an Autopilot move,
                       # negligible return drag (drift-sized, not a full rebalance)

TICKERS = ["NVDA", "GOOGL", "GOOG", "AAPL", "MSFT", "AMZN", "AVGO", "TSLA", "META",
           "WMT", "MU", "AMD", "ASML", "INTC", "CSCO", "COST", "LRCX", "NFLX",
           "AMAT", "PLTR", "ARM", "TXN", "QCOM", "LIN", "SNDK", "PANW", "TMUS",
           "PEP", "ADI", "STX", "AMGN", "MRVL", "WDC", "KLAC"]

SECTOR = {
    "NVDA": "Semiconductors", "AVGO": "Semiconductors", "MU": "Semiconductors",
    "AMD": "Semiconductors", "ASML": "Semiconductors", "INTC": "Semiconductors",
    "AMAT": "Semiconductors", "LRCX": "Semiconductors", "TXN": "Semiconductors",
    "QCOM": "Semiconductors", "KLAC": "Semiconductors", "MRVL": "Semiconductors",
    "ADI": "Semiconductors", "ARM": "Semiconductors", "STX": "Semiconductors",
    "WDC": "Semiconductors", "SNDK": "Semiconductors",
    "MSFT": "Software", "PANW": "Software", "PLTR": "Software",
    "GOOGL": "Communications", "GOOG": "Communications", "META": "Communications",
    "NFLX": "Communications", "TMUS": "Communications",
    "AMZN": "Consumer", "TSLA": "Consumer", "COST": "Consumer", "WMT": "Consumer",
    "PEP": "Consumer",
    "AAPL": "Hardware", "CSCO": "Hardware",
    "AMGN": "Healthcare", "LIN": "Materials",
}

DISCLAIMER = ("Paper-tracked. Not investment advice. No brokerage connection — "
              "you execute your own trades.")


def _closeon(s, date):
    s = s.dropna().loc[:date]
    return float(s.iloc[-1]) if len(s) else None


def _daily(end=None):
    df = yf.download(TICKERS + [BENCH], start="2026-05-18", end=end,
                     auto_adjust=False, progress=False, group_by="ticker")
    out = {}
    for t in TICKERS + [BENCH]:
        try:
            out[t] = df[t]["Close"]
        except Exception:
            pass
    d = pd.DataFrame(out)
    d.index = pd.to_datetime(d.index).strftime("%Y-%m-%d")
    return d.sort_index()


def _intraday(shares, end=None):
    start = (pd.Timestamp.now("UTC") - pd.Timedelta(days=40)).strftime("%Y-%m-%d")
    start = max(start, SEED_DATE)
    try:
        raw = yf.download(TICKERS + [BENCH], start=start, interval="30m",
                          auto_adjust=False, progress=False, group_by="ticker",
                          prepost=True)
    except Exception:
        return []
    cols = {}
    for t in TICKERS + [BENCH]:
        try:
            cols[t] = raw[t]["Close"]
        except Exception:
            pass
    if BENCH not in cols:
        return []
    df = pd.DataFrame(cols).ffill().dropna(subset=[BENCH])
    if df.empty:
        return []
    b0 = float(df[BENCH].iloc[0])
    out = []
    for ts, row in df.iterrows():
        d = ts.strftime("%Y-%m-%d")
        if d < SEED_DATE:
            continue
        val = sum(shares[t] * row[t] for t in TICKERS
                  if t in row and pd.notna(row[t]))
        bp = row[BENCH]
        out.append({"t": ts.strftime("%Y-%m-%d %H:%M"), "value": round(val, 2),
                    "benchmark": round(CAPITAL * bp / b0, 2) if pd.notna(bp) else None})
    # anchor inception at the seed regular close (one point), then forward
    seed_pts = [p for p in out if p["t"][:10] == SEED_DATE]
    if seed_pts:
        reg = [p for p in seed_pts if p["t"][11:] <= "16:00"]
        anchor = reg[-1] if reg else seed_pts[-1]
        out = [anchor] + [p for p in out if p["t"][:10] != SEED_DATE]
    return out


def build():
    daily = _daily()
    seed_px = {t: _closeon(daily[t], SEED_DATE) for t in TICKERS}
    missing = [t for t in TICKERS if not seed_px.get(t)]
    if missing:
        print("WARN missing seed prices:", missing)
    per = CAPITAL / len(TICKERS)
    shares = {t: per / seed_px[t] for t in TICKERS if seed_px.get(t)}
    bench_seed = _closeon(daily[BENCH], SEED_DATE)

    dates = [d for d in daily.index if d >= SEED_DATE]
    held = list(shares)
    moves = [{"date": SEED_DATE, "ticker": None, "action": "BUY", "rule": "Seed",
              "rationale": f"Bought the top {len(held)} Nasdaq-100 names, equal "
                           "weight. Buy & hold.", "judge": "mechanical", "price": None}]
    trade_days = [{"date": SEED_DATE, "n": len(held), "theme": "Nasdaq-100",
                   "closed": [], "changed": [],
                   "opened": [{"ticker": t, "weight": round(1 / len(held), 4),
                               "theme": SECTOR[t]} for t in held]}]

    curve = []
    prev_month = SEED_DATE[:7]
    n_rebal = 0
    for d in dates:
        if d[:7] != prev_month:               # first trading day of a new month
            prev_month = d[:7]
            vals = {t: shares[t] * float(daily[t].get(d)) for t in held
                    if pd.notna(daily[t].get(d))}
            if vals:
                tot = sum(vals.values())
                over = max(vals, key=vals.get)
                under = min(vals, key=vals.get)
                if over != under:
                    po, pu = float(daily[over].get(d)), float(daily[under].get(d))
                    amt = min(REBAL_AMOUNT, vals[over] * 0.9)
                    fo, fu = vals[over] / tot, vals[under] / tot
                    shares[over] -= amt / po
                    shares[under] += amt / pu
                    to_, tu = shares[over] * po / tot, shares[under] * pu / tot
                    n_rebal += 1
                    trade_days.append({"date": d, "n": 2, "theme": "Rebalance",
                        "opened": [], "closed": [], "changed": [
                          {"ticker": over, "from": round(fo, 4), "to": round(to_, 4), "theme": SECTOR[over]},
                          {"ticker": under, "from": round(fu, 4), "to": round(tu, 4), "theme": SECTOR[under]}]})
                    moves.append({"date": d, "ticker": over, "action": "TRIM", "rule": "Rebalance",
                        "rationale": f"Monthly rebalance — trimmed ${amt:.0f} of {over} (top weight) into {under}.",
                        "judge": "mechanical", "price": round(po, 2)})
                    moves.append({"date": d, "ticker": under, "action": "ADD", "rule": "Rebalance",
                        "rationale": f"Monthly rebalance — added ${amt:.0f} to {under} (lowest weight) from {over}.",
                        "judge": "mechanical", "price": round(pu, 2)})
        val = sum(shares[t] * float(daily[t].get(d)) for t in held
                  if pd.notna(daily[t].get(d)))
        bp = daily[BENCH].get(d)
        bval = CAPITAL * bp / bench_seed if pd.notna(bp) and bench_seed else None
        curve.append({"date": d, "value": round(val, 2),
                      "ret": round(val / CAPITAL - 1, 4),
                      "benchmark": round(bval, 2) if bval else None,
                      "benchmark_ret": round(bp / bench_seed - 1, 4) if (bp and bench_seed) else None,
                      "cash": 0})

    last = dates[-1]
    last_px = {t: float(daily[t].get(last)) for t in held if pd.notna(daily[t].get(last))}
    total = sum(shares[t] * last_px[t] for t in last_px)
    positions, theme_val = [], defaultdict(float)
    for t in held:
        price = last_px.get(t, seed_px[t])
        value = shares[t] * price
        theme_val[SECTOR[t]] += value
        positions.append({"ticker": t, "theme": SECTOR[t], "conviction": "core",
                          "shares": round(shares[t], 3), "avg_entry": round(seed_px[t], 2),
                          "price": round(price, 2), "value": round(value, 2),
                          "drawdown": round(max(0, 1 - price / seed_px[t]), 4),
                          "ret": round(price / seed_px[t] - 1, 4),
                          "thesis": f"Top-34 Nasdaq-100 ({SECTOR[t]})."})
    for p in positions:
        p["weight"] = round(p["value"] / total, 4) if total else 0
    positions.sort(key=lambda x: -x["value"])
    themes = [{"theme": k, "weight": round(v / total, 4)} for k, v in
              sorted(theme_val.items(), key=lambda kv: -kv[1])]

    moves = list(reversed(moves))
    trade_days = list(reversed(trade_days))

    intraday = _intraday(shares)

    f = curve[-1]
    state = {
        "meta": {"initial_deposit": CAPITAL, "start_date": SEED_DATE,
                 "last_date": last, "mode": "forward", "strategy": STRATEGY,
                 "total_value": round(total, 2), "cash": 0,
                 "total_return": f["ret"], "benchmark_return": f["benchmark_ret"],
                 "alpha": round((f["ret"] or 0) - (f["benchmark_ret"] or 0), 4),
                 "num_trades": n_rebal, "benchmark_label": BENCH_LABEL,
                 "disclaimer": DISCLAIMER},
        "curve": curve, "positions": positions, "themes": themes,
        "moves": moves, "trade_days": trade_days, "intraday": intraday,
    }
    STATE.write_text(json.dumps(state, indent=2))
    print(f"Built {STATE.name}: {len(curve)} days, {len(positions)} positions, "
          f"return {f['ret']*100:+.1f}% vs {BENCH_LABEL} {(f['benchmark_ret'] or 0)*100:+.1f}%.")
    return state


if __name__ == "__main__":
    build()
