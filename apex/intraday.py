"""Intraday equity curve at 30-minute resolution.

The daily snapshots are the official record, but they make the chart one coarse
line per day. This values the book at 30-min intervals (reconstructing the
holdings the portfolio *entered* each day with, since trades execute at the close)
so the dashboard shows real within-day movement, like a brokerage app.

Built once per daily run and merged into state.json as `intraday`. Network call
is wrapped so a failure never breaks the daily update.
"""
from __future__ import annotations

import json
from collections import defaultdict

import pandas as pd
import yfinance as yf

from . import ledger
from .paths import STATE_JSON


def build(days=30, interval="30m"):
    with ledger.connect() as conn:
        initial = float(ledger.get_meta(conn, "initial_deposit", 100000) or 100000)
        fw = ledger.get_meta(conn, "framework", {}) or {}
        bench = (fw.get("instruments") or {}).get("benchmark", "^GSPC")
        txns = [dict(r) for r in conn.execute(
            "SELECT date,ticker,action,shares FROM transactions ORDER BY id").fetchall()]
        eq = [dict(r) for r in conn.execute(
            "SELECT date,cash FROM equity ORDER BY date").fetchall()]
    if not txns or not eq:
        return []

    cash_by_date = {r["date"]: r["cash"] for r in eq}
    eq_dates = sorted(cash_by_date)
    tickers = sorted({t["ticker"] for t in txns})
    txn_by_date = defaultdict(list)
    for t in txns:
        txn_by_date[t["date"]].append(t)
    txn_dates = sorted(txn_by_date)

    launch = eq_dates[0]

    def holdings_asof(d, inclusive):
        h = defaultdict(float)
        for dt in txn_dates:
            if dt > d or (not inclusive and dt == d):
                break
            for t in txn_by_date[dt]:
                h[t["ticker"]] += t["shares"] if t["action"] == "BUY" else -t["shares"]
        return {k: v for k, v in h.items() if abs(v) > 1e-9}

    def holdings_for(d):
        # Use the book held *during* the session (pre-close trades), so a rotation
        # at the close doesn't retroactively distort that whole day.
        return holdings_asof(d, inclusive=False)

    def cash_for(d):
        prior = [x for x in eq_dates if x < d]
        return cash_by_date[prior[-1]] if prior else initial

    start = max((pd.Timestamp.now("UTC") - pd.Timedelta(days=days)).strftime("%Y-%m-%d"),
                eq_dates[0])
    try:
        raw = yf.download(tickers + [bench], start=start, interval=interval,
                          auto_adjust=True, progress=False, group_by="ticker")
    except Exception:
        return []

    closes = {}
    for tk in tickers + [bench]:
        try:
            closes[tk] = raw[tk]["Close"]
        except Exception:
            pass
    if bench not in closes:
        return []
    df = pd.DataFrame(closes).ffill().dropna(subset=[bench])
    if df.empty:
        return []
    b0 = float(df[bench].iloc[0])

    hcache, ccache, out = {}, {}, []
    for ts, row in df.iterrows():
        d = ts.strftime("%Y-%m-%d")
        if d < eq_dates[0]:
            continue
        if d not in hcache:
            hcache[d] = holdings_for(d)
            ccache[d] = cash_for(d)
        h, cash = hcache[d], ccache[d]
        val = cash + sum(sh * row[tk] for tk, sh in h.items()
                         if tk in row and pd.notna(row[tk]))
        bp = row[bench]
        out.append({
            "t": ts.strftime("%Y-%m-%d %H:%M"),
            "value": round(float(val), 2),
            "benchmark": round(float(initial * bp / b0), 2) if pd.notna(bp) else None,
        })
    # The book is seeded at the launch *close*, so it has no real intraday history
    # on launch day. Keep only the launch-day close as the $100k inception anchor;
    # real intraday begins the next session.
    launch_pts = [p for p in out if p["t"][:10] == launch]
    if launch_pts:
        out = [launch_pts[-1]] + [p for p in out if p["t"][:10] != launch]
    return out


def attach():
    """Build the intraday series and merge it into data/state.json."""
    try:
        series = build()
    except Exception:
        series = []
    if not STATE_JSON.exists():
        return 0
    state = json.loads(STATE_JSON.read_text())
    state["intraday"] = series
    STATE_JSON.write_text(json.dumps(state, indent=2))
    return len(series)
