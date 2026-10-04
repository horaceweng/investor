"""Weekly Navellier-style watchlist run: alpha/beta + fundamentals + momentum-cooling watch.

Quant score = Alpha/SD over the last 52 CLOSED weeks (see alpha_beta). Weekly scores are persisted per
week (keyed by the week-ending Friday) in data_navellier/history.jsonl; past weeks are back-computed
from prices (source="backfill"), live runs are never overwritten by a later backfill. History is used
for the "recent scores" column only -- it is NOT used to trigger the cooling flag (see below).

Momentum-cooling flag is LEVEL-based (our own rule; the book gives no numeric threshold):
  - score's percentile within the current universe  < REMOVE (default 40%)  -> RECOMMEND REMOVAL
  - score's percentile within the current universe  < WARN   (default 60%)  -> COOLING warning
  - otherwise                                                              -> normal
Thresholds are adjustable (data_navellier/cooling.json, or the dashboard).

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
import os
import re

import pandas as pd
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from . import alpha_beta, fundamentals, universe
except ImportError:  # 直接 python navellier/run_weekly.py 執行
    import alpha_beta
    import fundamentals
    import universe

DATA_DIR = Path(__file__).resolve().parent.parent / "data_navellier"
HISTORY_FILE = DATA_DIR / "history.jsonl"
WATCHLIST_FILE = DATA_DIR / "watchlist.txt"
MODE_FILE = DATA_DIR / "universe.txt"
COOLING_FILE = DATA_DIR / "cooling.json"
DEFAULT_COOLING = {"warn": 0.6, "remove": 0.4}
MODES = {"watchlist": "自訂觀察清單", "sp500": "S&P 500", "ndx": "Nasdaq 100"}   # 順序即頁首按鈕順序
BACKFILL_WEEKS = 8
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


# ───────────── 觀察清單 ─────────────
def parse_tickers(text):
    """以逗號/空白/換行分隔, 去 # 註解, 轉大寫, 去重; 格式不合者丟 ValueError。"""
    out = []
    for line in text.splitlines():
        for tok in re.split(r"[,\s]+", line.split("#")[0].strip()):
            if not tok:
                continue
            tok = tok.upper().replace(".", "-")
            if not TICKER_RE.match(tok):
                raise ValueError(f"不合法的代號: {tok!r}")
            if tok not in out:
                out.append(tok)
    return out


def load_watchlist():
    if WATCHLIST_FILE.exists():
        t = parse_tickers(WATCHLIST_FILE.read_text())
        if t:
            return t
    return list(alpha_beta.TICKERS)


def save_watchlist(tickers):
    if not 1 <= len(tickers) <= 60:
        raise ValueError("觀察清單需有 1 到 60 檔")
    DATA_DIR.mkdir(exist_ok=True)
    WATCHLIST_FILE.write_text("\n".join(tickers) + "\n")


def load_mode():
    try:
        m = MODE_FILE.read_text().strip()
        return m if m in MODES else "sp500"
    except OSError:
        return "sp500"


def save_mode(mode):
    if mode not in MODES:
        raise ValueError(f"未知的股票池: {mode!r}")
    DATA_DIR.mkdir(exist_ok=True)
    MODE_FILE.write_text(mode + "\n")


# ───────────── 歷史 (每週一筆) ─────────────
def week_end(d):
    """含 d 當天或其後的第一個週五 (舊格式以執行日為 key, 其資料屬於該週)。"""
    dt = date.fromisoformat(d)
    return str(dt + timedelta(days=(4 - dt.weekday()) % 7))


def load_history():
    """回傳 {週五日期: {"scores": {...}, "source": "live"|"backfill"|"legacy"}}"""
    recs = {}
    if HISTORY_FILE.exists():
        for line in HISTORY_FILE.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            wk = week_end(r["date"]) if r.get("source") is None else r["date"]
            recs[wk] = {"scores": r["scores"], "source": r.get("source", "legacy")}
    return recs


def save_history(recs):
    DATA_DIR.mkdir(exist_ok=True)
    lines = [json.dumps({"date": d, "scores": recs[d]["scores"], "source": recs[d]["source"]})
             for d in sorted(recs)]
    tmp = HISTORY_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, HISTORY_FILE)


