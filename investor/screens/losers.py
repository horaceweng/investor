#!/usr/bin/env python3
"""13 週跌幅: 列出股票池(S&P 500 / Nasdaq 100)中, 過去 N 週跌幅最大的公司。

用法: .venv/bin/python -m investor.screens.losers [--weeks 13] [--top 40] [--universe sp500|ndx]
"""
import argparse
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from investor import paths
from investor.universe import UNIVERSES, for_screens


def compute(weeks: int = 13, top: int = 40, universe: str = "sp500"):
    """回傳 (結果 DataFrame, 起始交易日, 最新交易日)。"""
    cons = for_screens(universe)
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
    res.to_csv(paths.ensure_parent(paths.EXPORTS / f"losers_{args.universe}.csv"), index=False, encoding="utf-8-sig")


if __name__ == "__main__":
    main()
