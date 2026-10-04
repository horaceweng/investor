#!/usr/bin/env python3
"""列出過去 N 週 S&P 500 成分股中跌幅最大的公司。

用法: .venv/bin/python sp500_losers.py [--weeks 13] [--top 40]
"""
import argparse
import io
from datetime import date, timedelta

import pandas as pd
import requests
import yfinance as yf

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def get_constituents() -> pd.DataFrame:
    html = requests.get(WIKI_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    df = pd.read_html(io.StringIO(html))[0]
    df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)  # BRK.B -> BRK-B
    return df[["Symbol", "Security", "GICS Sector"]]


UNIVERSES = {"sp500": "S&P 500", "ndx": "Nasdaq 100"}


def get_universe(name: str = "sp500") -> pd.DataFrame:
    """回傳 DataFrame[Symbol, Security, GICS Sector]。Nasdaq 100 的板塊借用 S&P 500 的 GICS 分類
    (Nasdaq 官方資料沒有板塊); 不在 S&P 500 內的少數幾檔板塊留空。"""
    if name == "sp500":
        return get_constituents()
    if name != "ndx":
        raise ValueError(f"未知的股票池: {name!r}")
    from navellier import universe
    ndx, sp = universe.get_nasdaq100(), universe.get_sp500()
    sector = dict(zip(sp["symbol"], sp["sector"]))
    return pd.DataFrame({"Symbol": ndx["symbol"], "Security": ndx["name"],
                         "GICS Sector": ndx["symbol"].map(sector).fillna("")})


def compute(weeks: int = 13, top: int = 40, universe: str = "sp500"):
    """回傳 (結果 DataFrame, 起始交易日, 最新交易日)。"""
    cons = get_universe(universe)
    end = date.today()
    start = end - timedelta(weeks=weeks)

    # 多抓幾天，確保起始日(遇週末/假日)前有一筆收盤價可用
    px = yf.download(
        cons["Symbol"].tolist(),
        start=start - timedelta(days=7),
        end=end + timedelta(days=1),
        auto_adjust=True,
        progress=False,
    )["Close"]

    before = px[px.index <= pd.Timestamp(start)]
    start_px = before.ffill().iloc[-1]
    end_px = px.ffill().iloc[-1]

    res = pd.DataFrame({"起始價": start_px, "最新價": end_px})
    res["13週%"] = (res["最新價"] / res["起始價"] - 1) * 100
    res = res.dropna().join(cons.set_index("Symbol"))
    res = res.sort_values("13週%").head(top).reset_index(names="代號")
    return res, before.index[-1].date(), px.index[-1].date()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", type=int, default=13)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--universe", choices=list(UNIVERSES), default="sp500")
    args = ap.parse_args()

    res, base_date, last_date = compute(args.weeks, args.top, args.universe)
    print(f"區間: {base_date} -> {last_date} (約 {args.weeks} 週, 調整後收盤價)")
    res.index += 1
    print(res[["代號", "Security", "GICS Sector", "起始價", "最新價", "13週%"]]
          .to_string(float_format=lambda x: f"{x:,.2f}"))
    res.to_csv("sp500_losers.csv", index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
