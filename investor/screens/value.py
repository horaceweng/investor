#!/usr/bin/env python3
"""S&P 500 價值面排行: 本益比最低、股價淨值比最低、股息殖利率最高。

用法: .venv/bin/python -m investor.screens.value [--top 40] [--universe sp500|ndx] [--cached]
基本面資料抓一次後存成 data/cache/screens/<股票池>_fundamentals.csv。
"""
import argparse
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf

from investor import paths
from investor.data_sources.yahoo import require_enough, retry
from investor.universe import UNIVERSES, for_screens



def cache_path(universe: str):
    return paths.SCREEN_CACHE / f"{universe}_fundamentals.csv"


def fetch(sym: str) -> dict:
    try:
        i = retry(lambda: yf.Ticker(sym).info)
    except Exception:
        i = {}
    price = i.get("currentPrice") or i.get("regularMarketPrice")
    rate = i.get("dividendRate")
    return {
        "代號": sym,
        "股價": price,
        "本益比": i.get("trailingPE"),
        "股價淨值比": i.get("priceToBook"),
        # 用年化股利/股價自行計算，避免不同版本 dividendYield 單位不一致
        "殖利率%": (rate / price * 100) if rate and price else None,
    }


def compute(top: int = 40, use_cache: bool = False, universe: str = "sp500") -> dict:
    """回傳 {'pe': df, 'pb': df, 'yield': df, 'counts': (pe,pb,yield,total)}。"""
    CACHE = cache_path(universe)
    if use_cache:
        df = pd.read_csv(CACHE)
    else:
        cons = for_screens(universe)
        with ThreadPoolExecutor(max_workers=4) as ex:
            rows = list(ex.map(fetch, cons["Symbol"]))
        df = pd.DataFrame(rows).merge(cons, left_on="代號", right_on="Symbol")
        require_enough(int(df["股價淨值比"].notna().sum()), len(df), "價值面基本面", str(CACHE))
        df.to_csv(paths.ensure_parent(CACHE), index=False, encoding="utf-8-sig")

    # 本益比/淨值比為負(虧損或淨值為負)沒有比較意義，排除
    pe = df[df["本益比"] > 0]
    pb = df[df["股價淨值比"] > 0]
    pick = lambda d, col, asc: d.dropna(subset=[col]).sort_values(col, ascending=asc).head(top).reset_index(drop=True)
    return {
        "pe": pick(pe, "本益比", True),
        "pb": pick(pb, "股價淨值比", True),
        "yield": pick(df, "殖利率%", False),
        "counts": (len(pe), len(pb), int(df["殖利率%"].notna().sum()), len(df)),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--cached", action="store_true", help="使用上次抓的快取檔")
    ap.add_argument("--universe", choices=list(UNIVERSES), default="sp500")
    args = ap.parse_args()

    r = compute(args.top, args.cached, args.universe)
    print(f"有效資料筆數: 本益比 {r['counts'][0]}, 淨值比 {r['counts'][1]}, 殖利率 {r['counts'][2]} / {r['counts'][3]}")
    for key, title in (("pe", "本益比最低"), ("pb", "股價淨值比最低"), ("yield", "股息殖利率最高")):
        d = r[key]; d.index += 1
        print(f"\n=== {title} {args.top} ===")
        print(d[["代號", "Security", "GICS Sector", "股價", "本益比", "股價淨值比", "殖利率%"]]
              .to_string(float_format=lambda x: f"{x:,.2f}"))


if __name__ == "__main__":
    main()
