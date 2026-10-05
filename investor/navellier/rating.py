"""Weekly Navellier-style watchlist run: alpha/beta + fundamentals + momentum-cooling watch.

Quant score = Alpha/SD over the last 52 CLOSED weeks (see alpha_beta). Weekly scores are persisted per
week (keyed by the week-ending Friday) in data/navellier/history.jsonl; past weeks are back-computed
from prices (source="backfill"), live runs are never overwritten by a later backfill. History is used
for the "recent scores" column only -- it is NOT used to trigger the cooling flag (see below).

Momentum-cooling flag is LEVEL-based (our own rule; the book gives no numeric threshold):
  - score's percentile within the current universe  < REMOVE (default 40%)  -> RECOMMEND REMOVAL
  - score's percentile within the current universe  < WARN   (default 60%)  -> COOLING warning
  - otherwise                                                              -> normal
Thresholds are adjustable (data/user/cooling.json, or the dashboard).

Why level and not "N consecutive weekly declines": a back-test on S&P 500 (2019-2026, ~180k stock-weeks)
showed that streak-of-declines flags fire at exactly the random-walk frequency (24.9% for >=2 weeks vs
25% theoretical; 12.4% for >=3 vs 12.5%), week-to-week score changes have autocorrelation -0.04, and
stocks flagged by streaks or by large 4-week drops did no worse afterwards than the average (the score is
a 52-week rolling window, so one week's change is tiny). The score LEVEL did carry information (top
quintile +1.89% vs bottom quintile -0.30% excess return over the next 13 weeks), but NOT consistently:
the high-minus-low spread was ~0 or negative in 2020-2022 and large in 2023-2026. Caveats: survivorship
bias (current constituents only), overlapping observations, no costs, no significance test.
"""
import json
from datetime import datetime

import pandas as pd

from investor import paths
from investor import universe
from investor.fileio import atomic_write
from investor.navellier import alpha_beta, fundamentals, history, settings

BACKFILL_WEEKS = 8


def cooling_flag(pct, warn, remove):
    """pct: 該股票量化分數在股票池內的分位 (0~1, 1 最好)。"""
    if pct is None:
        return "N/A（本週無量化分數）"
    p = f"{pct * 100:.0f}%"
    if pct < remove:
        return f"❌ 建議剔除（量化分數位於股票池第 {p} 分位，低於 {remove * 100:.0f}%）"
    if pct < warn:
        return f"\U0001f53b 動能冷卻警示（量化分數位於股票池第 {p} 分位，低於 {warn * 100:.0f}%）"
    return f"✅ 正常（第 {p} 分位）"


