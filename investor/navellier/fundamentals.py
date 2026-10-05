"""Navellier 基本面資料: 由 Yahoo 抓各檔原始季度序列(帶快取), 交給 factors 算因子、grading 評分。

快取存的是「原始季度序列」而非算好的因子 (data/cache/fundamentals.json), 所以公式改版時 factors.derive() 直接離線重算,
不必重抓 (只有原始欄位改變才需要把 CACHE_VERSION +1)。被 Yahoo 限流時已抓到的部分會存檔, 再按一次更新會接續。
"""
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

from investor import paths
from investor.data_sources.yahoo import require_enough, retry
from investor.fileio import read_json, write_json
from investor.navellier import grading
from investor.navellier.factors import derive

warnings.filterwarnings("ignore")

CACHE_VERSION = 4            # 原始欄位改變時 +1, 舊快取視為過期並重抓
CACHE_TTL = 3 * 24 * 3600   # 財報一季才更新一次, 單檔結果快取 3 天; 也讓被限流中斷後可接續


def row(df, *names):
    for n in names:
        if n in df.index:
            return df.loc[n]
    return None


def get(t, attr):
    try:
        df = retry(lambda: getattr(yf.Ticker(t), attr))
        if df is None or df.empty:
            return None
        return df.sort_index(axis=1)
    except YFRateLimitError:
        raise                      # 限流要往上報, 不能當成「沒資料」
    except Exception:
        return None



def fetch_ticker(t, notes):
    """抓原始季度序列(存進快取)與財報驚喜, 再交給 _derive 算因子。"""
    qi, qc, qb = get(t, "quarterly_income_stmt"), get(t, "quarterly_cashflow"), get(t, "quarterly_balance_sheet")
    r = {}

    def raw(df, key, *names):
        sr = row(df, *names) if df is not None else None
        if sr is not None:
            r[key] = [None if pd.isna(v) else float(v) for v in sr.iloc[-6:]]

    raw(qi, "_rev", "Total Revenue")
    raw(qi, "_opinc", "Operating Income")
    raw(qi, "_eps", "Diluted EPS")
    raw(qi, "_ni", "Net Income", "Net Income Common Stockholders")
    raw(qc, "_fcf", "Free Cash Flow", "Operating Cash Flow")
    raw(qb, "_eq", "Total Stockholder Equity", "Stockholders Equity", "Total Equity Gross Minority Interest")
    try:
        ed = retry(lambda: yf.Ticker(t).get_earnings_dates(limit=8))
        if ed is not None and "Surprise(%)" in ed.columns:
            sv = pd.to_numeric(ed["Surprise(%)"], errors="coerce").dropna()
            if len(sv):
                r["surprise_avg"] = float(sv.mean())
                r["beat_rate"] = float((sv > 0).mean() * 100)
                r["n_surprises"] = int(len(sv))
    except YFRateLimitError:
        raise
    except Exception as e:
        notes[t] = f"surprises unavailable: {e}"
    return derive(r)


def _load_cache():
    return read_json(paths.FUNDAMENTALS_CACHE, {})


def _save_cache(cache):
    write_json(paths.FUNDAMENTALS_CACHE, cache)


def _fetch_all(tickers, notes):
    """抓各檔基本面(有快取的直接用)。被限流時儲存已完成的進度, 再按一次會接續而不是從頭來。"""
    cache, fund, now = _load_cache(), {}, time.time()
    todo = []
    for t in tickers:
        c = cache.get(t)
        if c and c.get("v") == CACHE_VERSION and now - c["ts"] < CACHE_TTL:
            fund[t] = derive(dict(c["data"]))
            if c.get("note"):
                notes[t] = c["note"]
        else:
            todo.append(t)
    if todo:
        print(f"  基本面: 快取 {len(fund)} 檔, 需抓 {len(todo)} 檔", flush=True)
    lock, abort, done = threading.Lock(), threading.Event(), [0]

    def work(t):
        if abort.is_set():
            return
        try:
            n = {}
            r = fetch_ticker(t, n)
            time.sleep(0.2)
        except YFRateLimitError:
            abort.set()
            return
        with lock:
            fund[t] = r
            cache[t] = {"v": CACHE_VERSION, "ts": time.time(), "data": r, "note": n.get(t)}
            if n.get(t):
                notes[t] = n[t]
            done[0] += 1
            if done[0] % 10 == 0:
                _save_cache(cache)

    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(work, todo))
    if todo:
        _save_cache(cache)
    if abort.is_set():
        raise RuntimeError(f"Yahoo 限流: 基本面已完成 {len(fund)}/{len(tickers)} 檔並存檔, 稍後再按一次更新會接續")
    return fund


def compute(ab_results=None, tickers=None):
    """抓(或讀快取)基本面並評級。ab_results: alpha_beta.compute() 的結果; 給了才算「30% 基本面 + 70% 量化」綜合評級。"""
    if not tickers:
        raise ValueError("tickers 不能是空的")
    notes = {}
    fund = _fetch_all(tickers, notes)
    require_enough(sum(1 for t in tickers if fund[t].get("n_q")), len(tickers),
                   "Navellier 基本面", "既有報告", min_ratio=0.5)
    scores, fund_grade, combined = grading.grade(fund, tickers, ab_results)
    return {"fundamentals": fund, "factor_quintiles": scores, "fund_grade": fund_grade,
            "combined_30_70": combined, "notes": notes,
            "unavailable": ["analyst_earnings_revisions: needs Bloomberg/FactSet/paid API "
                            "(no historical estimate-revision series in free data)"]}
