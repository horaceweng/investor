"""Navellier-style fundamental momentum: 8 factors (book: The Little Book That Makes You Rich).
Computable from yfinance quarterly statements: 7 of 8 (analyst revisions need paid data).

Bug fixes vs. the original draft:
- growth()/margin/ROE no longer dropna() before taking a positional (-1, -1-k) offset.
  yfinance's quarterly columns are real fiscal quarter-end dates in order; dropping NaN
  rows before indexing silently shifts "4 quarters ago" to "whichever older quarter still
  has data" when an interior quarter is missing (confirmed real case: BRK-B's Net Income
  is NaN for 2025-09-30 in yfinance, which previously made "earn_yoy" quietly compare
  quarters 5 periods apart and label it as YoY).
- earn_momentum_pp and fcf_yoy now guard on a positive base period (like earn_yoy already
  did), so a swing through a loss/negative-FCF quarter doesn't flip the growth rate's sign
  into a misleading number.
- fundamental/quantitative blend weight corrected to the publicly-documented 30% fundamental
  / 70% quantitative (previous draft had it inverted, 70/30).

Later changes (dashboard integration):
- module-level fund/notes globals removed (compute() is re-run inside a long-lived server).
- hard-coded EXCLUDE removed: a ticker with fewer than MIN_FACTORS computable factors is graded
  N/A automatically (e.g. a recent IPO with <5 quarters of statements).
- Yahoo rate limits are retried and then raised, instead of silently returning empty data.
"""
import json
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from yf_util import require_enough, retry  # noqa: E402

warnings.filterwarnings("ignore")

TICKERS = ["NVDA", "TSM", "AMD", "AVGO", "GOOG", "MSFT", "AAPL", "MU", "SNDK",
           "COST", "BRK-B", "TSLA", "SPCX"]
CACHE_FILE = Path(__file__).resolve().parent.parent / "data_navellier" / "fund_cache.json"
CACHE_TTL = 3 * 24 * 3600   # 財報一季才更新一次, 單檔結果快取 3 天; 也讓被限流中斷後可接續
MIN_FACTORS = 3     # 可計算因子少於此數者不評級 (N/A)

FACTORS = ["sales_yoy", "margin_exp_yoy_pp", "earn_yoy", "earn_momentum_pp",
           "surprise_avg", "fcf_yoy", "roe_ttm"]

FUND_WEIGHT, QUANT_WEIGHT = 0.30, 0.70  # per Navellier's disclosed 30/70 blend


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


def _ok(v):
    return v is not None and not (isinstance(v, float) and np.isnan(v))


def growth(s, k=1, guard_positive_base=False):
    """(s[-1]/s[-1-k]) - 1, using positional offsets on the ORIGINAL (non-dropna'd) series.

    Deliberately does not drop NaNs first: yfinance's quarterly columns are already spaced
    one real fiscal quarter apart, so an interior gap must produce NaN here, not silently
    reach further back and mislabel the result as a k-quarter change.
    """
    if s is None or len(s) <= k:
        return np.nan
    a, b = s.iloc[-1], s.iloc[-1 - k]
    if pd.isna(a) or pd.isna(b):
        return np.nan
    if guard_positive_base and b <= 0:
        return np.nan
    if not guard_positive_base and b == 0:
        return np.nan
    return float(a / b - 1)


