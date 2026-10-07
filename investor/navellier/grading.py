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
    在「這份 tickers 名單」內分五等 (指數股票池用)。回傳 (factor_quintiles, fund_grade, combined)。"""
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


MIN_PEERS = 10          # 同業基準至少這麼多檔才評; 太少 (如 ETF 沒有同業) 就不評


def percentile(v, ref):
    """v 在 ref 中的百分位 (0~1): 小於 v 的比例 + 一半的相同值。ref 為空回傳 None。"""
    if not ref:
        return None
    return (sum(1 for r in ref if r < v) + 0.5 * sum(1 for r in ref if r == v)) / len(ref)


def quintile_of(p):
    """百分位 (0~1) -> 五分位 1..5 (5 最好)。"""
    return min(int(p * 5), 4) + 1


def grade_vs_peers(target_fund: dict, tickers, industry_of: dict, peer_fund: dict, ab_results: dict, peer_nav: dict):
    """每一檔「跟自己的同業全市場」比, 而不是只跟清單裡的股票比 (清單裡的本來就是篩選過的, 清單內互比沒有意義)。
    target_fund: {代號: 因子 dict}; industry_of: {代號: 產業}; peer_fund: {產業: {代號: 因子 dict}} (同業基準);
    ab_results: 這些股票自己的 alpha_beta 結果; peer_nav: {產業: [同業 52 週 Alpha/SD...]}。
    因子與量化分數都用「同一套算法」算出, 目標與同業才可比。
    回傳 (factor_quintiles, fund_grade, combined, nav_info); nav_info = {代號: {"pct": 0~1, "grade": A~E}}。"""
    scores, fund_grade, combined, nav_info = {}, {}, {}, {}
    for t in tickers:
        ind = industry_of.get(t)
        refs = list((peer_fund.get(ind) or {}).values())
        n_peers = len(refs)
        info = {"industry": ind, "n_peers": n_peers}
        fd = target_fund.get(t)
        if fd is None or ind is None or n_peers < MIN_PEERS:
            fund_grade[t] = {"avg": None, "n_factors": 0, "grade": "N/A", **info, "no_peers": True}
            combined[t] = {"fund_grade": "N/A", "alpha_over_sd": (ab_results.get(t) or {}).get("nav_score"), "overall": "N/A", **info}
            continue
        sc = {}
        for f in FACTORS:
            v = factor_value(fd, f)
            ref = [x for x in (factor_value(p, f) for p in refs) if x is not None]
            if v is not None and len(ref) >= MIN_PEERS:
                sc[f] = quintile_of(percentile(v, ref))
        if len(sc) >= MIN_FACTORS:
            scores[t] = sc
            avg = sum(sc.values()) / len(sc)
            fund_grade[t] = {"avg": round(avg, 2), "n_factors": len(sc), "grade": letter(avg), **info}
        else:
            fund_grade[t] = {"avg": None, "n_factors": len(sc), "grade": "N/A", **info}
        a = ab_results.get(t) or {}
        nref = peer_nav.get(ind) or []
        if a.get("eligible") and len(nref) >= MIN_PEERS:
            p = percentile(a["nav_score"], nref)
            q = quintile_of(p)
            nav_info[t] = {"pct": p, "grade": "ABCDE"[5 - q]}
        fg = fund_grade[t]
        if fg["grade"] == "N/A" or t not in nav_info:
            combined[t] = {"fund_grade": fg["grade"], "alpha_over_sd": a.get("nav_score"), "overall": "N/A", **info}
            continue
        q = 5 - "ABCDE".index(nav_info[t]["grade"])
        total = FUND_WEIGHT * fg["avg"] + QUANT_WEIGHT * q
        combined[t] = {"fund_avg": fg["avg"], "fund_grade": fg["grade"], "quant_quintile": q, "alpha_over_sd": a.get("nav_score"),
                       "combined": round(total, 2), "overall": letter(total), **info}
    return scores, fund_grade, combined, nav_info
