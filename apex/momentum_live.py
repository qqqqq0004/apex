"""The LIVE engine — concentrated thematic momentum, running forward from launch.

Strategy (your words): hold winners on hot themes, sell when the trend breaks,
rotate into the next hottest names. Mechanics:
  - Hold ~10 names, equal weight at entry, let winners run (no trimming the strong).
  - EXIT a name when it closes below its 50-day trend line, or falls 20% from its
    recent high. That's the "party's over" trigger.
  - REFILL empty slots with the next hottest in-uptrend names (via scout), funded
    by the freed cash.

Writes to the same ledger + dashboard as the rest of Apex, so you watch every move
and reason on the existing page. Run `init` once at launch, then `run` daily.
"""
from __future__ import annotations

import json
import sys

import pandas as pd
import yfinance as yf

from . import ledger, export, scout, intraday
from .paths import CONFIG_DIR

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BENCH = "^GSPC"


def _cfg():
    return json.loads((CONFIG_DIR / "live_book.json").read_text())


def _download(tickers, days=320, end=None):
    start = (pd.Timestamp.now("UTC") - pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    raw = yf.download(list(dict.fromkeys(tickers)), start=start, end=end,
                      auto_adjust=True, progress=False, group_by="ticker")
    out = {}
    for t in dict.fromkeys(tickers):
        try:
            out[t] = raw[t]["Close"]
        except (KeyError, TypeError):
            continue
    df = pd.DataFrame(out)
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def _min_framework():
    return {"instruments": {"benchmark": BENCH, "benchmark_label": "S&P 500"},
            "themes": {}}


def init(capital=None):
    cfg = _cfg()
    capital = capital or cfg["capital"]
    book = cfg["holdings"]
    df = _download(book + [BENCH]).ffill()
    last = df.iloc[-1]
    date = df.index[-1].strftime("%Y-%m-%d")

    ledger.reset_db()
    with ledger.connect() as conn:
        ledger.init_db(conn)
        ledger.set_meta(conn, "framework", _min_framework())
        ledger.set_meta(conn, "initial_deposit", capital)
        ledger.set_meta(conn, "start_date", date)
        ledger.set_meta(conn, "mode", "forward")
        ledger.set_meta(conn, "strategy", cfg["strategy"])
        w = 1.0 / len(book)
        cash = capital
        for t in book:
            price = float(last[t])
            alloc = capital * w
            shares = alloc / price
            ledger.upsert_position(conn, t, scout._theme_of(t), "momentum", w,
                                   thesis=f"Momentum leader in {scout._theme_of(t)}.",
                                   shares=shares, avg_entry=price)
            cash -= alloc
            ledger.log_transaction(conn, date, t, "BUY", shares, price, -alloc,
                                   "Launch", f"Seed: hot momentum pick, ~{w*100:.0f}%.")
        ledger.set_cash(conn, cash)
        ledger.snapshot_equity(conn, date, capital, cash, benchmark=float(last[BENCH]))
        ledger.set_meta(conn, "last_date", date)
        ledger.set_meta(conn, "last_committed_date", date)  # no same-day churn
        ledger.set_meta(conn, "last_prices",
                        {t: float(last[t]) for t in df.columns})
        ledger.log_decision(conn, date, "BUY",
                            f"Launched live momentum book: {len(book)} hot names, "
                            "equal weight. Riding winners; exit on trend break.",
                            rule="Launch", judge="momentum")
    export.export_state()
    intraday.attach()
    print(f"LIVE book launched {date} with ${capital:,.0f} across {len(book)} names.")
    return date


def run(end=None):
    """One forward step: apply trend-break exits, refill from the hottest names."""
    cfg = _cfg()
    exit_ma = cfg["exit_ma"]
    exit_dd = cfg["exit_drawdown_from_high"]
    target_n = cfg["target_positions"]

    with ledger.connect() as conn:
        holds = [p["ticker"] for p in ledger.open_positions(conn)]
    px = _download(holds + [BENCH], end=end).ffill()
    date = px.index[-1].strftime("%Y-%m-%d")
    last = px.iloc[-1]

    moves = 0
    sold_today = []
    with ledger.connect() as conn:
        if ledger.get_meta(conn, "last_committed_date") == date:
            print(f"{date} already processed.")
            return
        # --- exits: trend break ---
        for p in ledger.open_positions(conn):
            t = p["ticker"]
            s = px[t].dropna()
            if len(s) < exit_ma + 1:
                continue
            price = float(s.iloc[-1])
            ma = float(s.tail(exit_ma).mean())
            high = float(s.tail(120).max())
            below_ma = price < ma
            deep = price < (1 - exit_dd) * high
            if below_ma or deep:
                reason = (f"{t} broke its {exit_ma}-day trend (${price:.2f} < "
                          f"${ma:.2f} MA)" if below_ma else
                          f"{t} fell {(1-price/high)*100:.0f}% from its high")
                proceeds = p["shares"] * price
                ledger.set_cash(conn, ledger.get_cash(conn) + proceeds)
                ledger.set_shares(conn, t, 0.0, p["avg_entry"])
                ledger.log_transaction(conn, date, t, "SELL", p["shares"], price,
                                       proceeds, "Trend Break", reason)
                ledger.log_decision(conn, date, "SELL", reason + " — exit, lock it in.",
                                    ticker=t, rule="Trend Break", judge="momentum",
                                    price=price)
                sold_today.append(t)
                moves += 1

        held = [p["ticker"] for p in ledger.open_positions(conn)]
        empty = target_n - len(held)

    # --- refill empty slots with the hottest in-uptrend names not held ---
    if empty > 0:
        sc = scout.scan(end=end)
        cands = [r for _, r in sc.iterrows()
                 if r["above_200"] and r["ticker"] not in held
                 and r["ticker"] not in sold_today and r["ticker"] != BENCH]
        picks = cands[:empty]
        if picks:
            with ledger.connect() as conn:
                cash = ledger.get_cash(conn)
                per = cash / len(picks)
                for r in picks:
                    t = r["ticker"]
                    price = float(r["price"])
                    shares = per / price
                    ledger.upsert_position(conn, t, r["theme"], "momentum",
                                           1.0 / target_n,
                                           thesis=f"Rotated in: hot momentum, "
                                                  f"+{r['ret_6m']*100:.0f}% 6mo.",
                                           shares=shares, avg_entry=price)
                    ledger.set_cash(conn, ledger.get_cash(conn) - per)
                    ledger.log_transaction(conn, date, t, "BUY", shares, price, -per,
                                           "Rotation", f"Rotated in (hot, "
                                           f"+{r['ret_6m']*100:.0f}% 6mo).")
                    ledger.log_decision(conn, date, "BUY",
                                        f"Rotated into {t} — hottest available in "
                                        f"{r['theme']} (+{r['ret_6m']*100:.0f}% 6mo).",
                                        ticker=t, rule="Rotation", judge="momentum",
                                        price=price)
                    moves += 1

    # --- snapshot + export ---
    with ledger.connect() as conn:
        cash = ledger.get_cash(conn)
        total = cash + sum(p["shares"] * float(last.get(p["ticker"], p["avg_entry"]))
                           for p in ledger.open_positions(conn))
        ledger.snapshot_equity(conn, date, total, cash, benchmark=float(last[BENCH]))
        ledger.set_meta(conn, "last_date", date)
        ledger.set_meta(conn, "last_committed_date", date)
        lp = ledger.get_meta(conn, "last_prices", {})
        for p in ledger.open_positions(conn):
            lp[p["ticker"]] = float(last.get(p["ticker"], p["avg_entry"]))
        ledger.set_meta(conn, "last_prices", lp)
        if moves == 0:
            ledger.log_decision(conn, date, "NO_MOVE",
                                "All holdings still trending up — holding. Let winners "
                                "run.", rule="Hold", judge="momentum")
    export.export_state()
    intraday.attach()
    print(f"{date}: {moves} move(s). Portfolio ${total:,.0f}.")


def refresh():
    """Data-only refresh: re-price the book and rebuild the intraday curve, with
    NO trading. Safe to run repeatedly during the session so the dashboard (and the
    1D 'today' view) update live. Trades only ever happen in run() at the close."""
    with ledger.connect() as conn:
        holds = [p["ticker"] for p in ledger.open_positions(conn)]
    df = _download(holds + [BENCH], days=8).ffill()
    if df.empty:
        print("No data; skipping refresh.")
        return
    last = df.iloc[-1]
    with ledger.connect() as conn:
        lp = ledger.get_meta(conn, "last_prices", {}) or {}
        for t in holds + [BENCH]:
            if t in last and pd.notna(last[t]):
                lp[t] = float(last[t])
        ledger.set_meta(conn, "last_prices", lp)
    export.export_state()
    intraday.attach()
    print(f"Refreshed at {df.index[-1].strftime('%Y-%m-%d')} (no trades).")


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="apex.momentum_live")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("init"); pi.add_argument("--capital", type=float)
    sub.add_parser("run")
    sub.add_parser("refresh")
    a = ap.parse_args()
    if a.cmd == "init":
        init(a.capital)
    elif a.cmd == "run":
        run()
    elif a.cmd == "refresh":
        refresh()


if __name__ == "__main__":
    main()