def _compute_ticker(t, notes):
    qi, qc, qb = get(t, "quarterly_income_stmt"), get(t, "quarterly_cashflow"), get(t, "quarterly_balance_sheet")
    r = {}
    if qi is not None:
        rev = row(qi, "Total Revenue")
        opinc = row(qi, "Operating Income")
        netinc = row(qi, "Net Income", "Net Income Common Stockholders")
        eps = row(qi, "Diluted EPS")
        if rev is not None:
            r["sales_qoq"] = growth(rev, 1)
            r["sales_yoy"] = growth(rev, 4)
            r["n_q"] = int(rev.dropna().shape[0])
        if opinc is not None and rev is not None:
            margin = opinc / rev  # keep NaNs in place, do not dropna before positional offset
            last = margin.iloc[-1]
            r["op_margin"] = float(last) * 100 if pd.notna(last) else np.nan
            if len(margin) >= 5 and pd.notna(margin.iloc[-1]) and pd.notna(margin.iloc[-5]):
                r["margin_exp_yoy_pp"] = float((margin.iloc[-1] - margin.iloc[-5]) * 100)
            else:
                r["margin_exp_yoy_pp"] = np.nan
        base = eps if eps is not None and eps.dropna().shape[0] >= 5 else netinc
        if base is not None:
            # YoY growth only meaningful when the base quarter was profitable (avoid sign flip)
            r["earn_yoy"] = growth(base, 4, guard_positive_base=True)
            # momentum = QoQ acceleration: g(latest QoQ) - g(previous QoQ), both guarded positive-base
            g_now = growth(base, 1, guard_positive_base=True)
            g_prev = growth(base.iloc[:-1], 1, guard_positive_base=True) if len(base) >= 3 else np.nan
            r["earn_momentum_pp"] = float((g_now - g_prev) * 100) if pd.notna(g_now) and pd.notna(g_prev) else np.nan
    if qc is not None:
        fcf = row(qc, "Free Cash Flow", "Operating Cash Flow")
        if fcf is not None:
            r["fcf_yoy"] = growth(fcf, 4, guard_positive_base=True)
    if qi is not None and qb is not None:
        netinc = row(qi, "Net Income", "Net Income Common Stockholders")
        eq = row(qb, "Total Stockholder Equity", "Stockholders Equity", "Total Equity Gross Minority Interest")
        if netinc is not None and eq is not None:
            last4 = netinc.iloc[-4:] if len(netinc) >= 4 else None
            last2eq = eq.iloc[-2:] if len(eq) >= 2 else None
            ni_ttm = float(last4.sum()) if last4 is not None and last4.notna().all() else np.nan
            eq_avg = float(last2eq.mean()) if last2eq is not None and last2eq.notna().all() else np.nan
            r["roe_ttm"] = float(ni_ttm / eq_avg * 100) if (pd.notna(ni_ttm) and eq_avg not in (0, None) and pd.notna(eq_avg)) else np.nan
    try:
        ed = retry(lambda: yf.Ticker(t).get_earnings_dates(limit=8))
        if ed is not None and "Surprise(%)" in ed.columns:
            s = pd.to_numeric(ed["Surprise(%)"], errors="coerce").dropna()
            if len(s):
                r["surprise_avg"] = float(s.mean())
                r["beat_rate"] = float((s > 0).mean() * 100)
                r["n_surprises"] = int(len(s))
    except YFRateLimitError:
        raise
    except Exception as e:
        notes[t] = f"surprises unavailable: {e}"
    return r


def _load_cache():
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def _save_cache(cache):
    CACHE_FILE.parent.mkdir(exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache))
    tmp.replace(CACHE_FILE)


