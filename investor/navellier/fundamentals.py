"""Navellier 基本面資料: 由 Yahoo 抓各檔原始季度序列(帶快取), 交給 factors 算因子、grading 評分。

快取存的是「原始季度序列」而非算好的因子 (data/cache/fundamentals.json), 所以公式改版時 factors.derive() 直接離線重算,
不必重抓 (只有原始欄位改變才需要把 CACHE_VERSION +1)。被 Yahoo 限流時已抓到的部分會存檔, 再按一次更新會接續。

最新一季空欄位用 TradingView 補值 (逐季歷史), 記錄在 raw 資料的 _src: {欄位: "tv", ...}。
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
from investor.data_sources import tradingview
from investor.fileio import read_json, write_json
from investor.navellier import grading, estimates
from investor.navellier.factors import derive

warnings.filterwarnings("ignore")

CACHE_VERSION = 5            # 原始欄位改變時 +1, 舊快取視為過期並重抓
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


def _fill_tv_gaps(fund, tickers):
    """用 TradingView 補 Yahoo 最新季度的空值: _rev/_ni/_eps/_fcf。

    條件: 對 4 個序列中, Yahoo 最新值為 None (或序列不足 5 個有效值) 且 TV 對應 _h 序列至少 5 個有效值者,
    以 TV 序列整段取代 (TV 最新在前, 反轉後取最後 6 個)。_opinc、_eq 不動。
    TV 抓取失敗時只警告, 不影響既有 Yahoo 結果。
    """
    try:
        tv_data = tradingview.fetch_fundamentals(tickers)
    except Exception as e:
        warnings.warn(f"TradingView 補值失敗: {e}")
        return

    if not tv_data:
        return

    # 對應關係: Yahoo key -> TV _h key
    map_to_tv = {
        "_rev": "total_revenue_fq_h",
        "_ni": "net_income_fq_h",
        "_eps": "earnings_per_share_diluted_fq_h",
        "_fcf": "free_cash_flow_fq_h",
    }

    cache = _load_cache()
    for t in tickers:
        if t not in fund:
            continue

        raw_data = fund[t]
        sources = {}

        for yahoo_key, tv_key in map_to_tv.items():
            yahoo_seq = raw_data.get(yahoo_key)
            tv_seq = tv_data.get(t, {}).get(tv_key)

            # 判斷是否需要用 TV 取代: Yahoo 最新值為 None 或序列不足 5 個有效值
            # 且 TV 至少有 5 個有效值
            need_tv = False
            if tv_seq is not None:
                tv_valid = [v for v in tv_seq if v is not None]
                if len(tv_valid) >= 5:
                    if yahoo_seq is None:
                        need_tv = True
                    else:
                        yahoo_valid = [v for v in yahoo_seq if v is not None]
                        if len(yahoo_valid) < 5 or (len(yahoo_seq) > 0 and yahoo_seq[-1] is None):
                            need_tv = True

            if need_tv:
                # TV 最新在前, 取前 6 個(最新 6 季), 反轉成舊→新格式
                raw_data[yahoo_key] = list(reversed(tv_seq[:6]))
                sources[yahoo_key] = "tv"

        # 保存來源標記
        if sources:
            raw_data["_src"] = sources

        # 同時嘗試抓 TV 的最新季驚喜
        eps_surprise = tv_data.get(t, {}).get("eps_surprise_percent_fq")
        rev_surprise = tv_data.get(t, {}).get("revenue_surprise_percent_fq")
        if eps_surprise is not None:
            raw_data["eps_surprise_last_pct"] = eps_surprise
        if rev_surprise is not None:
            raw_data["rev_surprise_last_pct"] = rev_surprise

        # 更新快取中該檔的 raw 資料
        if t in cache:
            cache[t]["data"] = raw_data

    _save_cache(cache)


def compute(ab_results=None, tickers=None, peers=None):
    """抓(或讀快取)基本面並評級。ab_results: alpha_beta.compute() 的結果; 給了才算「30% 基本面 + 70% 量化」綜合評級。
    peers: peers.build() 的結果; 給了就改為「每檔對自己的同業全市場」評級 (見 grading.grade_vs_peers), 否則在這份 tickers 內互比。"""
    if not tickers:
        raise ValueError("tickers 不能是空的")
    notes = {}
    fund = _fetch_all(tickers, notes)

    # 用 TradingView 補 Yahoo 最新季度的空值
    _fill_tv_gaps(fund, tickers)

    # 重新 derive (以應用 TV 補的值)
    for t in tickers:
        fund[t] = derive(fund[t])

    # 存預估快照
    try:
        estimates.snapshot(tickers)
    except Exception as e:
        warnings.warn(f"預估快照失敗: {e}")

    # 計算預估修正與取得技術面資料
    technicals_data = {}
    try:
        snapshots = estimates.load_snapshots()
        revisions = estimates.compute_revisions(snapshots)
        # 併進各檔 fund dict
        for t in tickers:
            if t in revisions:
                fund[t].update(revisions[t])

        # 取得技術面資料
        technicals_data = estimates.get_technicals()
    except Exception as e:
        warnings.warn(f"預估修正計算失敗: {e}")

    require_enough(sum(1 for t in tickers if fund[t].get("n_q")), len(tickers),
                   "Navellier 基本面", "既有報告", min_ratio=0.5)
    nav_info = {}
    if peers:
        scores, fund_grade, combined, nav_info = grading.grade_vs_peers(
            peers["target_fund"], tickers, peers["industry_of"], peers["peer_fund"], ab_results or {}, peers["peer_nav"])
    else:
        scores, fund_grade, combined = grading.grade(fund, tickers, ab_results)
    return {"fundamentals": fund, "factor_quintiles": scores, "fund_grade": fund_grade,
            "combined_30_70": combined, "notes": notes, "nav_info": nav_info,
            "unavailable": [], "technicals": technicals_data}
