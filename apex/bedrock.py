"""Bedrock — defensive, recession-resilient, actively (but lightly) managed.

The low-risk sleeve: staples, healthcare, auto-parts, discount retail, waste, a
couple of utilities and gold — everyday businesses that hold up in downturns.
Bought equal-weight on 2026-05-22, then reviewed TWICE A MONTH (the first
trading day on/after the 1st and the 15th). A review doesn't force a trade — it
just checks two conservative, rules-based things:

  • Band rebalance — if a holding has drifted well off its equal-weight target
    (above ~1.2x or below ~0.8x), trim the winner / top up the laggard back to
    target. Harvests the names that ran, recycles into the ones that got cheap,
    and keeps any single name from dominating the risk. Naturally buys defensive
    dips — how it earns its keep in down markets.
  • Health swap — if a holding falls into a deep, sustained drawdown (>=20% off
    its 3-month high), the defensive thesis has cracked, not just wobbled. Swap
    it for the steadiest name on a vetted defensive bench.

No market-panic circuit breaker here — unlike the rotation book, Bedrock should
hold and BUY through broad dips, not flee them. NAV is price return; dividends
tracked apart. Risk: Low.
"""
from __future__ import annotations

import sys
from collections import defaultdict

import pandas as pd
import yfinance as yf

from . import divs, jsonio
from .paths import DATA_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATE = DATA_DIR / "bedrock_state.json"
SEED_DATE = "2026-05-22"
FETCH_START = "2026-02-01"        # 3-month lookback for drawdown / trend
CAPITAL = 100000.0
BENCH = "SPY"
BENCH_LABEL = "S&P 500"
STRATEGY = "Bedrock · defensive, actively managed"
RISK = "Low"

REBAL_HI = 1.20                   # trim a name above 1.2x its equal-weight target
REBAL_LO = 0.80                   # top up a name below 0.8x its target
DD_STOP = 0.20                    # >=20% off the 3-month high → thesis cracked, swap
LOOKBACK = 63                     # ~3 trading months

# Defensive book held from the seed (equal weight).
HELD = {
    "WMT": "Staples", "COST": "Staples", "PG": "Staples", "KO": "Staples",
    "PEP": "Staples", "CL": "Staples", "MDLZ": "Staples",
    "JNJ": "Healthcare", "UNH": "Healthcare", "ABBV": "Healthcare",
    "MRK": "Healthcare", "LLY": "Healthcare", "AMGN": "Healthcare",
    "AZO": "Auto Parts", "ORLY": "Auto Parts",
    "ROST": "Discount Retail", "TJX": "Discount Retail",
    "WM": "Waste", "RSG": "Waste",
    "NEE": "Utilities", "DUK": "Utilities",
    "GLD": "Gold",
}
# Vetted defensive bench — swap candidates when a holding's thesis breaks.
BENCHN = {
    "KMB": "Staples", "GIS": "Staples", "HSY": "Staples", "SYY": "Staples",
    "CHD": "Staples", "CLX": "Staples", "KHC": "Staples", "HRL": "Staples",
    "MKC": "Staples", "ADM": "Staples",
    "MDT": "Healthcare", "BMY": "Healthcare", "GILD": "Healthcare",
    "CI": "Healthcare", "CVS": "Healthcare", "ZTS": "Healthcare",
    "DG": "Discount Retail", "KR": "Discount Retail", "DLTR": "Discount Retail",
    "SO": "Utilities", "AEP": "Utilities", "D": "Utilities", "ED": "Utilities",
    "XEL": "Utilities", "WCN": "Waste", "SLV": "Silver",
}
CAT = {**HELD, **BENCHN}
ALL = list(CAT)

DISCLAIMER = ("Paper-tracked. Not investment advice. No brokerage connection — "
              "you execute your own trades.")


def _daily(end=None):
    df = yf.download(ALL + [BENCH], start=FETCH_START, end=end, auto_adjust=False,
                     progress=False, group_by="ticker")
    out = {}
    for t in ALL + [BENCH]:
        try:
            out[t] = df[t]["Close"]
        except Exception:
            pass
    d = pd.DataFrame(out)
    d.index = pd.to_datetime(d.index).strftime("%Y-%m-%d")
    return d.sort_index().ffill()


def _dd(px, t, k):
    """Drawdown of t from its trailing 3-month high at row k."""
    hi = px[t].iloc[max(0, k - LOOKBACK):k + 1].max()
    p = px[t].iloc[k]
    return float(1 - p / hi) if pd.notna(p) and hi else 0.0


def _trend(px, t, k):
    j = max(0, k - LOOKBACK)
    a, b = px[t].iloc[j], px[t].iloc[k]
    return float(b / a - 1) if pd.notna(a) and pd.notna(b) and a > 0 else None