def run(tickers=None, verbose=False, mode=None):
    """mode: "sp500"(預設) / "ndx"(Nasdaq 100) / "watchlist"(自訂清單); 給 tickers 時直接用該清單。
    評級(五分位)只在這個股票池內相對排名, 所以換股票池要重新計算。"""
    mode = mode or settings.load_mode()
    names, sectors = {}, {}
    if tickers is None:
        if mode in universe.UNIVERSES:
            uni = universe.members(mode)
            tickers, names = list(uni["symbol"]), dict(zip(uni["symbol"], uni["name"]))
            sectors = dict(zip(uni["symbol"], uni["sector"].fillna("")))
        else:
            tickers = settings.load_watchlist()
    ab, missing, wk_hist = alpha_beta.compute(tickers, verbose=verbose, history_weeks=BACKFILL_WEEKS)
    fd = fundamentals.compute(ab, tickers)

    cutoff = str(alpha_beta.complete_week_cutoff())
    recs = history.merge_history(history.load_history(), wk_hist, cutoff)
    history.save_history(recs)

    weeks = sorted(w for w in recs if w <= cutoff)
    series = {t: [recs[w]["scores"][t] for w in weeks if t in recs[w]["scores"]] for t in tickers}

    cool = settings.load_cooling()
    pct = pd.Series({t: ab[t]["nav_score"] for t in tickers if ab.get(t, {}).get("eligible")},
                    dtype=float).rank(pct=True).to_dict()      # 分位只在「資料足夠」的股票之間算

    # 匯入技術面模組以計算提示
    from investor.navellier import technicals as tech_module

    report = {}
    for t in tickers:
        entry = dict(fd["combined_30_70"].get(t, {"overall": "N/A"}))
        a = ab.get(t, {})
        cooling_str = cooling_flag(pct.get(t), cool["warn"], cool["remove"]) if a.get("eligible") else "N/A（資料不足 52 週，不評級）"
        entry.update({
            "beta_5y": a.get("beta_5y"), "alpha_ann_5y_pct": a.get("alpha_ann_5y"),
            "nav_grade": a.get("nav_grade", "N/A"), "eligible": a.get("eligible", False),
            "n_weeks": a.get("n_weeks"), "last_close": a.get("last_close"),
            "nav_pct": pct.get(t),
            "cooling": cooling_str,
        })

        # 加入技術面欄位
        tech_data = fd.get("technicals", {}).get(t, {})
        if tech_data:
            entry.update({
                "rsi": tech_data.get("rsi"),
                "rsi_zone": tech_data.get("rsi_zone"),
                "trend": tech_data.get("trend"),
                "off_high_pct": tech_data.get("off_high_pct"),
                "macd_dir": tech_data.get("macd_dir"),
                "tech_rating": tech_data.get("tech_rating"),
                "next_report": tech_data.get("next_report"),
                "days_to_report": tech_data.get("days_to_report"),
                "report_soon": tech_data.get("report_soon"),
            })

            # 計算提示 (使用 overall 評級與 cooling 字串)
            hints_list = tech_module.hints(tech_data, fund_grade=entry.get("overall"), cooling_flag=cooling_str)
            entry["tech_hints"] = hints_list
        else:
            # 沒有技術面資料時留空
            entry.update({
                "rsi": None, "rsi_zone": None, "trend": None, "off_high_pct": None,
                "macd_dir": None, "tech_rating": None, "next_report": None,
                "days_to_report": None, "report_soon": False, "tech_hints": [],
            })

        report[t] = entry

    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    text = json.dumps(report, indent=2, ensure_ascii=False)
    atomic_write(paths.NAVELLIER / f"report_{today}_{mode}.json", text)
    atomic_write(paths.NAVELLIER / f"latest_report_{mode}.json", text)

    return {"asof": cutoff, "tickers": tickers, "mode": mode, "names": names, "sectors": sectors, "cooling": cool, "report": report, "series": series,
            "fundamentals": fd["fundamentals"], "factor_quintiles": fd["factor_quintiles"],
            "missing": missing, "notes": fd["notes"], "unavailable": fd["unavailable"]}


def main():
    import sys
    r = run(verbose=True, mode=sys.argv[1] if len(sys.argv) > 1 else None)   # python -m investor.navellier.rating [sp500|ndx|watchlist]
    rep = r["report"]
    print(f"\n股票池: {settings.MODES[r['mode']]} ({len(r['tickers'])} 檔) | 資料截至 {r['asof']} 那週收盤 (只計已收完的週)")
    print(f"{'Ticker':7} {'Beta5y':>7} {'Alpha5y%':>9} {'Alpha/SD':>9} {'NavGr':>6} "
          f"{'Fund':>6} {'Overall':>8}  Cooling")
    nan = float("nan")
    g = lambda c, k: nan if c.get(k) is None else c[k]    # 0 是有效值, 不能用 `or`
    for t in r["tickers"]:
        c = rep[t]
        print(f"{t:7} {g(c, 'beta_5y'):7} {g(c, 'alpha_ann_5y_pct'):9} "
              f"{g(c, 'alpha_over_sd'):9} {c.get('nav_grade', '-'):>6} "
              f"{c.get('fund_grade', '-'):>6} {c.get('overall', '-'):>8}  {c.get('cooling')}")
    if r["missing"]:
        print("\nmissing tickers (no price data):", r["missing"])
    print(f"\nhistory: {paths.HISTORY} ({len(history.load_history())} weeks on file)")


if __name__ == "__main__":
    main()