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

Earnings momentum ("earn_accel"), per the book: "we measure the rate of change over four quarters; we look for
positive earnings changes that grow progressively larger over consecutive quarters". Implemented on the earnings
*growth rate*: 1st derivative = QoQ growth rate g = EPS_t / EPS_{t-1} - 1; 2nd derivative = rate of change of that
growth rate = (g_latest - g_previous) / g_previous (a percentage; e.g. +347% -> +91% is -74%). The score is the number of consecutive latest quarters in which g is
positive and higher than the previous quarter's g (0-3), tie-broken by the 2nd derivative. It needs 5 consecutive
quarters without gaps. (History of this factor: an intermediate version applied the "rate of change" to the absolute
EPS *increase* instead of the growth rate -- that was a misreading and has been removed. The original single-step
version had the right derivative but no consecutiveness and skipped loss-making companies.)
Loss handling: if the latest quarter's EPS / free cash flow is <= 0 the YoY factor gets the worst score; a swing
from loss to profit gets the best score; only genuinely missing data is skipped.

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
CACHE_VERSION = 4            # 因子定義改變時 +1, 舊快取視為過期並重抓
CACHE_TTL = 3 * 24 * 3600   # 財報一季才更新一次, 單檔結果快取 3 天; 也讓被限流中斷後可接續
MIN_FACTORS = 5     # 可計算因子少於此數者不評級 (N/A)

