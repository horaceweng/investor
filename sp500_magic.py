#!/usr/bin/env python3
"""神奇公式 (Greenblatt, The Little Book That Beats the Market) - S&P 500 版。

盈餘殖利率 = EBIT(近四季) / EV;  EV = 市值 + 總負債 - 現金
資本報酬率 = EBIT / (淨營運資金 + 淨固定資產)
  淨營運資金 = (流動資產 - 現金) - (流動負債 - 短期借款)
兩項各自排名 (1=最好) 後相加, 總和越小越好。排除金融股、公用事業股, 以及 EBIT<=0 或投入資本<=0 者。

用法: .venv/bin/python sp500_magic.py [--top 30]
"""
import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf

from sp500_losers import UNIVERSES, get_universe
from yf_util import require_enough, retry

EXCLUDE = {"Financials", "Utilities"}


def pick(df, names, col=0):
    for n in names:
        if n in df.index and pd.notna(df.loc[n].iloc[col]):
            return float(df.loc[n].iloc[col])
    return None


def fetch(sym):
    try:
        t = yf.Ticker(sym)
        q = retry(lambda: t.quarterly_income_stmt)
        bs = retry(lambda: t.quarterly_balance_sheet)
        ebit_row = next((n for n in ("EBIT", "Operating Income") if n in q.index), None)
        ebit = q.loc[ebit_row].iloc[:4].sum() if ebit_row and q.loc[ebit_row].iloc[:4].notna().sum() == 4 else None
        cash = pick(bs, ["Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents"]) or 0
        debt = pick(bs, ["Total Debt"]) or 0
        cur_a, cur_l = pick(bs, ["Current Assets"]), pick(bs, ["Current Liabilities"])
        cur_debt = pick(bs, ["Current Debt And Capital Lease Obligation", "Current Debt"]) or 0
        ppe = pick(bs, ["Net PPE"])
        mcap = retry(lambda: t.fast_info.get("marketCap"))
        nwc = (cur_a - cash) - (cur_l - cur_debt) if cur_a is not None and cur_l is not None else None
        return {"代號": sym, "EBIT": ebit, "市值": mcap, "現金": cash, "總負債": debt,
                "淨營運資金": nwc, "淨固定資產": ppe, "最新季報": str(bs.columns[0].date())}
    except Exception:
        return {"代號": sym}


PARTIAL = "sp500_magic_partial.csv"   # 逐檔財報(各股票池共用), 每列帶抓取時間 ts
PARTIAL_TTL = 3 * 24 * 3600            # 財報一季才更新一次; 超過 3 天視為過期, 更新時重抓


def raw_path(universe: str) -> str:
    return "sp500_magic_raw.csv" if universe == "sp500" else f"{universe}_magic_raw.csv"


def _load_partial():
    if not os.path.exists(PARTIAL):
        return pd.DataFrame(columns=["代號", "ts"])
    have = pd.read_csv(PARTIAL)
    if "ts" not in have:                      # 舊檔沒有時間欄: 以檔案修改時間代替
        have["ts"] = os.path.getmtime(PARTIAL)
    return have


def compute(top: int = 30, use_cache: bool = False, universe: str = "sp500"):
    """回傳 (前 top 名 DataFrame, (股票池, 排除金融公用後, 可排名檔數))。"""
    RAW = raw_path(universe)
    if use_cache:
        df = pd.read_csv(RAW)
    else:
        cons = get_universe(universe)
        # 可續傳: 成功抓到的列存在 PARTIAL, 被限流時下次只補抓缺的/過期的
        have = _load_partial()
        fresh = have[have["ts"] > time.time() - PARTIAL_TTL]
        todo = [x for x in cons["Symbol"] if x not in set(fresh["代號"])]
        keep = have[~have["代號"].isin(todo)]      # 不重抓的舊列; 要重抓的先丟掉, 抓到再補回
        print(f"  財報: 待抓 {len(todo)} 檔 (有效快取 {len(cons) - len(todo)} 檔)", flush=True)
        rows = []

        def save():
            new = pd.DataFrame([dict(x, ts=time.time()) for x in rows if x.get("EBIT") is not None])
            # 過期但這次重抓失敗(如被限流)的舊資料先保留, 不要丟掉
            stale_ok = have[have["代號"].isin(todo) & ~have["代號"].isin(new["代號"] if len(new) else [])]
            out = pd.concat([keep, stale_ok, new])
            out.to_csv(PARTIAL, index=False)
            return out

        with ThreadPoolExecutor(max_workers=3) as ex:
            for k, r in enumerate(ex.map(lambda x: (time.sleep(0.3), fetch(x))[1], todo), 1):
                rows.append(r)
                if k % 50 == 0:
                    save()
        ok_rows = save()
        df = ok_rows.drop(columns=["ts"]).merge(cons, left_on="代號", right_on="Symbol")
        require_enough(len(df), len(cons), "神奇公式財報", RAW, min_ratio=0.6)
        df.to_csv(RAW, index=False, encoding="utf-8-sig")
    total = len(df)

    df = df[~df["GICS Sector"].isin(EXCLUDE)].copy()
    df["EV"] = df["市值"] + df["總負債"] - df["現金"]
    df["投入資本"] = df["淨營運資金"] + df["淨固定資產"]
    ok = df[(df["EBIT"] > 0) & (df["EV"] > 0) & (df["投入資本"] > 0)].copy()
    ok["盈餘殖利率%"] = ok["EBIT"] / ok["EV"] * 100
    ok["資本報酬率%"] = ok["EBIT"] / ok["投入資本"] * 100
    ok["EY名次"] = ok["盈餘殖利率%"].rank(ascending=False)
    ok["ROC名次"] = ok["資本報酬率%"].rank(ascending=False)
    ok["總名次"] = ok["EY名次"] + ok["ROC名次"]
    res = ok.sort_values(["總名次", "EY名次"]).head(top).reset_index(drop=True)
    res["市值(B)"] = res["市值"] / 1e9
    return res, (total, len(df), len(ok))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--cached", action="store_true", help="使用上次抓的快取檔")
    ap.add_argument("--universe", choices=list(UNIVERSES), default="sp500")
    args = ap.parse_args()

    res, (total, after, ok) = compute(args.top, args.cached, args.universe)
    res.index += 1
    res.to_csv("sp500_magic.csv", index_label="排名", encoding="utf-8-sig")
    print(f"股票池: {total} 檔 -> 排除金融/公用事業後 {after} -> 資料完整且為正 {ok}")
    print(res[["代號", "Security", "GICS Sector", "市值(B)", "盈餘殖利率%", "資本報酬率%", "EY名次", "ROC名次", "總名次"]]
          .to_string(float_format=lambda x: f"{x:,.1f}"))


if __name__ == "__main__":
    main()
