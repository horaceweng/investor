#!/usr/bin/env python3
"""價值投資人最近一季買進家數最多的股票 (資料: SEC 13F-HR)。

用法: .venv/bin/python -m investor.superinvestors.buys [--quarter YYYY-MM-DD] [--top 20] [--history 4]
季度預設自動偵測: 取「已有 75% 以上經理人申報」的最新一季; 可用 --quarter 手動指定。
"買進" = 新建倉, 或持股數較上季增加 >= 5% (已用多數持有人同比例變動偵測並還原股票分割)。
"""
import argparse
import csv
import re
from collections import Counter, defaultdict
from datetime import date

import pandas as pd

from investor import paths
from investor.data_sources import sec
from investor.data_sources.openfigi import cusip_to_ticker

ETF_RE = re.compile(r"\b(ETF|ISHARES|SPDR|VANGUARD|INVESCO|PROSHARES|SELECT SECTOR|TRUST UNITS|INDEX FUND|WISDOMTREE|VANECK|FUND)\b", re.I)


def prev_quarters(q, n):
    """從季底日期往前推 n 季, 回傳由舊到新的季底日期 (含 q 本身, 共 n+1 個)。"""
    y, m = int(q[:4]), int(q[5:7])
    out = []
    for _ in range(n + 1):
        out.append(f"{y}-{m:02d}-{30 if m in (6, 9) else 31}")
        m -= 3
        if m < 1:
            m, y = m + 12, y - 1
    return out[::-1]


def quarter_stats(data, qi):
    """data[mgr] = {quarter: holdings}; 比較第 qi 季與前一季, 回傳 {cusip: stat} 與參與人數。"""
    qs, qp = qi
    pairs = {m: (h[qs], h[qp]) for m, h in data.items() if qs in h and qp in h}
    ratios = defaultdict(list)
    for cur, prv in pairs.values():
        for c, (s, _) in cur.items():
            if c in prv and prv[c][0] > 0:
                ratios[c].append(round(s / prv[c][0], 2))
    split = {}
    for c, rs in ratios.items():
        if len(rs) >= 4:
            mode, n = Counter(rs).most_common(1)[0]
            if n / len(rs) >= 0.5 and (mode >= 1.3 or mode <= 0.77):
                split[c] = mode
    stat = defaultdict(lambda: {"name": "", "new": [], "add": [], "holders": 0})
    for mgr, (cur, prv) in pairs.items():
        for c, (s, name) in cur.items():
            if ETF_RE.search(name):
                continue
            st = stat[c]; st["name"] = name; st["holders"] += 1
            p = prv.get(c, [0, ""])[0] * split.get(c, 1)
            if p == 0:
                st["new"].append(mgr)
            elif s >= p * 1.05:
                st["add"].append(mgr)
    # 所有持有人都是"新建倉"且 >=5 家: 典型分拆新股 / CUSIP 變更, 並非主動買進, 排除
    spun = {c for c, v in stat.items() if len(v["new"]) == v["holders"] and v["holders"] >= 5}
    return {c: v for c, v in stat.items() if c not in spun}, len(pairs), split


def detect_latest_quarter(mgrs, min_ratio: float = 0.75):
    """自動偵測最新一季: 從最近的季底往回, 取第一個「申報人數 >= min_ratio × 前一季申報人數」的季度。
    (季底後 45 天內只有部分人申報, 此時最新一季資料不完整, 會自動退回前一季。)"""
    today = date.today()
    qm = (today.month - 1) // 3 * 3          # 本季開始前一個月: 0/3/6/9
    newest = f"{today.year - 1}-12-31" if qm == 0 else f"{today.year}-{qm:02d}-28"
    cands = prev_quarters(newest, 2)[::-1]   # 新 -> 舊: 最近完整季底, 前一季, 前兩季
    filed = []
    for m in mgrs:
        f = sec.submissions(int(m["cik"]))["filings"]["recent"]
        filed.append({rd for form, rd in zip(f["form"], f["reportDate"]) if form == "13F-HR"})
    counts = {q: sum(q in s for s in filed) for q in cands}
    print("  各季申報人數:", counts, flush=True)
    for q, prev in zip(cands, cands[1:]):
        if counts[q] > 0 and counts[q] >= min_ratio * counts[prev]:
            return q
    return cands[-1]


def compute(quarter: str = None, top: int = 20, history: int = 4):
    """回傳 (df, 可比較經理人數, 季底日期清單); quarter=None 時自動偵測最新一季。"""
    mgrs = list(csv.DictReader(open(paths.MANAGERS_CSV)))
    if quarter is None:
        quarter = detect_latest_quarter(mgrs)
        print(f"  自動偵測最新一季: {quarter}", flush=True)
    quarters = prev_quarters(quarter, history)  # 舊 -> 新, 共 history+1 個
    data = {}
    for m in mgrs:
        cik, hs = int(m["cik"]), {}
        for q in quarters:
            acc = sec.find_filing(cik, q)
            h = sec.holdings(cik, acc) if acc else None
            if h:
                hs[q] = h
        data[m["manager"]] = hs
        print(f"{len(hs)}/{len(quarters)} 季  {m['manager']}", flush=True)

    per_q = {}
    for qp, qs in zip(quarters, quarters[1:]):
        per_q[qs] = quarter_stats(data, (qs, qp))
        print(f"{qs}: 可比較經理人 {per_q[qs][1]} 位")
    latest = quarter
    stat, n_mgr, split = per_q[latest]

    rows = []
    for c, v in stat.items():
        row = {"cusip": c, "公司": v["name"], "買進家數": len(v["new"]) + len(v["add"]),
               "新建倉": len(v["new"]), "加碼": len(v["add"]), "持有家數": v["holders"],
               "買進者": "; ".join(v["new"] + v["add"])}
        for q in quarters[1:]:
            sq = per_q[q][0].get(c)
            row[f"買進@{q[:7]}"] = len(sq["new"]) + len(sq["add"]) if sq else 0
        first = per_q[quarters[1]][0].get(c)
        row["持有家數變化"] = v["holders"] - (first["holders"] if first else 0)
        rows.append(row)
    df = pd.DataFrame(rows).sort_values(["買進家數", "新建倉"], ascending=False).head(top).reset_index(drop=True)

    df.insert(0, "代號", df["cusip"].map(cusip_to_ticker(df["cusip"].tolist())))
    # OpenFIGI 查不到的外國公司, 以公司名補上
    manual = {"TE CONNECTIVITY": "TEL", "CRH PLC": "CRH"}
    df["代號"] = [t or next((v for k, v in manual.items() if k in n.upper()), "") for t, n in zip(df["代號"], df["公司"])]
    df["公司"] = df["公司"].str.title()
    return df, n_mgr, quarters


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quarter", default=None, help="季底日期; 省略則自動偵測")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--history", type=int, default=4, help="趨勢回看幾季")
    args = ap.parse_args()

    df, n_mgr, quarters = compute(args.quarter, args.top, args.history)
    df.index += 1
    df.to_csv(paths.ensure_parent(paths.EXPORTS / "superinvestor_buys.csv"), index_label="排名", encoding="utf-8-sig")
    tcols = [f"買進@{q[:7]}" for q in quarters[1:]]
    print(f"\n=== {quarters[-1]} 季買進家數最多 Top {args.top}  (本季可比較經理人 {n_mgr} 位) ===")
    print(df[["代號", "公司"] + tcols + ["新建倉", "持有家數", "持有家數變化"]].to_string())


if __name__ == "__main__":
    main()