def merge_history(recs, wk_hist, cutoff):
    """live 紀錄保留; 其餘(legacy/backfill)以回算值覆蓋; 本週(cutoff)一律寫成 live。"""
    for wk, scores in wk_hist.items():
        old = recs.get(wk)
        if wk == cutoff:
            recs[wk] = {"scores": {**(old["scores"] if old and old["source"] == "live" else {}), **scores},
                        "source": "live"}
        elif old and old["source"] == "live":
            recs[wk] = {"scores": {**scores, **old["scores"]}, "source": "live"}
        else:
            recs[wk] = {"scores": scores, "source": "backfill"}
    return recs


def load_cooling():
    try:
        c = json.loads(COOLING_FILE.read_text())
        w, r = float(c["warn"]), float(c["remove"])
        if 0 < r < w < 1:
            return {"warn": w, "remove": r}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return dict(DEFAULT_COOLING)


def save_cooling(warn, remove):
    """warn / remove 為 0~1 的分位門檻, 需 0 < remove < warn < 1。"""
    warn, remove = float(warn), float(remove)
    if not 0 < remove < warn < 1:
        raise ValueError("門檻需滿足 0 < 剔除 < 警示 < 100 (%)")
    DATA_DIR.mkdir(exist_ok=True)
    COOLING_FILE.write_text(json.dumps({"warn": warn, "remove": remove}))


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


# ───────────── 主流程 ─────────────
def run(tickers=None, verbose=False, mode=None):
    """mode: "sp500"(預設) / "ndx"(Nasdaq 100) / "watchlist"(自訂清單); 給 tickers 時直接用該清單。
    評級(五分位)只在這個股票池內相對排名, 所以換股票池要重新計算。"""
    mode = mode or load_mode()
    names, sectors = {}, {}
    if tickers is None:
        if mode in ("sp500", "ndx"):
            uni = universe.get_sp500() if mode == "sp500" else universe.get_nasdaq100()
            tickers, names = list(uni["symbol"]), dict(zip(uni["symbol"], uni["name"]))
            if "sector" in uni:
                sectors = dict(zip(uni["symbol"], uni["sector"].fillna("")))
        else:
            tickers = load_watchlist()
    ab, missing, wk_hist = alpha_beta.compute(tickers, verbose=verbose, history_weeks=BACKFILL_WEEKS)
    fd = fundamentals.compute(ab, tickers)

    cutoff = str(alpha_beta.complete_week_cutoff())
    recs = merge_history(load_history(), wk_hist, cutoff)
    save_history(recs)

    weeks = sorted(w for w in recs if w <= cutoff)
    series = {t: [recs[w]["scores"][t] for w in weeks if t in recs[w]["scores"]] for t in tickers}

    cool = load_cooling()
    pct = pd.Series({t: ab[t]["nav_score"] for t in tickers if ab.get(t, {}).get("eligible")},
                    dtype=float).rank(pct=True).to_dict()      # 分位只在「資料足夠」的股票之間算
    report = {}
    for t in tickers:
        entry = dict(fd["combined_30_70"].get(t, {"overall": "N/A"}))
        a = ab.get(t, {})
        entry.update({
            "beta_5y": a.get("beta_5y"), "alpha_ann_5y_pct": a.get("alpha_ann_5y"),
            "nav_grade": a.get("nav_grade", "N/A"), "eligible": a.get("eligible", False),
            "n_weeks": a.get("n_weeks"), "last_close": a.get("last_close"),
            "nav_pct": pct.get(t),
            "cooling": cooling_flag(pct.get(t), cool["warn"], cool["remove"]) if a.get("eligible") else "N/A（資料不足 52 週，不評級）",
        })
        report[t] = entry

    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    DATA_DIR.mkdir(exist_ok=True)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    (DATA_DIR / f"report_{today}_{mode}.json").write_text(text)
    (DATA_DIR / f"latest_report_{mode}.json").write_text(text)

    return {"asof": cutoff, "tickers": tickers, "mode": mode, "names": names, "sectors": sectors, "cooling": cool, "report": report, "series": series,
            "fundamentals": fd["fundamentals"], "factor_quintiles": fd["factor_quintiles"],
            "missing": missing, "notes": fd["notes"], "unavailable": fd["unavailable"]}


def main():
    import sys
    r = run(verbose=True, mode=sys.argv[1] if len(sys.argv) > 1 else None)   # python -m navellier.run_weekly [sp500|ndx|watchlist]
    rep = r["report"]
    print(f"\n股票池: {MODES[r['mode']]} ({len(r['tickers'])} 檔) | 資料截至 {r['asof']} 那週收盤 (只計已收完的週)")
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
    print(f"\nhistory: {HISTORY_FILE} ({len(load_history())} weeks on file)")


if __name__ == "__main__":
    main()
