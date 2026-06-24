"""Midday fills. From a cutoff date forward, trades change hands in the
12:00-1:30pm ET window (the mean of the 12:00/12:30/13:00 30-min bars) instead of
the daily close. Older trades keep the close. The daily NAV curve still marks at
the close — only the *trade* price moves to midday.

Yahoo serves ~2 months of 30-min data, which is plenty: the cutoff is "now", so
only going-forward trades are ever repriced, and those days are always recent.
"""
from __future__ import annotations

import pandas as pd
import yfinance as yf

# Trades from this date forward fill at the 12:00-1:30pm window; before it, at the
# close (so every trade already on the books stays exactly as it happened).
FROM = "2026-06-24"

# 30-min bars (ET, regular session) whose START falls in the 12:00-1:30pm window
_BARS = {"12:00", "12:30", "13:00"}


def fetch(tickers, start):
    """Mean of the 12:00-1:30pm ET bars per (date, ticker). DataFrame indexed by
    'YYYY-MM-DD' (str), columns = tickers; NaN where a bar is unavailable."""
    tks = list(dict.fromkeys(t for t in tickers if t))
    if not tks:
        return pd.DataFrame()
    # 30-min data only goes back ~60 days, so clamp the start to the recent window
    recent = (pd.Timestamp.now("UTC") - pd.Timedelta(days=58)).strftime("%Y-%m-%d")
    start = max(str(start), recent)
    try:
        raw = yf.download(tks, start=start, interval="30m", auto_adjust=False,
                          progress=False, group_by="ticker", prepost=False)
    except Exception:
        return pd.DataFrame()
    cols = {}
    for t in tks:
        try:
            s = raw[t]["Close"] if len(tks) > 1 else raw["Close"]
        except Exception:
            continue
        s = s.dropna()
        if s.empty:
            continue
        idx = s.index
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_convert("America/New_York")
        keep = [hm in _BARS for hm in idx.strftime("%H:%M")]
        if not any(keep):
            continue
        w = s[keep]
        days = pd.Index(idx[keep]).strftime("%Y-%m-%d")
        cols[t] = w.groupby(days).mean()
    return pd.DataFrame(cols) if cols else pd.DataFrame()


def blend(px, mid, cutoff):
    """A trade-price frame shaped like `px` (daily closes): rows on/after `cutoff`
    take the 1pm price from `mid` where available; everything else stays the close.
    Identical to `px` for every date before the cutoff (so history never moves)."""
    tpx = px.copy()
    if mid is None or getattr(mid, "empty", True):
        return tpx
    for d in px.index:
        if d < cutoff or d not in mid.index:
            continue
        for t in px.columns:
            if t in mid.columns and pd.notna(mid.at[d, t]):
                tpx.at[d, t] = float(mid.at[d, t])
    return tpx