def _fetch_all(tickers, notes):
    """抓各檔基本面(有快取的直接用)。被限流時儲存已完成的進度, 再按一次會接續而不是從頭來。"""
    cache, fund, now = _load_cache(), {}, time.time()
    todo = []
    for t in tickers:
        c = cache.get(t)
        if c and now - c["ts"] < CACHE_TTL:
            fund[t] = c["data"]
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
            r = _compute_ticker(t, n)
            time.sleep(0.2)
        except YFRateLimitError:
            abort.set()
            return
        with lock:
            fund[t] = r
            cache[t] = {"ts": time.time(), "data": r, "note": n.get(t)}
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
    """ab_results: dict from alpha_beta.compute()[0], keyed by ticker with 'nav_score' / 'eligible'.
    If given, blends fundamentals (30%) with the quantitative reward/risk score (70%)."""
    tickers = tickers or TICKERS
    notes = {}
    fund = _fetch_all(tickers, notes)
    require_enough(sum(1 for t in tickers if fund[t].get("n_q")), len(tickers),
                   "Navellier 基本面", "既有報告", min_ratio=0.5)

    n_factors = {t: sum(1 for f in FACTORS if _ok(fund[t].get(f))) for t in tickers}
    scored_tickers = [t for t in tickers if n_factors[t] >= MIN_FACTORS]
    scores = {}
    for f in FACTORS:
        vals = [(t, fund[t].get(f)) for t in scored_tickers if _ok(fund[t].get(f))]
        vals.sort(key=lambda x: x[1])
        n = len(vals)
        for i, (t, v) in enumerate(vals):
            q = min(int(i * 5 / max(n, 1)), 4)
            scores.setdefault(t, {})[f] = q + 1  # 1..5, higher=better

    fund_grade = {}
    for t in tickers:
        sc = scores.get(t, {})
        if t in scored_tickers and sc:
            avg = sum(sc.values()) / len(sc)
            fund_grade[t] = {"avg": round(avg, 2), "n_factors": len(sc),
                              "grade": "ABCDE"[min(int((5 - avg) * 5 / 4), 4)]}
        else:
            fund_grade[t] = {"avg": None, "n_factors": n_factors[t], "grade": "N/A"}

    combined = {}
    if ab_results is not None:
        nav_q = {t: v["nav_score"] for t, v in ab_results.items()
                 if t in fund_grade and fund_grade[t]["grade"] != "N/A" and v.get("eligible", True)}
        sv = sorted(nav_q.items(), key=lambda x: x[1])
        nq = len(sv)
        qscore = {}
        for i, (t, _) in enumerate(sv):
            qscore[t] = min(int(i * 5 / max(nq, 1)), 4) + 1

        for t in tickers:
            fg = fund_grade[t]
            alpha_over_sd = ab_results.get(t, {}).get("nav_score")
            if fg["grade"] == "N/A" or t not in qscore:
                combined[t] = {"fund_grade": fg["grade"], "alpha_over_sd": alpha_over_sd, "overall": "N/A"}
                continue
            fscore = fg["avg"]
            total = FUND_WEIGHT * fscore + QUANT_WEIGHT * qscore[t]
            combined[t] = {"fund_avg": fg["avg"], "fund_grade": fg["grade"],
                            "quant_quintile": qscore[t], "alpha_over_sd": alpha_over_sd,
                            "combined": round(total, 2),
                            "overall": "ABCDE"[min(int((5 - total) * 5 / 4), 4)]}

    out = {"fundamentals": fund, "factor_quintiles": scores, "fund_grade": fund_grade,
           "combined_30_70": combined, "notes": notes,
           "unavailable": ["analyst_earnings_revisions: needs Bloomberg/FactSet/paid API "
                            "(no historical estimate-revision series in free data)"]}
    return out


if __name__ == "__main__":
    import json
    try:
        ab = json.load(open("/tmp/alpha_beta_results.json"))["results"]
    except FileNotFoundError:
        ab = None
    out = compute(ab)
    fund = out["fundamentals"]
    print(f"{'T':6} " + " ".join(f"{f[:10]:>10}" for f in FACTORS))
    for t in TICKERS:
        rowv = []
        for f in FACTORS:
            v = fund[t].get(f)
            rowv.append(f"{v:10.1f}" if v is not None and v == v else "       nan")
        print(f"{t:6} " + " ".join(rowv) + f"  nq={fund[t].get('n_q', '?')}")
    print("\nGrades:")
    for t in TICKERS:
        c = out["combined_30_70"].get(t, {"fund_grade": "N/A", "overall": "N/A"})
        print(f"{t:6} fund={c.get('fund_grade')}({out['fund_grade'][t]['avg']}) "
              f"quant_q={c.get('quant_quintile', '-')} alpha/sd={c.get('alpha_over_sd')} "
              f"overall={c.get('overall')}")
    print("\nnotes:", out["notes"])