FACTORS = ["sales_yoy", "margin_exp_yoy_pp", "earn_yoy", "earn_accel",
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


def earn_accel(series):
    """盈餘動能 = 盈餘成長率的變化率 (二階導數)。
    一階導數 = 盈餘成長率 g = 本季 EPS / 上季 EPS - 1 (季增率, 基期需為正);
    二階導數 = 成長率的變化率 = (g_最新季 - g_前一季) / g_前一季  (百分比; 前一季成長率需 > 0 才有意義)。
      例: 前一季 +347%、最新季 +91% -> (91% - 347%) / 347% = -74%。
    streak = 從最新一季往回, 連續幾季『成長率為正, 且比前一季更高』(0~3) -- 即書上的「連續幾季逐漸加大的盈餘正向變化」。
    需連續 5 季都有資料。排名: 先比 streak, 同分再比二階導數;
    最新季虧損 = 最差; 由虧轉盈 = 最佳; 前一季成長率 <= 0 而本季回升為正(『反彈』, 比值的正負號會反, 不算百分比) = 排在所有成長率減速者之前, 彼此依最新季成長率排序。"""
    out = {"earn_accel": np.nan, "earn_accel_streak": np.nan, "earn_accel_pct": np.nan, "earn_growth_pct": None,
           "earn_accel_case": None}
    if series is None or len(series) < 5:
        return out
    x = series.iloc[-5:].astype(float)
    if x.isna().any():
        return out
    e = x.values
    g = [(e[i] / e[i - 1] - 1) if e[i - 1] > 0 else np.nan for i in range(1, 5)]   # 4 個季增率 g[0..3], g[3]=最新
    streak, k = 0, 3
    while k >= 1 and np.isfinite(g[k]) and np.isfinite(g[k - 1]) and g[k] > g[k - 1] > 0:
        streak += 1
        k -= 1
    gn, gp = g[3], g[2]
    pct, case = np.nan, None
    if e[4] <= 0:
        tie, case = -1.0, "loss"                     # 最新季虧損
    elif np.isfinite(gn) and np.isfinite(gp) and gp > 0:
        pct = gn / gp - 1                            # 二階導數: 成長率的變化率
        tie = float(np.tanh(pct))                    # tanh 只用來壓進 (-1, 1), 同分排序用
    elif np.isfinite(gn) and np.isfinite(gp):        # 前一季成長率 <= 0 (EPS 較再前一季下滑): 比值的正負號會反, 不能算百分比
        if gn > 0:
            tie, case = float(np.tanh(gn)), "rebound"   # 本季回升為正成長: 優於一切成長率減速者; 彼此再依最新季成長率排序
        else:
            tie, case = (0.0 if gn > gp else -1.0), "declining"   # 兩季都在衰退 (衰退趨緩=中性, 惡化=最差)
    else:
        tie, case = 1.0, "turnaround"                # 前期 EPS <= 0, 成長率無法計算, 本季已轉為正 = 最佳
    out.update(earn_accel_streak=streak, earn_accel_pct=pct * 100 if np.isfinite(pct) else np.nan,
               earn_accel=streak + 0.5 * tie, earn_accel_case=case,
               earn_growth_pct=[None if not np.isfinite(v) else round(float(v) * 100, 1) for v in g])
    return out


def yoy_state(series):
    """回傳 (年增率, 狀態)。狀態: 'loss'=最新季<=0, 'turnaround'=4 季前<=0 且最新季>0, 'ok', None=缺資料。"""
    if series is None or len(series) < 5:
        return np.nan, None
    last, prior = series.iloc[-1], series.iloc[-5]
    if pd.isna(last) or pd.isna(prior):
        return np.nan, None
    if last <= 0:
        return np.nan, "loss"
    if prior <= 0:
        return np.nan, "turnaround"
    return float(last / prior - 1), "ok"


def _trim(x):
    """去掉尾端(最新)的空值, 讓『最新』對齊到最近一個有資料的季度。
    Yahoo 對剛公布的最新一季常只有 EPS, 營收/營業利益/淨利是空的; 若把空值當最新一欄, 營收年增、利益率、ROE 全部算不出來。
    只剪尾端, 中間的缺口保留, 所以 [-1] vs [-5] 的位置關係仍然是真正的相隔 4 季。"""
    s = pd.Series(x, dtype=float)
    while len(s) and pd.isna(s.iloc[-1]):
        s = s.iloc[:-1]
    return s


def _derive(r):
    """由快取中的『原始季度序列』(_rev/_opinc/_eps/_ni/_fcf/_eq, 舊→新) 算出所有財報因子; 純計算, 不連網。
    公式或資料處理改版時只要改這裡, 不必重抓。"""
    rev = _trim(r.get("_rev") or [])
    if len(rev):
        r["sales_qoq"], r["sales_yoy"] = growth(rev, 1), growth(rev, 4)
        r["n_q"] = int(rev.notna().sum())
    if r.get("_rev") and r.get("_opinc"):
        margin = _trim(pd.Series(r["_opinc"], dtype=float) / pd.Series(r["_rev"], dtype=float))
        if len(margin):
            r["op_margin"] = float(margin.iloc[-1]) * 100
            r["margin_exp_yoy_pp"] = (float((margin.iloc[-1] - margin.iloc[-5]) * 100)
                                      if len(margin) >= 5 and pd.notna(margin.iloc[-5]) else np.nan)
    eps, ni = _trim(r.get("_eps") or []), _trim(r.get("_ni") or [])
    base = eps if eps.notna().sum() >= 5 else ni                 # EPS 資料太少時退而用淨利
    if len(base):
        r["earn_yoy"], r["earn_state"] = yoy_state(base)
        r.update(earn_accel(base))
        r["_base"] = [None if pd.isna(v) else float(v) for v in base.iloc[-6:]]
    fcf = _trim(r.get("_fcf") or [])
    if len(fcf):
        r["fcf_yoy"], r["fcf_state"] = yoy_state(fcf)
    eq = _trim(r.get("_eq") or [])
    if len(ni) >= 4 and len(eq) >= 2:
        last4, last2 = ni.iloc[-4:], eq.iloc[-2:]
        if last4.notna().all() and last2.notna().all() and last2.mean() != 0:
            r["roe_ttm"] = float(last4.sum() / last2.mean() * 100)
    return r


def _compute_ticker(t, notes):
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
    return _derive(r)


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
        if c and c.get("v") == CACHE_VERSION and now - c["ts"] < CACHE_TTL:
            fund[t] = _derive(dict(c["data"]))
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


def _fv(r, f):
    """排名用數值。虧損 -> 最差; 由虧轉盈 -> 最佳; 真正缺資料 -> None(略過)。"""
    st = r.get("earn_state") if f == "earn_yoy" else r.get("fcf_state") if f == "fcf_yoy" else None
    if st == "loss":
        return -1e9
    if st == "turnaround":
        return 1e9
    v = r.get(f)
    return v if _ok(v) else None


def _quintiles(vals):
    """vals: [(ticker, value)] -> {ticker: 1..5}。平手取平均名次, 避免同值因排序先後被分到不同等級。"""
    sr = pd.Series({t: v for t, v in vals}, dtype=float)
    q = ((sr.rank(method="average") - 1) / len(sr) * 5).astype(int).clip(upper=4) + 1
    return q.to_dict()


def compute(ab_results=None, tickers=None):
    """ab_results: dict from alpha_beta.compute()[0], keyed by ticker with 'nav_score' / 'eligible'.
    If given, blends fundamentals (30%) with the quantitative reward/risk score (70%)."""
    tickers = tickers or TICKERS
    notes = {}
    fund = _fetch_all(tickers, notes)
    require_enough(sum(1 for t in tickers if fund[t].get("n_q")), len(tickers),
                   "Navellier 基本面", "既有報告", min_ratio=0.5)

    n_factors = {t: sum(1 for f in FACTORS if _fv(fund[t], f) is not None) for t in tickers}
    scored_tickers = [t for t in tickers if n_factors[t] >= MIN_FACTORS]
    scores = {}
    for f in FACTORS:
        vals = [(t, _fv(fund[t], f)) for t in scored_tickers if _fv(fund[t], f) is not None]
        if vals:
            for t, q in _quintiles(vals).items():
                scores.setdefault(t, {})[f] = q       # 1..5, higher=better

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
