"""Navellier 基本面因子: 由原始季度序列算出各因子 (純計算, 不連網, 可單獨測試)。

因子 (書上 8 個, 免費資料可算 7 個; 「分析師預估修正」需要付費資料):
  sales_yoy 營收年增 | margin_exp_yoy_pp 營業利益率年增 | earn_yoy EPS 年增 | earn_accel 盈餘動能 |
  surprise_avg 財報驚喜 | fcf_yoy 自由現金流年增 | roe_ttm 股東權益報酬率

盈餘動能 (earn_accel), 書上原文: 「我們會衡量公司在四季內的變化率。我們所要尋找的是連續幾季逐漸加大的盈餘正向變化。」
實作在盈餘「成長率」上: 一階導數 = 季增率 g = EPS_t / EPS_{t-1} - 1; 二階導數 = 成長率的變化率
= (g_最新季 - g_前一季) / g_前一季 (百分比; 例: +347% -> +91% 是 -74%)。分數 = 連續幾季「成長率為正且比前一季更高」
(0~3), 同分再比二階導數; 需連續 5 季都有資料。(曾誤把「變化率」套在盈餘增加額上, 已移除; 最初的單步版本導數正確,
但沒有「連續」概念且略過虧損公司。)

資料處理原則:
- 位置運算 ([-1] vs [-5]) 一律在「保留空值」的原序列上做: yfinance 的季度欄位是真實連續的財季, 中間缺一季就必須得到 NaN,
  不能先 dropna 讓「4 季前」默默變成「更早的某一季」(實際案例: BRK-B 的 2025-09 淨利為空)。
- 只剪掉「尾端(最新)」的空值 (trim): Yahoo 對剛公布的最新一季常只有 EPS, 營收/營業利益/淨利為空。
- 虧損處理: 最新季 EPS / 自由現金流 <= 0 -> 該因子給最差; 由虧轉盈 -> 給最佳; 只有真正缺資料才略過。
"""
import numpy as np
import pandas as pd

FACTORS = ["sales_yoy", "margin_exp_yoy_pp", "earn_yoy", "earn_accel",
           "surprise_avg", "fcf_yoy", "roe_ttm"]


def ok(v):
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


def trim(x):
    """去掉尾端(最新)的空值, 讓『最新』對齊到最近一個有資料的季度。
    Yahoo 對剛公布的最新一季常只有 EPS, 營收/營業利益/淨利是空的; 若把空值當最新一欄, 營收年增、利益率、ROE 全部算不出來。
    只剪尾端, 中間的缺口保留, 所以 [-1] vs [-5] 的位置關係仍然是真正的相隔 4 季。"""
    s = pd.Series(x, dtype=float)
    while len(s) and pd.isna(s.iloc[-1]):
        s = s.iloc[:-1]
    return s


def derive(r):
    """由快取中的『原始季度序列』(_rev/_opinc/_eps/_ni/_fcf/_eq, 舊→新) 算出所有財報因子; 純計算, 不連網。
    公式或資料處理改版時只要改這裡, 不必重抓。"""
    rev = trim(r.get("_rev") or [])
    if len(rev):
        r["sales_qoq"], r["sales_yoy"] = growth(rev, 1), growth(rev, 4)
        r["n_q"] = int(rev.notna().sum())
    if r.get("_rev") and r.get("_opinc"):
        margin = trim(pd.Series(r["_opinc"], dtype=float) / pd.Series(r["_rev"], dtype=float))
        if len(margin):
            r["op_margin"] = float(margin.iloc[-1]) * 100
            r["margin_exp_yoy_pp"] = (float((margin.iloc[-1] - margin.iloc[-5]) * 100)
                                      if len(margin) >= 5 and pd.notna(margin.iloc[-5]) else np.nan)
    eps, ni = trim(r.get("_eps") or []), trim(r.get("_ni") or [])
    base = eps if eps.notna().sum() >= 5 else ni                 # EPS 資料太少時退而用淨利
    if len(base):
        r["earn_yoy"], r["earn_state"] = yoy_state(base)
        r.update(earn_accel(base))
        r["_base"] = [None if pd.isna(v) else float(v) for v in base.iloc[-6:]]
    fcf = trim(r.get("_fcf") or [])
    if len(fcf):
        r["fcf_yoy"], r["fcf_state"] = yoy_state(fcf)
    eq = trim(r.get("_eq") or [])
    if len(ni) >= 4 and len(eq) >= 2:
        last4, last2 = ni.iloc[-4:], eq.iloc[-2:]
        if last4.notna().all() and last2.notna().all() and last2.mean() != 0:
            r["roe_ttm"] = float(last4.sum() / last2.mean() * 100)
    return r


def factor_value(r, f):
    """排名用數值。虧損 -> 最差; 由虧轉盈 -> 最佳; 真正缺資料 -> None(略過)。"""
    st = r.get("earn_state") if f == "earn_yoy" else r.get("fcf_state") if f == "fcf_yoy" else None
    if st == "loss":
        return -1e9
    if st == "turnaround":
        return 1e9
    v = r.get(f)
    return v if ok(v) else None


def quintiles(vals):
    """vals: [(ticker, value)] -> {ticker: 1..5}。平手取平均名次, 避免同值因排序先後被分到不同等級。"""
    sr = pd.Series({t: v for t, v in vals}, dtype=float)
    q = ((sr.rank(method="average") - 1) / len(sr) * 5).astype(int).clip(upper=4) + 1
    return q.to_dict()
