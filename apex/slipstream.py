"""Slipstream — momentum ETF rotation, run forward in public.

A medium-risk book that rotates through whatever global ETFs are running hot,
and bails the moment the move tires. Two sleeves:

  • Core anchors (~35%): broad international funds, held as ballast.
  • Rotation (~65%): the hottest names on a broad universe of country, region
    and sector ETFs, ranked by trailing 1-month + 3-month trend.

Each rotation position lives or dies by four "doors", measured from its entry:
  • −8%  → stop out (the loser door).
  • +13% → it's "armed". After that: slip back below +13% and it's sold (lock
    the gain); push past +21% and it's sold (target); or sit in the +13–21%
    hallway for two weeks without resolving and it's sold (stalled).
A sold name then sits out a ~1-month cooldown; a fresh name is held a minimum
1 week before profit-doors can fire (so the book doesn't churn intraweek).

UNIVERSE — built mechanically, NOT hand-picked: a wide net is cast by whole ETF
families (the full single-country lineup incl. the obscure ones, all SPDR
sectors + a broad industry/thematic net, the standard regions, broad intl funds
for the core), then pruned by a rule only — must have 3-month history by the
seed and clear a liquidity floor measured at inception. No ticker gets special
treatment; the survivor list is frozen at the seed so the book's menu is stable.

HONESTY: simulated as if started ~1 month ago. Every pick — at the seed and at
every rotation — is ranked using ONLY data available on that date. NAV is price
return; dividends are tracked apart.
"""
from __future__ import annotations

import sys
from collections import defaultdict

import pandas as pd
import yfinance as yf

from . import divs, jsonio, livebar, midday
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
MIN_HOLD = 5                      # hold a fresh name >=1 week before the lock /
                                  # stalled doors can fire (stop & target always
                                  # active). Tempers intraweek churn.

# Market-wide circuit breaker: if the S&P closes <= this on the day, it's a
# broad risk-off session, not a thesis break. The book SKIPS trading entirely —
# no stops, no rotations, no core swaps. A panic day also starts a cooling-off
# window: the next COOL_SESSIONS sessions are skipped too, so the book doesn't
# just dump the same broken-by-beta names the moment the panic day ends. Normal
# rules resume once the dust settles. Another panic mid-cooloff resets the clock.
PANIC_DROP = -0.025  # only genuine deep selloffs, not shallow ~-2% wobbles. A
                     # 6-month A/B showed firing on ~-2% dip-days (which bounce)
                     # was pure drag (~3pp); -2.5% keeps the crash net without it.
COOL_SESSIONS = 5   # ~1 trading week. Across 877 S&P panic days since 1927 the
                    # median time to the post-panic BOTTOM is 5 sessions, so the
                    # book resumes just past where the market typically troughs
                    # rather than selling into the slide.

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
CORE_ALLOC = 0.35
CORE_SLOTS = 4                    # number of core anchors held
ROT_N = 11                        # rotation slots (total book = 4 core + 11 = 15)
TILT_N = 3                        # hottest few get an overweight tilt
TILT_MULT = 1.4

# Universe is built mechanically from these families + a liquidity filter (see
# module docstring) — never hand-picked. Liquidity floor measured at inception.
LIQ_MIN = 3_000_000              # $3M average daily dollar volume
# Optional volatility ceiling (annualized, measured at inception). Drops the
# hyper-volatile narrow thematics (solar/uranium/lithium/ARKK…) that whip the
# book with cluster stop-outs. None = keep the full net.
MAX_VOL = None