def _period(d):
    """Half-month bucket: (year, month, 1) for days 1-14, (..,2) for 15+."""
    return (int(d[:4]), int(d[5:7]), 1 if int(d[8:10]) < 15 else 2)


def build():
    px = _daily()
    dates = list(px.index)
    if not dates or BENCH not in px.columns:
        print("WARN bedrock: empty price fetch; keeping existing state.")
        return None
    k0 = next((k for k, d in enumerate(dates)
               if d >= SEED_DATE and pd.notna(px[BENCH].iloc[k])), None)
    if k0 is None:
        print("WARN bedrock: no benchmark at seed; keeping existing state.")
        return None
    seed = dates[k0]
    bench_seed = float(px[BENCH].iloc[k0])

    held0 = [t for t in HELD if pd.notna(px[t].iloc[k0])]
    N = len(held0)
    per = CAPITAL / N
    holdings = {t: {"shares": per / float(px[t].iloc[k0]), "entry_price": float(px[t].iloc[k0]),
                    "entry_date": seed, "cat": CAT[t]} for t in held0}

    def pct(x):
        return f"{x*100:+.1f}%"

    moves = [{"date": seed, "ticker": None, "action": "BUY", "rule": "Seed",
              "rationale": f"Opened {N} defensive, recession-resilient names, equal "
              "weight. Reviewed twice a month from here.", "judge": "mechanical", "price": None}]
    trade_days = [{"date": seed, "n": N, "theme": "Seed", "closed": [], "changed": [],
                   "opened": [{"ticker": t, "weight": round(1 / N, 4), "theme": CAT[t]}
                              for t in held0]}]
    n_trades = 0
    cash = 0.0
    curve = [{"date": seed, "value": round(CAPITAL, 2), "ret": 0.0,
              "benchmark": round(CAPITAL, 2), "benchmark_ret": 0.0, "cash": 0}]
    last_period = _period(seed)

    for k in range(k0 + 1, len(dates)):
        d = dates[k]
        if pd.isna(px[BENCH].iloc[k]):
            continue
        price = {t: float(px[t].iloc[k]) for t in holdings if pd.notna(px[t].iloc[k])}

        per_review = _period(d) != last_period
        if per_review:
            last_period = _period(d)
            pv0 = cash + sum(holdings[t]["shares"] * price.get(t, holdings[t]["entry_price"])
                             for t in holdings)
            closed, opened, changed = [], [], []

            # 1) health swaps — a holding deep off its 3-month high is broken
            for t in list(holdings):
                if t not in price or _dd(px, t, k) < DD_STOP:
                    continue
                cand = [b for b in BENCHN if b not in holdings and pd.notna(px[b].iloc[k])
                        and _dd(px, b, k) < DD_STOP and _trend(px, b, k) is not None]
                if not cand:
                    continue
                repl = max(cand, key=lambda b: _trend(px, b, k))
                val = holdings[t]["shares"] * price[t]
                rp = float(px[repl].iloc[k])
                closed.append({"ticker": t, "weight": round(val / pv0, 4) if pv0 else 0, "theme": CAT[t]})
                opened.append({"ticker": repl, "weight": round(val / pv0, 4) if pv0 else 0, "theme": CAT[repl]})
                moves.append({"date": d, "ticker": t, "action": "SELL", "rule": "Health swap",
                              "rationale": f"Swapped {t} — {pct(-_dd(px, t, k))} off its 3-month high, "
                              f"defensive thesis cracked. Into {repl}.", "judge": "mechanical", "price": round(price[t], 2)})
                moves.append({"date": d, "ticker": repl, "action": "BUY", "rule": "Health swap",
                              "rationale": f"Replaced {t} with {repl} ({CAT[repl]}) — steadiest healthy "
                              "name on the defensive bench.", "judge": "mechanical", "price": round(rp, 2)})
                holdings[repl] = {"shares": val / rp, "entry_price": rp, "entry_date": d, "cat": CAT[repl]}
                del holdings[t]
                price[repl] = rp
                n_trades += 1

            # 2) band rebalance — pull big drifters back toward equal weight
            total = cash + sum(holdings[t]["shares"] * price.get(t, holdings[t]["entry_price"])
                               for t in holdings)
            tgt = total / len(holdings)
            for t in list(holdings):
                if t not in price:
                    continue
                val = holdings[t]["shares"] * price[t]
                if val > REBAL_HI * tgt:
                    changed.append({"ticker": t, "from": round(val / total, 4),
                                    "to": round(tgt / total, 4), "theme": CAT[t]})
                    moves.append({"date": d, "ticker": t, "action": "TRIM", "rule": "Rebalance",
                                  "rationale": f"Trimmed {t} back to target — it had run to "
                                  f"{val/total*100:.1f}% of the book.", "judge": "mechanical", "price": round(price[t], 2)})
                    holdings[t]["shares"] = tgt / price[t]
                    cash += val - tgt
                    n_trades += 1
            for t in list(holdings):
                if t not in price:
                    continue
                val = holdings[t]["shares"] * price[t]
                if val < REBAL_LO * tgt and cash > 1:
                    buy = min(tgt - val, cash)
                    changed.append({"ticker": t, "from": round(val / total, 4),
                                    "to": round((val + buy) / total, 4), "theme": CAT[t]})
                    moves.append({"date": d, "ticker": t, "action": "ADD", "rule": "Rebalance",
                                  "rationale": f"Topped up {t} — it had slipped to "
                                  f"{val/total*100:.1f}% of the book.", "judge": "mechanical", "price": round(price[t], 2)})
                    holdings[t]["shares"] += buy / price[t]
                    cash -= buy
                    n_trades += 1

            if closed or opened or changed:
                trade_days.append({"date": d, "n": len(closed) + len(changed),
                                   "theme": "Review", "closed": closed, "opened": opened,
                                   "changed": changed})

        val = cash + sum(holdings[t]["shares"] * price.get(t, holdings[t]["entry_price"])
                         for t in holdings)
        bp = float(px[BENCH].iloc[k])
        curve.append({"date": d, "value": round(val, 2), "ret": round(val / CAPITAL - 1, 4),
                      "benchmark": round(CAPITAL * bp / bench_seed, 2),
                      "benchmark_ret": round(bp / bench_seed - 1, 4), "cash": round(cash, 2)})

    # ---- final positions ----
    kL = len(dates) - 1
    while kL > k0 and pd.isna(px[BENCH].iloc[kL]):
        kL -= 1
    positions, theme_val = [], defaultdict(float)
    for t, h in holdings.items():
        p = px[t].iloc[kL]
        p = float(p) if pd.notna(p) else h["entry_price"]
        value = h["shares"] * p
        theme_val[h["cat"]] += value
        positions.append({"ticker": t, "theme": h["cat"], "conviction": "core",
                          "shares": round(h["shares"], 3), "avg_entry": round(h["entry_price"], 2),
                          "price": round(p, 2), "value": round(value, 2),
                          "drawdown": round(max(0, 1 - p / h["entry_price"]), 4),
                          "ret": round(p / h["entry_price"] - 1, 4),
                          "thesis": f"Defensive holding ({h['cat']})."})
    total = cash + sum(p["value"] for p in positions)
    for p in positions:
        p["weight"] = round(p["value"] / total, 4) if total else 0
    positions.sort(key=lambda x: -x["value"])
    themes = [{"theme": k, "weight": round(v / total, 4)} for k, v in
              sorted(theme_val.items(), key=lambda kv: -kv[1])]

    div_total, div_per = 0.0, []
    for t, h in holdings.items():
        try:
            dt, dp = divs.dividends_since({t: h["shares"]}, h["entry_date"])
        except Exception:
            dt, dp = 0.0, []
        div_total += dt or 0.0
        div_per.extend(dp or [])

    # No intraday: once the book can swap holdings, an intraday curve can't be
    # honestly anchored back to inception (the current names weren't all held
    # since the seed). The daily curve is the record of truth.
    intraday = []
    moves = list(reversed(moves))
    trade_days = list(reversed(trade_days))
    f = curve[-1]
    state = {
        "meta": {"initial_deposit": CAPITAL, "start_date": seed, "last_date": dates[kL],
                 "mode": "forward", "strategy": STRATEGY,
                 "total_value": round(total, 2), "cash": round(cash, 2),
                 "total_return": f["ret"], "benchmark_return": f["benchmark_ret"],
                 "alpha": round((f["ret"] or 0) - (f["benchmark_ret"] or 0), 4),
                 "num_trades": n_trades, "benchmark_label": BENCH_LABEL,
                 "risk": RISK, "disclaimer": DISCLAIMER},
        "curve": curve, "positions": positions, "themes": themes,
        "moves": moves, "trade_days": trade_days, "intraday": intraday,
        "dividends": {"total": round(div_total, 2), "per": div_per},
    }
    jsonio.dump(state, STATE)
    print(f"Built {STATE.name}: {len(curve)} days, {len(positions)} positions, "
          f"{n_trades} active trades, return {f['ret']*100:+.1f}% vs "
          f"{BENCH_LABEL} {(f['benchmark_ret'] or 0)*100:+.1f}%.")
    return state


if __name__ == "__main__":
    build()
