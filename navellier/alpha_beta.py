"""Alpha/Beta calculator: industry-standard (5Y monthly vs S&P500) + Navellier-style (52W weekly).

Navellier-style is a documented approximation of his publicly-disclosed "reward/risk"
formula (Alpha / 52-week weekly-return std dev), confirmed via public sources — it does
NOT filter out short-covering-driven alpha, which he discloses but never publishes the
exact method for (proprietary).
"""
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yf_util import retry  # noqa: E402  (限流時指數退避重試)

warnings.filterwarnings("ignore")

TICKERS = ["NVDA", "TSM", "AMD", "AVGO", "GOOG", "MSFT", "AAPL", "MU", "SNDK",
           "COST", "BRK-B", "TSLA", "SPCX"]
BENCH = "SPY"   # S&P 500 total-return proxy
RF = "^IRX"     # 13-week T-bill (annualized %)
MIN_WEEKS = 52  # 少於 52 週資料者不參與評級(統計上太薄, 如剛上市的股票)


def complete_week_cutoff(today=None):
    """最近一個「已收完」的週五。週六起才算該週結束, 避免把未收完的一週算進 52 週窗口
    (否則同一週內不同天執行會得到不同分數)。"""
    today = today or date.today()
    last_fri = today - timedelta(days=(today.weekday() - 4) % 7)
    return last_fri if today > last_fri else last_fri - timedelta(days=7)


def _weekly(s, m):
    sw = s.resample("W-FRI").last().pct_change().dropna()
    mw = m.resample("W-FRI").last().pct_change().dropna()
    return sw, mw


def weekly_scores(px, tickers, bench, cutoff, n_back):
    """由歷史價格回算過去 n_back 週(含 cutoff 本週)各週的 nav_score: {週五日期: {代號: 分數}}。
    nav_score 只取決於價格, 所以可精確回算, 不必等數週累積歷史才能偵測動能冷卻。"""
    out = {}
    m = px[bench].dropna()
    for t in tickers:
        if t not in px.columns:
            continue
        s = px[t].dropna()
        idx = s.index.intersection(m.index)
        sw, mw = _weekly(s.loc[idx], m.loc[idx])
        common = sw.index.intersection(mw.index)
        for k in range(n_back + 1):
            end = cutoff - pd.Timedelta(days=7 * k)
            c = common[common <= end][-MIN_WEEKS:]
            if len(c) < MIN_WEEKS:
                continue
            a = (sw.loc[c] - mw.loc[c]).values
            sd = a.std(ddof=1) * np.sqrt(52)
            out.setdefault(str(end.date()), {})[t] = round(float(a.mean() * 52 / sd), 3) if sd > 0 else 0.0
    return out