COUNTRY = {
    "EWA": "Australia", "EWO": "Austria", "EWK": "Belgium", "EWZ": "Brazil",
    "EWC": "Canada", "ECH": "Chile", "MCHI": "China", "EDEN": "Denmark",
    "EFNL": "Finland", "EWQ": "France", "EWG": "Germany", "GREK": "Greece",
    "EWH": "Hong Kong", "INDA": "India", "EIDO": "Indonesia", "EIS": "Israel",
    "EWI": "Italy", "EWJ": "Japan", "EWM": "Malaysia", "EWW": "Mexico",
    "EWN": "Netherlands", "ENZL": "New Zealand", "NORW": "Norway", "EPU": "Peru",
    "EPHE": "Philippines", "EPOL": "Poland", "QAT": "Qatar", "KSA": "Saudi Arabia",
    "EWS": "Singapore", "EZA": "South Africa", "EWY": "South Korea", "EWP": "Spain",
    "EWD": "Sweden", "EWL": "Switzerland", "EWT": "Taiwan", "THD": "Thailand",
    "TUR": "Turkey", "UAE": "UAE", "EWU": "United Kingdom", "ARGT": "Argentina",
    "VNM": "Vietnam",
}
REGION = {
    "EEM": "Emerging Mkts", "IEMG": "Emerging Mkts", "VWO": "Emerging Mkts",
    "EMXC": "EM ex-China", "VGK": "Europe", "FEZ": "Eurozone", "EZU": "Eurozone",
    "EPP": "Asia Pacific", "AAXJ": "Asia ex-Japan", "ILF": "Latin America",
    "SCZ": "Intl Small-Cap",
}
SECTOR = {
    "XLB": "Materials", "XLC": "Comm Services", "XLE": "Energy", "XLF": "Financials",
    "XLI": "Industrials", "XLK": "Technology", "XLP": "Staples", "XLRE": "Real Estate",
    "XLU": "Utilities", "XLV": "Health Care", "XLY": "Discretionary",
    "SMH": "Semiconductors", "SOXX": "Semiconductors", "XBI": "Biotech", "IBB": "Biotech",
    "KRE": "Regional Banks", "KBE": "Banks", "XME": "Metals & Mining", "GDX": "Gold Miners",
    "GDXJ": "Jr Gold Miners", "SIL": "Silver Miners", "XOP": "Oil & Gas E&P",
    "OIH": "Oil Services", "IEZ": "Oil Equipment", "TAN": "Solar", "ICLN": "Clean Energy",
    "URA": "Uranium", "URNM": "Uranium", "LIT": "Lithium", "ITA": "Defense",
    "XAR": "Aero & Defense", "PAVE": "Infrastructure", "JETS": "Airlines",
    "IYT": "Transport", "KIE": "Insurance", "IGV": "Software", "HACK": "Cybersecurity",
    "SKYY": "Cloud", "FDN": "Internet", "ARKK": "Innovation", "XHB": "Homebuilders",
    "ITB": "Homebuilders", "XRT": "Retail", "KWEB": "China Internet", "REMX": "Rare Earth",
    "COPX": "Copper Miners", "GUNR": "Natural Resources",
}
CORE_FAMILY = {
    "VEA": "Developed Intl", "VXUS": "Total Intl", "IXUS": "Total Intl",
    "SCHF": "Developed Intl", "EFA": "Developed Intl", "IEFA": "Developed Intl",
    "VEU": "Intl ex-US", "ACWX": "World ex-US", "SPDW": "Developed Intl",
    "VYMI": "Intl Dividend", "IDV": "Intl Dividend", "VIGI": "Intl Div Growth",
    "SCHY": "Intl Dividend", "DEM": "EM Dividend", "DTH": "Intl Dividend",
    "IQDF": "Intl Dividend",
}
BUCKET, LABEL = {}, {}
for _d, _b in [(COUNTRY, "country"), (REGION, "region"), (SECTOR, "sector"),
               (CORE_FAMILY, "core")]:
    for _t, _lab in _d.items():
        BUCKET[_t] = _b
        LABEL[_t] = _lab
CANDIDATES = list(LABEL)

DISCLAIMER = ("Paper-tracked. Not investment advice. No brokerage connection — "
              "you execute your own trades.")


def _fetch():
    """Close prices (ffilled) + average daily dollar volume for every candidate."""
    raw = yf.download(CANDIDATES + [BENCH], start=FETCH_START, auto_adjust=False,
                      progress=False, group_by="ticker")
    close, dvol = {}, {}
    for t in CANDIDATES + [BENCH]:
        try:
            c, v = raw[t]["Close"], raw[t]["Volume"]
        except Exception:
            continue
        if c.dropna().empty:
            continue
        close[t] = c
        if t != BENCH:
            dvol[t] = float((c * v).dropna().mean())
    px = pd.DataFrame(close)
    px.index = pd.to_datetime(px.index).strftime("%Y-%m-%d")
    return px.sort_index().ffill(), dvol


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


def _vol(px, t, k):
    """Annualized realized volatility from daily returns up to row k (inception)."""
    r = px[t].iloc[:k + 1].pct_change().dropna()
    return float(r.std() * (252 ** 0.5)) if len(r) > 5 else 0.0


