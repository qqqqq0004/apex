"""Slipstream — momentum ETF rotation, run forward in public.

A medium-risk book that rotates through whatever global ETFs are running hot,
and bails the moment the move tires. Two sleeves:

  • Core anchors (~35%): a few slow-growth international / dividend ETFs, held
    throughout — the ballast.
  • Rotation (~65%): the hottest names on a fixed, broad universe of regional
    and sector ETFs, ranked by trailing 1-month + 3-month trend.

Each rotation position lives or dies by four "doors", measured from its entry:
  • −8%  → stop out (the loser door).
  • +13% → it's "armed". After that: slip back below +13% and it's sold (lock
    the gain); push past +21% and it's sold (target); or sit in the +13–21%
    hallway for two weeks without resolving and it's sold (stalled).
When a name dies, the cash rotates straight into the next-hottest ETF.

HONESTY: this is simulated as if started ~1 month ago. Every pick — at the
seed and at every rotation — is ranked using ONLY price data available on that
date. No name is ever chosen with prices that hadn't happened yet. The universe
is a fixed, broad list (winners AND losers), so selection is momentum-driven,
not cherry-picked. NAV is price return; dividends are tracked apart.
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

STATE = DATA_DIR / "slipstream_state.json"
SEED_DATE = "2026-05-19"          # ~1 month ago; snaps to first trading day >=
FETCH_START = "2026-02-01"        # >3 months of lookback before the seed
CAPITAL = 100000.0
BENCH = "SPY"
BENCH_LABEL = "S&P 500"
STRATEGY = "Slipstream · momentum ETF rotation"
RISK = "Medium"

# Rotation door rules (return measured from entry)
STOP = -0.08
ARM = 0.13
TARGET = 0.21
HALL_DAYS = 10                    # ~2 trading weeks in the +13–21% hallway

# Once a name is sold (any sleeve), it can't be re-bought for this many trading
# days (~1 month). Forces the book to give other candidates a turn instead of
# churning the same ticker in and out.
COOLDOWN_DAYS = 21

# Core anchors don't follow the tight doors — they're meant to hold things
# together — but they're not held forever either. A core is replaced (by another
# core-style ETF) only on a BIG move: an outsized fast gain (abnormal for a slow
# anchor — bank it), an outsized gain at any speed, or a deep loss.
CORE_STOP = -0.18
CORE_TP_FAST = 0.18               # +18% reached fast...
CORE_FAST_DAYS = 20               # ...within ~1 month of entry → bank it
CORE_TP_ABS = 0.30               # +30% at any pace → no longer a calm anchor

# Sleeves
CORE = ["VYMI", "IXUS", "IDV", "SCHF"]   # the 4 anchors we open with
CORE_ALLOC = 0.35
ROT_N = 11                        # rotation slots (total book = 4 core + 11 = 15)
TILT_N = 3                        # hottest few get an overweight tilt
TILT_MULT = 1.4

# Fixed, broad universe of liquid regional + sector ETFs. Deliberately mixed —
# strong and weak alike — so the momentum rank does the picking, not hindsight.
CAT = {
    # core-style anchors (slow-growth intl / dividend) — the pool cores rotate within
    "VYMI": "Intl Dividend", "IXUS": "Intl Broad", "IDV": "Intl Dividend", "SCHF": "Developed Intl",
    "VEA": "Developed Intl", "VXUS": "Total Intl", "EFA": "Developed Intl",
    "IEFA": "Developed Intl", "VEU": "Intl ex-US", "VIGI": "Intl Div Growth",
    "SCHY": "Intl Dividend", "ACWX": "World ex-US",
    # single-country
    "EWY": "South Korea", "EWT": "Taiwan", "EWJ": "Japan", "EWG": "Germany",
    "EWQ": "France", "EWP": "Spain", "EWI": "Italy", "EWU": "United Kingdom",
    "EWL": "Switzerland", "EWN": "Netherlands", "EWD": "Sweden", "EPOL": "Poland",
    "EWZ": "Brazil", "EWW": "Mexico", "EWC": "Canada", "EWA": "Australia",
    "INDA": "India", "EIDO": "Indonesia", "THD": "Thailand", "TUR": "Turkey",
    "GREK": "Greece", "EZA": "South Africa", "ARGT": "Argentina", "KSA": "Saudi Arabia",
    # regions
    "EMXC": "EM ex-China", "ILF": "Latin America", "VGK": "Europe",
    "FEZ": "Eurozone", "EPP": "Asia Pacific",
    # sectors
    "SMH": "Semiconductors", "XLE": "Energy", "XOP": "Oil & Gas", "IEZ": "Oil Equipment",
    "XLF": "Financials", "KRE": "Regional Banks", "XLU": "Utilities",
    "XLI": "Industrials", "XLB": "Materials", "XME": "Metals & Mining",
    "GDX": "Gold Miners", "SIL": "Silver Miners", "URA": "Uranium",
    "TAN": "Solar", "ITA": "Defense", "IBB": "Biotech",
}
# core-style ETFs cores may rotate among (anchors, not momentum chasers)
CORE_POOL = ["VYMI", "IXUS", "IDV", "SCHF", "VEA", "VXUS", "EFA",
             "IEFA", "VEU", "VIGI", "SCHY", "ACWX"]
UNIVERSE = [t for t in CAT if t not in CORE_POOL]   # rotation candidates only
ALL = list(CAT)

DISCLAIMER = ("Paper-tracked. Not investment advice. No brokerage connection — "
              "you execute your own trades.")


def _prices():
    df = yf.download(ALL + [BENCH], start=FETCH_START, auto_adjust=False,
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


def _score(px, t, k):
    """Blended 1m + 3m trailing return at row k, using only data <= k."""
    s = px[t]
    if k < 0 or k >= len(s) or pd.isna(s.iloc[k]) or s.iloc[k] <= 0:
        return None
    parts = []
    for lb in (21, 63):
        j = k - lb
        if j >= 0 and pd.notna(s.iloc[j]) and s.iloc[j] > 0:
            parts.append(s.iloc[k] / s.iloc[j] - 1.0)
    return sum(parts) / len(parts) if parts else None


def build():
    px = _prices()
    dates = list(px.index)
    if not dates:
        print("WARN slipstream: empty price fetch; keeping existing state.")
        return None
    # seed = first trading day >= SEED_DATE that has a benchmark print
    k0 = next((k for k, d in enumerate(dates)
               if d >= SEED_DATE and pd.notna(px[BENCH].iloc[k])), None)
    if k0 is None:
        print("WARN slipstream: no benchmark at seed; keeping existing state.")
        return None
    seed = dates[k0]
    bench_seed = float(px[BENCH].iloc[k0])

    # ---- initial book at the seed (ranked on data <= seed only) ----
    ranked = sorted((t for t in UNIVERSE if _score(px, t, k0) is not None),
                    key=lambda t: _score(px, t, k0), reverse=True)
    picks = ranked[:ROT_N]
    cores = [c for c in CORE if pd.notna(px[c].iloc[k0])]

    raw = {t: (TILT_MULT if i < TILT_N else 1.0) for i, t in enumerate(picks)}
    rot_alloc = 1.0 - CORE_ALLOC
    tot_raw = sum(raw.values()) or 1.0
    weights = {t: raw[t] / tot_raw * rot_alloc for t in picks}
    core_each = CORE_ALLOC / len(cores) if cores else 0.0

    holdings = {}
    opened = []
    for t in cores:
        p = float(px[t].iloc[k0])
        holdings[t] = {"shares": CAPITAL * core_each / p, "entry_price": p,
                       "entry_date": seed, "entry_k": k0, "armed": False,
                       "armed_days": 0, "core": True, "cat": CAT[t]}
        opened.append({"ticker": t, "weight": round(core_each, 4), "theme": CAT[t]})
    for t in picks:
        p = float(px[t].iloc[k0])
        holdings[t] = {"shares": CAPITAL * weights[t] / p, "entry_price": p,
                       "entry_date": seed, "entry_k": k0, "armed": False,
                       "armed_days": 0, "core": False, "cat": CAT[t]}
        opened.append({"ticker": t, "weight": round(weights[t], 4), "theme": CAT[t]})

    last_exit = {}   # ticker -> k of its most recent sale (cooldown gate)

    moves = [{"date": seed, "ticker": None, "action": "BUY", "rule": "Seed",
              "rationale": f"Opened {len(holdings)} ETFs — {len(cores)} slow-growth "
              f"core anchors plus the {len(picks)} hottest names on the 1m+3m trend "
              "board. No forward knowledge used.", "judge": "mechanical", "price": None}]
    trade_days = [{"date": seed, "n": len(holdings), "theme": "Seed",
                   "opened": opened, "closed": [], "changed": []}]
    n_moves = 0
    cash = 0.0
    curve = [{"date": seed, "value": round(CAPITAL, 2), "ret": 0.0,
              "benchmark": round(CAPITAL, 2), "benchmark_ret": 0.0, "cash": 0}]

    def pct(x):
        return f"{x*100:+.1f}%"

    # ---- walk forward ----
    for k in range(k0 + 1, len(dates)):
        d = dates[k]
        if pd.isna(px[BENCH].iloc[k]):
            continue
        pv_open = cash + sum(h["shares"] * float(px[t].iloc[k])
                             for t, h in holdings.items() if pd.notna(px[t].iloc[k]))
        day_sells, day_buys = [], []
        core_cash, rot_cash = 0.0, 0.0      # keep sleeves' proceeds separate

        def _ok(t):   # off cooldown + has a usable price/score today
            return (t not in last_exit or k - last_exit[t] >= COOLDOWN_DAYS) \
                and _score(px, t, k) is not None and pd.notna(px[t].iloc[k])

        # 1) process exits — cores on big moves, rotation on the doors
        for t in list(holdings):
            h = holdings[t]
            p = px[t].iloc[k]
            if pd.isna(p):
                continue
            p = float(p)
            r = p / h["entry_price"] - 1.0
            reason = None
            if h["core"]:
                held_days = k - h["entry_k"]
                if r <= CORE_STOP:
                    reason = "Core stop −18%"
                elif r >= CORE_TP_ABS:
                    reason = "Core take-profit"
                elif r >= CORE_TP_FAST and held_days <= CORE_FAST_DAYS:
                    reason = "Core fast gain"
            else:
                if r <= STOP:
                    reason = "Stop −8%"
                elif h["armed"]:
                    if r >= TARGET:
                        reason = "Target +21%"
                    elif r < ARM:
                        reason = "Locked +13%"
                    elif h["armed_days"] >= HALL_DAYS:
                        reason = "Stalled 2wk"
            if reason:
                proceeds = h["shares"] * p
                if h["core"]:
                    core_cash += proceeds
                else:
                    rot_cash += proceeds
                wprev = (proceeds / pv_open) if pv_open else 0
                day_sells.append({"ticker": t, "weight": round(wprev, 4), "theme": h["cat"]})
                msg = {"Stop −8%": f"Cut {t} — broke the −8% stop ({pct(r)}).",
                       "Target +21%": f"Banked {t} — tagged the +21% target ({pct(r)}).",
                       "Locked +13%": f"Locked {t} — slipped back through the +13% door ({pct(r)}).",
                       "Stalled 2wk": f"Dropped {t} — stalled in the +13–21% hallway two weeks ({pct(r)}).",
                       "Core stop −18%": f"Cut core {t} — deep {pct(r)} loss, swapping the anchor.",
                       "Core take-profit": f"Banked core {t} — outsized {pct(r)} gain, no longer a calm anchor.",
                       "Core fast gain": f"Banked core {t} — {pct(r)} in under a month is abnormal for an anchor; rotating it."}[reason]
                moves.append({"date": d, "ticker": t, "action": "SELL", "rule": reason,
                              "rationale": msg, "judge": "mechanical", "price": round(p, 2)})
                n_moves += 1
                last_exit[t] = k
                del holdings[t]
            elif not h["core"]:
                if not h["armed"] and r >= ARM:
                    h["armed"] = True
                    h["armed_days"] = 0
                elif h["armed"]:
                    h["armed_days"] += 1

        # 2) refill core slots from the core pool (steady anchors, ranked by trend)
        open_core = len(CORE) - sum(1 for h in holdings.values() if h["core"])
        if open_core > 0 and core_cash > 1:
            cands = sorted((t for t in CORE_POOL if t not in holdings and _ok(t)),
                           key=lambda t: _score(px, t, k), reverse=True)[:open_core]
            if cands:
                per = core_cash / len(cands)
                for t in cands:
                    p = float(px[t].iloc[k])
                    holdings[t] = {"shares": per / p, "entry_price": p, "entry_date": d,
                                   "entry_k": k, "armed": False, "armed_days": 0,
                                   "core": True, "cat": CAT[t]}
                    day_buys.append({"ticker": t, "weight": round(per / pv_open, 4) if pv_open else 0,
                                     "theme": CAT[t]})
                    moves.append({"date": d, "ticker": t, "action": "BUY", "rule": "New anchor",
                                  "rationale": f"New core anchor {t} ({CAT[t]}) — strongest-trending "
                                  "intl / dividend ETF available to replace it.", "judge": "mechanical", "price": round(p, 2)})
                    n_moves += 1
                    core_cash -= per

        # 3) refill rotation slots with the next-hottest names (ranked at d)
        open_rot = ROT_N - sum(1 for h in holdings.values() if not h["core"])
        if open_rot > 0 and rot_cash > 1:
            cands = sorted((t for t in UNIVERSE if t not in holdings and _ok(t)),
                           key=lambda t: _score(px, t, k), reverse=True)[:open_rot]
            if cands:
                per = rot_cash / len(cands)
                for t in cands:
                    p = float(px[t].iloc[k])
                    holdings[t] = {"shares": per / p, "entry_price": p, "entry_date": d,
                                   "entry_k": k, "armed": False, "armed_days": 0,
                                   "core": False, "cat": CAT[t]}
                    day_buys.append({"ticker": t, "weight": round(per / pv_open, 4) if pv_open else 0,
                                     "theme": CAT[t]})
                    moves.append({"date": d, "ticker": t, "action": "BUY", "rule": "Rotate in",
                                  "rationale": f"Rotated into {t} ({CAT[t]}) — top of the 1m+3m "
                                  "momentum board among names not on cooldown.",
                                  "judge": "mechanical", "price": round(p, 2)})
                    n_moves += 1
                    rot_cash -= per

        cash += core_cash + rot_cash        # tiny rounding remainder only

        if day_sells or day_buys:
            trade_days.append({"date": d, "n": len(day_sells) + len(day_buys),
                               "theme": "Rotation", "opened": day_buys,
                               "closed": day_sells, "changed": []})

        val = cash + sum(h["shares"] * float(px[t].iloc[k])
                         for t, h in holdings.items() if pd.notna(px[t].iloc[k]))
        bp = float(px[BENCH].iloc[k])
        curve.append({"date": d, "value": round(val, 2), "ret": round(val / CAPITAL - 1, 4),
                      "benchmark": round(CAPITAL * bp / bench_seed, 2),
                      "benchmark_ret": round(bp / bench_seed - 1, 4),
                      "cash": round(cash, 2)})

    # ---- final positions (last valid prices) ----
    kL = len(dates) - 1
    while kL > k0 and pd.isna(px[BENCH].iloc[kL]):
        kL -= 1
    positions, theme_val = [], defaultdict(float)
    for t, h in holdings.items():
        p = px[t].iloc[kL]
        p = float(p) if pd.notna(p) else h["entry_price"]
        value = h["shares"] * p
        theme_val[h["cat"]] += value
        positions.append({"ticker": t, "theme": h["cat"],
                          "conviction": "core" if h["core"] else "rotation",
                          "shares": round(h["shares"], 3), "avg_entry": round(h["entry_price"], 2),
                          "price": round(p, 2), "value": round(value, 2),
                          "drawdown": round(max(0, 1 - p / h["entry_price"]), 4),
                          "ret": round(p / h["entry_price"] - 1, 4),
                          "thesis": f"{h['cat']} — {'core anchor' if h['core'] else 'momentum rotation'}."})
    total = cash + sum(p["value"] for p in positions)
    for p in positions:
        p["weight"] = round(p["value"] / total, 4) if total else 0
    positions.sort(key=lambda x: -x["value"])
    themes = [{"theme": k, "weight": round(v / total, 4)} for k, v in
              sorted(theme_val.items(), key=lambda kv: -kv[1])]

    # dividends collected by the CURRENT book since each name's entry (price-return
    # NAV excludes these; shown only in the aside box)
    div_total, div_per = 0.0, []
    for t, h in holdings.items():
        try:
            dt, dp = divs.dividends_since({t: h["shares"]}, h["entry_date"])
        except Exception:
            dt, dp = 0.0, []
        div_total += dt or 0.0
        div_per.extend(dp or [])

    # No intraday for a rotation book: the current holdings didn't exist at the
    # seed, so an intraday curve can't be honestly anchored to inception the way
    # the buy & hold books' can. The daily curve is the record of truth.
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
                 "num_trades": n_moves, "benchmark_label": BENCH_LABEL,
                 "risk": RISK, "disclaimer": DISCLAIMER},
        "curve": curve, "positions": positions, "themes": themes,
        "moves": moves, "trade_days": trade_days, "intraday": intraday,
        "dividends": {"total": round(div_total, 2), "per": div_per},
    }
    jsonio.dump(state, STATE)
    print(f"Built {STATE.name}: {len(curve)} days, {len(positions)} positions, "
          f"{n_moves} rotation moves, return {f['ret']*100:+.1f}% vs "
          f"{BENCH_LABEL} {(f['benchmark_ret'] or 0)*100:+.1f}%.")
    return state


if __name__ == "__main__":
    build()
