"""Ignition — generational-breakout moonshots, run forward in public.

The deliberately high-risk sleeve: a "smart lottery" on small-cap deep-tech that
could be the next NVIDIA or SanDisk if its technology wins — bought BEFORE the
breakout. Picks-and-shovels of the next compute/AI/autonomy wave: silicon
photonics, quantum computing & security, AI-power (nuclear/GaN), novel memory,
compute-in-memory, lidar.

Most of these will go nowhere; a few might 10-50x. So it's diversified across
~16 names in three conviction tiers — bigger weights to the quality anchors with
real revenue and survival odds, smaller "lottery ticket" weights to the
pre-revenue moonshots. One winner pays for many misses.

Management is intentionally light (backtests showed over-managing a basket like
this only adds drag): hold, but CUT a name on thesis-death (>=50% off entry —
a fraud, a failed milestone, a dilution spiral) and SKIM a quarter off a genuine
monster (after a 4x) so a round-trip to zero can't erase a locked win. No
market-crash breaker — at this risk tier you ride it out.

HONESTY: started LIVE today — no backtest, no hindsight. The basket was chosen
on forward thesis (a fact-checked deep-research pass across the verticals), then
seeded at the latest close and walked forward from here. NAV is price return.
Risk: High.
"""
from __future__ import annotations

import sys
from collections import defaultdict

import pandas as pd
import yfinance as yf

from . import jsonio
from .paths import DATA_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATE = DATA_DIR / "ignition_state.json"
SEED_DATE = "2026-06-18"          # latest close before launch (Juneteenth holiday)
FETCH_START = "2026-05-15"
CAPITAL = 100000.0
BENCH = "SPY"
BENCH_LABEL = "S&P 500"
STRATEGY = "Ignition · generational-breakout moonshots"
RISK = "High"

DEEP_STOP = -0.50                 # >=50% off entry → thesis dead, cut it
MOON_AT = 3.0                     # after a +300% (4x) run...
MOON_TRIM = 0.25                  # ...skim a quarter off, lock some house money

# Research-backed basket, tiered by conviction / survival odds.
# (ticker, weight %, tier, vertical)
BASKET = [
    # Tier 1 — anchors: real revenue / moat, better survival, bigger weight
    ("AAOI", 8, "anchor", "Photonics"), ("LEU", 8, "anchor", "Nuclear Fuel"),
    ("MRAM", 8, "anchor", "Novel Memory"), ("SOLS", 8, "anchor", "DC Cooling"),
    ("MTSI", 8, "anchor", "Photonics / RF"),
    # Tier 2 — core moonshots: credible tech, early traction
    ("POET", 6, "moonshot", "Photonics"), ("OKLO", 6, "moonshot", "Nuclear / SMR"),
    ("LAES", 6, "moonshot", "Quantum Security"), ("BTQ", 6, "moonshot", "Quantum Security"),
    ("GSIT", 6, "moonshot", "Compute-in-Memory"), ("NVTS", 6, "moonshot", "Power Semis"),
    ("OUST", 6, "moonshot", "Lidar"),
    # Tier 3 — lottery tickets: pre-revenue / earliest, smallest weight
    ("LWLG", 5, "lottery", "Photonics"), ("XNDU", 5, "lottery", "Quantum Compute"),
    ("RGTI", 4, "lottery", "Quantum Compute"), ("QBTS", 4, "lottery", "Quantum Compute"),
]
CAT = {t: c for t, _, _, c in BASKET}
TIER = {t: tr for t, _, tr, _ in BASKET}
WEIGHT = {t: w for t, w, _, _ in BASKET}
TICKERS = [t for t, _, _, _ in BASKET]

DISCLAIMER = ("Paper-tracked. Not investment advice. No brokerage connection — "
              "you execute your own trades.")


def _daily():
    df = yf.download(TICKERS + [BENCH], start=FETCH_START, auto_adjust=False,
                     progress=False, group_by="ticker")
    out = {}
    for t in TICKERS + [BENCH]:
        try:
            out[t] = df[t]["Close"]
        except Exception:
            pass
    d = pd.DataFrame(out)
    d.index = pd.to_datetime(d.index).strftime("%Y-%m-%d")
    return d.sort_index().ffill()


