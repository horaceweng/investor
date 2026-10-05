"""股價表現: 1M / 3M / 6M / 1Y 漲跌幅 (調整後收盤價, 含股利與分割)。"""
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from investor.data_sources.yahoo import retry

PERF = ["1M", "3M", "6M", "1Y"]
OFFSETS = {"1M": pd.DateOffset(months=1), "3M": pd.DateOffset(months=3),
           "6M": pd.DateOffset(months=6), "1Y": pd.DateOffset(years=1)}


def performance(tickers) -> dict:
    """回傳 {代號: {1M,3M,6M,1Y}} (%); 以各檔最後一個交易日回推。"""
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return {}
    px = retry(lambda: yf.download(tickers, start=date.today() - timedelta(days=400),
                                   auto_adjust=True, progress=False)["Close"])
    if isinstance(px, pd.Series):
        px = px.to_frame(tickers[0])
    out = {}
    for t in px.columns:
        s = px[t].dropna()
        if s.empty:
            continue
        last, ld = s.iloc[-1], s.index[-1]
        row = {}
        for lab, off in OFFSETS.items():
            prior = s[s.index <= ld - off]
            row[lab] = (last / prior.iloc[-1] - 1) * 100 if len(prior) else float("nan")
        out[t] = row
    return out