def reg_beta_alpha(rs, rm):
    """OLS: rs = a + b*rm + e. Returns beta, monthly alpha, R^2, n."""
    x = np.vstack([np.ones_like(rm), rm]).T
    coef, *_ = np.linalg.lstsq(x, rs, rcond=None)
    a, b = coef
    pred = a + b * rm
    ss_res = np.sum((rs - pred) ** 2)
    ss_tot = np.sum((rs - rs.mean()) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return b, a, r2, len(rs)


def dl(tickers, period):
    df = retry(lambda: yf.download(tickers, period=period, interval="1d",
                                   auto_adjust=True, progress=False, threads=True))
    if df is None or df.empty:
        raise RuntimeError("價格下載為空(可能被 Yahoo 限流), 請稍後重試")
    closes = df["Close"]
    if isinstance(closes, pd.Series):
        closes = closes.to_frame(tickers if isinstance(tickers, str) else tickers[0])
    return closes


def compute(tickers=None, bench=BENCH, verbose=True, history_weeks=0):
    """回傳 (results, missing, weekly_hist); weekly_hist 為回算的各週 nav_score (history_weeks=0 時為 {})。"""
    tickers = tickers or TICKERS
    all_syms = tickers + [bench]
    if verbose:
        print("downloading daily prices (6y)...", flush=True)
    px = dl(all_syms, "6y")
    if verbose:
        print("downloading T-bill...", flush=True)
    rf_px = dl(RF, "6y")
    rf_daily = rf_px.iloc[:, 0] / 100.0 / 360.0  # approx daily rf

    cutoff = pd.Timestamp(complete_week_cutoff())
    results = {}
    missing = []
    for t in tickers:
        if t not in px.columns or px[t].dropna().empty:
            missing.append(t)
            continue
        s = px[t].dropna()
        m = px[bench].dropna()
        idx = s.index.intersection(m.index)
        s, m = s.loc[idx], m.loc[idx]

        # ---------- Industry standard: 5Y monthly ----------
        sm = s.resample("ME").last().pct_change().dropna()
        mm = m.resample("ME").last().pct_change().dropna()
        rf_m = rf_daily.resample("ME").sum().reindex(sm.index).ffill().fillna(0)
        common = sm.index.intersection(mm.index).intersection(rf_m.index)
        rse = (sm.loc[common] - rf_m.loc[common]).values
        rme = (mm.loc[common] - rf_m.loc[common]).values
        rse, rme = rse[-60:], rme[-60:]
        beta, alpha_m, r2, n = reg_beta_alpha(rse, rme)
        alpha_ann = alpha_m * 12
        beta_raw, _, _, _ = reg_beta_alpha(sm.loc[common].values[-60:], mm.loc[common].values[-60:])

        # ---------- Navellier style: 52W weekly ----------
        sw, mw = _weekly(s, m)
        sw, mw = sw[sw.index <= cutoff], mw[mw.index <= cutoff]   # 只用已收完的週
        common_w = sw.index.intersection(mw.index)[-MIN_WEEKS:]
        aw = (sw.loc[common_w] - mw.loc[common_w]).values  # weekly alpha = stock - market
        nav_alpha_ann = aw.mean() * 52
        nav_sd_ann = aw.std(ddof=1) * np.sqrt(52)
        # nav_score = "reward/risk" = alpha / std dev of alpha, i.e. Information Ratio
        nav_score = nav_alpha_ann / nav_sd_ann if nav_sd_ann > 0 else 0.0
        beta_52, _, r2_52, _ = reg_beta_alpha(sw.loc[common_w].values, mw.loc[common_w].values)

        results[t] = {
            "n_months": n,
            "n_weeks": len(common_w),
            "eligible": bool(len(common_w) >= MIN_WEEKS),
            "asof_week": str(cutoff.date()),
            "beta_5y": round(float(beta), 3),
            "beta_5y_raw": round(float(beta_raw), 3),
            "alpha_ann_5y": round(float(alpha_ann) * 100, 2),  # %
            "r2_5y": round(float(r2), 3),
            "nav_alpha_ann": round(float(nav_alpha_ann) * 100, 2),  # %
            "nav_sd_ann": round(float(nav_sd_ann) * 100, 2),        # %
            "nav_score": round(float(nav_score), 3),      # alpha / stddev ("reward/risk")
            "beta_52w": round(float(beta_52), 3),
            "r2_52w": round(float(r2_52), 3),
            "last_close": round(float(s.iloc[-1]), 2),
            "last_date": str(s.index[-1].date()),
        }

    # Navellier-style grades: quintiles of nav_score within this universe (approximation, disclosed)
    # 只在資料足夠(>=52 週)的股票之間分五等; 資料不足者標 N/A
    scores = sorted([(t, v["nav_score"]) for t, v in results.items() if v["eligible"]],
                    key=lambda x: x[1], reverse=True)
    labels = ["A", "B", "C", "D", "E"]
    n_scored = len(scores)
    for i, (t, _) in enumerate(scores):
        q = min(i * 5 // max(n_scored, 1), 4)
        results[t]["nav_grade"] = labels[q]
    for t, v in results.items():
        v.setdefault("nav_grade", "N/A")

    wk_hist = weekly_scores(px, [t for t in tickers if t in results], bench, cutoff, history_weeks) if history_weeks else {}
    return results, missing, wk_hist


if __name__ == "__main__":
    import json
    results, missing, _ = compute()
    out = {"results": results, "missing": missing,
           "method": ("industry: 5Y monthly excess returns vs SPY, rf=13W T-bill; "
                      "navellier-style: 52W weekly (stock-mkt), score=ann_alpha/ann_sd (reward/risk), "
                      "grades=quintiles (approx, not short-covering-adjusted)")}
    print(json.dumps(out, indent=1))