def build():
    px = _daily()
    dates = list(px.index)
    if not dates or BENCH not in px.columns:
        print("WARN ignition: empty price fetch; keeping existing state.")
        return None
    k0 = next((k for k, d in enumerate(dates)
               if d >= SEED_DATE and pd.notna(px[BENCH].iloc[k])), None)
    if k0 is None:
        print("WARN ignition: no benchmark at seed yet; keeping existing state.")
        return None
    seed = dates[k0]
    bench_seed = float(px[BENCH].iloc[k0])

    held0 = [t for t in TICKERS if pd.notna(px[t].iloc[k0]) and px[t].iloc[k0] > 0]
    holdings = {t: {"shares": CAPITAL * WEIGHT[t] / 100 / float(px[t].iloc[k0]),
                    "entry_price": float(px[t].iloc[k0]), "entry_date": seed,
                    "tier": TIER[t], "cat": CAT[t], "trimmed": False} for t in held0}

    def pct(x):
        return f"{x*100:+.1f}%"

    moves = [{"date": seed, "ticker": None, "action": "BUY", "rule": "Seed",
              "rationale": f"Opened {len(held0)} generational-breakout names across "
              "photonics, quantum, AI-power, memory & lidar — tiered by conviction. "
              "A diversified smart-lottery, live from today.", "judge": "thesis", "price": None}]
    trade_days = [{"date": seed, "n": len(held0), "theme": "Seed", "closed": [], "changed": [],
                   "opened": [{"ticker": t, "weight": round(WEIGHT[t] / 100, 4), "theme": CAT[t]}
                              for t in held0]}]
    n_trades = 0
    cash = 0.0
    curve = [{"date": seed, "value": round(CAPITAL, 2), "ret": 0.0,
              "benchmark": round(CAPITAL, 2), "benchmark_ret": 0.0, "cash": 0}]

    for k in range(k0 + 1, len(dates)):
        d = dates[k]
        if pd.isna(px[BENCH].iloc[k]):
            continue
        price = {t: float(px[t].iloc[k]) for t in holdings if pd.notna(px[t].iloc[k])}
        pv = cash + sum(holdings[t]["shares"] * price.get(t, holdings[t]["entry_price"]) for t in holdings)
        for t in list(holdings):
            if t not in price:
                continue
            h = holdings[t]
            r = price[t] / h["entry_price"] - 1
            if r <= DEEP_STOP:
                cash += h["shares"] * price[t]
                moves.append({"date": d, "ticker": t, "action": "SELL", "rule": "Thesis stop",
                              "rationale": f"Cut {t} — down {pct(r)} from entry, the thesis is "
                              "dead. Lottery tickets that break get torn up.", "judge": "thesis",
                              "price": round(price[t], 2)})
                trade_days.append({"date": d, "n": 1, "theme": "Thesis stop", "opened": [], "changed": [],
                                   "closed": [{"ticker": t, "weight": round(h["shares"]*price[t]/pv, 4) if pv else 0, "theme": h["cat"]}]})
                n_trades += 1
                del holdings[t]
            elif r >= MOON_AT and not h["trimmed"]:
                sell = h["shares"] * MOON_TRIM
                cash += sell * price[t]
                h["shares"] -= sell
                h["trimmed"] = True
                moves.append({"date": d, "ticker": t, "action": "TRIM", "rule": "Moonshot trim",
                              "rationale": f"Skimmed {MOON_TRIM*100:.0f}% off {t} — up {pct(r)}, a "
                              "moonshot hit. Bank house money, let the rest run.", "judge": "thesis",
                              "price": round(price[t], 2)})
                trade_days.append({"date": d, "n": 1, "theme": "Moonshot trim", "opened": [], "closed": [],
                                   "changed": [{"ticker": t, "from": round((h["shares"]+sell)*price[t]/pv, 4) if pv else 0,
                                                "to": round(h["shares"]*price[t]/pv, 4) if pv else 0, "theme": h["cat"]}]})
                n_trades += 1

        val = cash + sum(holdings[t]["shares"] * float(px[t].iloc[k])
                         for t in holdings if pd.notna(px[t].iloc[k]))
        bp = float(px[BENCH].iloc[k])
        curve.append({"date": d, "value": round(val, 2), "ret": round(val / CAPITAL - 1, 4),
                      "benchmark": round(CAPITAL * bp / bench_seed, 2),
                      "benchmark_ret": round(bp / bench_seed - 1, 4), "cash": round(cash, 2)})

    kL = len(dates) - 1
    while kL > k0 and pd.isna(px[BENCH].iloc[kL]):
        kL -= 1
    positions, theme_val = [], defaultdict(float)
    for t, h in holdings.items():
        p = px[t].iloc[kL]
        p = float(p) if pd.notna(p) else h["entry_price"]
        value = h["shares"] * p
        theme_val[h["cat"]] += value
        positions.append({"ticker": t, "theme": h["cat"], "conviction": h["tier"],
                          "shares": round(h["shares"], 3), "avg_entry": round(h["entry_price"], 2),
                          "price": round(p, 2), "value": round(value, 2),
                          "drawdown": round(max(0, 1 - p / h["entry_price"]), 4),
                          "ret": round(p / h["entry_price"] - 1, 4),
                          "thesis": f"{h['cat']} — {h['tier']}."})
    total = cash + sum(p["value"] for p in positions)
    for p in positions:
        p["weight"] = round(p["value"] / total, 4) if total else 0
    positions.sort(key=lambda x: -x["value"])
    themes = [{"theme": k, "weight": round(v / total, 4)} for k, v in
              sorted(theme_val.items(), key=lambda kv: -kv[1])]

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
        "moves": moves, "trade_days": trade_days, "intraday": [],
        "dividends": {"total": 0.0, "per": []},   # moonshots don't pay dividends
    }
    jsonio.dump(state, STATE)
    print(f"Built {STATE.name}: seeded {seed}, {len(positions)} names, "
          f"{len(curve)} days, return {f['ret']*100:+.1f}% vs {BENCH_LABEL} "
          f"{(f['benchmark_ret'] or 0)*100:+.1f}%.")
    return state


if __name__ == "__main__":
    build()
