"""Navellier 評級: 由各檔基本面因子與量化分數算出等級 (純計算, 不連網)。

- 基本面: 每個因子在股票池內分五等 (1~5, 平手取平均名次), 取各因子平均; 可計算因子少於 MIN_FACTORS 者不評級 (N/A)。
- 綜合 = 30% 基本面 + 70% 量化 (Navellier 公開的比例); 量化 = 52 週 Alpha/SD 在股票池內的五分位。
- 等級 A 最好、E 最差; 評級是「股票池內的相對排名」, 不是絕對好壞。
"""
from investor.navellier.factors import FACTORS, factor_value, quintiles

MIN_FACTORS = 5                          # 可計算因子少於此數者不評級 (N/A)
FUND_WEIGHT, QUANT_WEIGHT = 0.30, 0.70   # Navellier 公開的 30/70 比例


def letter(score: float) -> str:
    """1~5 分 (5 最好) -> A~E。"""
    return "ABCDE"[min(int((5 - score) * 5 / 4), 4)]


def grade(fund: dict, tickers, ab_results=None):
    """fund: {ticker: 因子 dict}; ab_results: alpha_beta.compute() 的結果 (含 nav_score / eligible), 給了才算綜合評級。
    回傳 (factor_quintiles, fund_grade, combined)。"""
    n_factors = {t: sum(1 for f in FACTORS if factor_value(fund[t], f) is not None) for t in tickers}
    scored_tickers = [t for t in tickers if n_factors[t] >= MIN_FACTORS]
    scores = {}
    for f in FACTORS:
        vals = [(t, factor_value(fund[t], f)) for t in scored_tickers if factor_value(fund[t], f) is not None]
        if vals:
            for t, q in quintiles(vals).items():
                scores.setdefault(t, {})[f] = q       # 1..5, higher=better

    fund_grade = {}
    for t in tickers:
        sc = scores.get(t, {})
        if t in scored_tickers and sc:
            avg = sum(sc.values()) / len(sc)
            fund_grade[t] = {"avg": round(avg, 2), "n_factors": len(sc), "grade": letter(avg)}
        else:
            fund_grade[t] = {"avg": None, "n_factors": n_factors[t], "grade": "N/A"}

    combined = {}
    if ab_results is not None:
        nav_q = {t: v["nav_score"] for t, v in ab_results.items()
                 if t in fund_grade and fund_grade[t]["grade"] != "N/A" and v.get("eligible", True)}
        sv = sorted(nav_q.items(), key=lambda x: x[1])
        nq = len(sv)
        qscore = {t: min(int(i * 5 / max(nq, 1)), 4) + 1 for i, (t, _) in enumerate(sv)}
        for t in tickers:
            fg = fund_grade[t]
            alpha_over_sd = ab_results.get(t, {}).get("nav_score")
            if fg["grade"] == "N/A" or t not in qscore:
                combined[t] = {"fund_grade": fg["grade"], "alpha_over_sd": alpha_over_sd, "overall": "N/A"}
                continue
            total = FUND_WEIGHT * fg["avg"] + QUANT_WEIGHT * qscore[t]
            combined[t] = {"fund_avg": fg["avg"], "fund_grade": fg["grade"],
                           "quant_quintile": qscore[t], "alpha_over_sd": alpha_over_sd,
                           "combined": round(total, 2), "overall": letter(total)}
    return scores, fund_grade, combined
