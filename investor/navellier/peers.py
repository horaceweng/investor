"""同業比較基準: 每一檔自訂清單的股票, 拿「自己 TradingView 產業的全市場股票 (市值 >= 20 億美元)」當比較對象。

為什麼: 放進觀察清單的股票本來就是篩選過的, 清單內互相排名沒有意義; 要問的是「在它那一類全部股票裡, 它相對好不好」。
一致性: 目標與同業的基本面因子都用同一份 TradingView 資料、同一套算法 (factors.derive) 算出, 量化分數 (52 週 Alpha/SD) 也用
同一個函式、同一個基準指數, 才可比。TradingView 沒有營業利益與股東權益的逐季歷史, 所以同業比較少「營業利益率年增」, 財報驚喜只有
最新一季 (不是 8 季均值), 預估修正沒有同業資料; 其餘因子齊全 (可評級至少需要 5 個)。
"""
from investor import paths
from investor.data_sources import tradingview
from investor.fileio import read_json, write_json
from investor.navellier import alpha_beta
from investor.navellier.factors import derive

MIN_CAP = 2e9          # 同業只取市值 >= 20 億美元 (太小的公司波動大、資料薄)
CHUNK = 150            # 同業價格分批下載, 每批存檔, 被限流中斷後可接續

_SERIES = (("_rev", "total_revenue_fq_h"), ("_ni", "net_income_fq_h"),
           ("_eps", "earnings_per_share_diluted_fq_h"), ("_fcf", "free_cash_flow_fq_h"))


def _f(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return None if v != v else v


def tv_factors(entry):
    """TradingView 一檔的原始資料 -> 因子 dict (與 factors.derive 同一套算法)。"""
    r = {}
    for key, tvk in _SERIES:
        h = entry.get(tvk)
        if h is not None and len(h):
            r[key] = [_f(v) for v in list(h)[:6]][::-1]            # TradingView 最新在前 -> 舊到新
    out = derive(r)
    roe, sur = _f(entry.get("return_on_equity")), _f(entry.get("eps_surprise_percent_fq"))
    if roe is not None:
        out["roe_ttm"] = roe
    if sur is not None:
        out["surprise_avg"] = sur                                  # 同業比較只有最新一季驚喜
    return out


def build(tickers, targets=None, peer_rows=None):
    """抓同業基準的基本面。回傳 {"industry_of": {代號: 產業}, "peer_fund": {產業: {代號: 因子}}, "target_fund": {代號: 因子},
    "mcap": {代號: 市值}}。不在 TradingView 的股票 (如 ETF) 不會有產業, 之後不評等級。"""
    targets = tradingview.fetch_peer_targets(tickers) if targets is None else targets
    industry_of = {t: e["industry"] for t, e in targets.items() if e.get("industry")}
    inds = sorted(set(industry_of.values()))
    rows = (tradingview.fetch_industry(inds, MIN_CAP) if peer_rows is None else peer_rows) if inds else {}
    peer_fund = {ind: {} for ind in inds}
    for t, e in rows.items():
        if e.get("industry") in peer_fund:
            peer_fund[e["industry"]][t] = tv_factors(e)
    return {"industry_of": industry_of, "peer_fund": peer_fund,
            "target_fund": {t: tv_factors(e) for t, e in targets.items()}}


def nav_scores(tickers, cutoff, path=None):
    """同業的 52 週 Alpha/SD: {代號: 分數或 None (資料不足)}。依「已收完的週」(cutoff) 存檔, 同一週內不重抓;
    分批下載、每批存檔, 被 Yahoo 限流中斷後再跑會接續。"""
    path = path or paths.PEER_SCORES
    cache = read_json(path, {})
    if cache.get("cutoff") != str(cutoff):
        cache = {"cutoff": str(cutoff), "scores": {}}
    scores = cache["scores"]
    todo = [t for t in tickers if t not in scores]
    for i in range(0, len(todo), CHUNK):
        part = todo[i:i + CHUNK]
        res, _, _ = alpha_beta.compute(part, verbose=False)
        for t in part:
            scores[t] = res[t]["nav_score"] if t in res and res[t].get("eligible") else None
        write_json(path, cache)
    return {t: scores.get(t) for t in tickers}


def peer_nav(peer_fund, scores):
    """-> {產業: [同業的有效 Alpha/SD...]}"""
    return {ind: [scores[t] for t in d if scores.get(t) is not None] for ind, d in peer_fund.items()}