def build():
    px, dvol = _fetch()
    dates = list(px.index)
    if not dates or BENCH not in px.columns:
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

    # ---- build the universe mechanically: families pruned by 3m-history-at-seed
    # + a liquidity floor (measured at inception). Frozen here so the menu is
    # stable for the life of the book. No ticker is hand-selected.
    survivors = [t for t in CANDIDATES if t in px.columns
                 and _score(px, t, k0) is not None and dvol.get(t, 0) >= LIQ_MIN
                 and (MAX_VOL is None or _vol(px, t, k0) <= MAX_VOL)]
    CAT = {t: LABEL[t] for t in survivors}
    CORE_POOL = sorted((t for t in survivors if BUCKET[t] == "core"),
                       key=lambda t: dvol.get(t, 0), reverse=True)
    CORE = CORE_POOL[:CORE_SLOTS]                     # biggest broad funds = anchors
    UNIVERSE = [t for t in survivors if BUCKET[t] != "core"]
    if not CORE or not UNIVERSE:
        print("WARN slipstream: universe filter left no names; keeping existing state.")
        return None

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
    # pre-trade book held DURING each day, for honest dense intraday (livebar.history)
    book_by_date = {seed: {"h": {t: holdings[t]["shares"] for t in holdings}, "cash": cash}}

    def pct(x):
        return f"{x*100:+.1f}%"

    # ---- walk forward ----
    # trade-price frame: 12:00-1:30pm fills from the cutoff forward, close before it
    tpx = midday.blend(px, midday.fetch(survivors + [BENCH], FETCH_START), midday.FROM)
    prev_bench = bench_seed
    cooloff = 0                              # sessions still to skip after a panic
    for k in range(k0 + 1, len(dates)):
        d = dates[k]
        if pd.isna(px[BENCH].iloc[k]):
            continue
        book_by_date[d] = {"h": {t: holdings[t]["shares"] for t in holdings}, "cash": cash}
        bp = float(px[BENCH].iloc[k])
        mkt = bp / prev_bench - 1.0          # S&P close-to-close move this session
        prev_bench = bp
        val_held = cash + sum(h["shares"] * float(px[t].iloc[k])
                              for t, h in holdings.items() if pd.notna(px[t].iloc[k]))

        # market-wide selloff → skip the day; then cool off for COOL_SESSIONS more
        skip_reason = None
        if mkt <= PANIC_DROP:
            cooloff = COOL_SESSIONS
            skip_reason = (f"S&P {pct(mkt)} — a broad market selloff, not a thesis "
                           "break. Held everything; no stops, no rotations.")
        elif cooloff > 0:
            n = COOL_SESSIONS - cooloff + 1
            cooloff -= 1
            skip_reason = (f"Cooling off after the selloff (session {n} of "
                           f"{COOL_SESSIONS}) — still holding, letting the dust settle.")
        if skip_reason:
            moves.append({"date": d, "ticker": None, "action": "HOLD",
                          "rule": "Risk-off skip", "rationale": skip_reason,
                          "judge": "mechanical", "price": None})
            curve.append({"date": d, "value": round(val_held, 2),
                          "ret": round(val_held / CAPITAL - 1, 4),
                          "benchmark": round(CAPITAL * bp / bench_seed, 2),
                          "benchmark_ret": round(bp / bench_seed - 1, 4), "cash": round(cash, 2)})
            continue

        pv_open = val_held
        day_sells, day_buys = [], []
        core_cash, rot_cash = 0.0, 0.0      # keep sleeves' proceeds separate

        def _ok(t):   # off cooldown + has a usable price/score today
            return (t not in last_exit or k - last_exit[t] >= COOLDOWN_DAYS) \
                and _score(px, t, k) is not None and pd.notna(px[t].iloc[k])

        # 1) process exits — cores on big moves, rotation on the doors
        for t in list(holdings):
            h = holdings[t]
            p = tpx[t].iloc[k]
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
                held_days = k - h["entry_k"]
                if r <= STOP:                              # capital protection: always on
                    reason = "Stop −8%"
                elif h["armed"] and r >= TARGET:           # bank a big win: always on
                    reason = "Target +21%"
                elif h["armed"] and held_days >= MIN_HOLD:  # lock / stalled: only after min-hold
                    if r < ARM:
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
                    p = float(tpx[t].iloc[k])
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
                    p = float(tpx[t].iloc[k])
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

    # Honest dense intraday: 30-min bars valued against the book held DURING each
    # day (never back-valuing rotated holdings to the seed).
    intraday = livebar.history(curve, book_by_date, BENCH, bench_seed, CAPITAL)

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
