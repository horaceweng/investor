"""全市場動能榜: 掃描美股 (NYSE/NASDAQ/AMEX, 市值 >= 20 億美元, 普通股與 ADR), 依產業分段, 找出「持續強勢」的股票。

依據 (2019-10 ~ 2026-10 週資料回測, 研究紀錄見專案備忘; 全部 point-in-time):
- 「強者恆強」在右尾成立: 52 週 / 26 週 Alpha/SD 在全市場前 10% 的股票, 之後 26 週翻倍的比例約為全體的 2~2.5 倍,
  只看市值 >= 100 億 (生存者偏差最小) 結論相同。
- 要翻倍必須有波動 (年化 >= 45%): 這是機械效應, 不是方向訊號; 但在高波動股票裡, 持續強勢者翻倍比例仍高約 1.8 倍,
  跌 30% 以上的比例沒有更高。
- 「持續強勢」(半年 Alpha/SD 前 20%、半年內 >= 60% 的週贏 SPY、站上且上彎的 40 週線、離 52 週高 < 10%) 加上
  「產業順風」(產業半年報酬中位數在前 30%) 是風險報酬最好的組合。
- 只有短期 (13 週) 強、年度還沒強的「太早期」訊號反而較差; 大盤在 40 週線下時所有訊號幾乎失效。
- 機率仍然低 (翻倍約 5~7%), 跌 30% 以上約 11~15%: 這是觀察清單的來源, 不是買進訊號。

指標全部用「已收完的週」(alpha_beta.complete_week_cutoff) 的週收盤計算, 同一週內重算結果相同。
"""
import time
import warnings

import numpy as np
import pandas as pd
import yfinance as yf

from investor import paths
from investor.data_sources import tradingview
from investor.data_sources.yahoo import retry
from investor.fileio import atomic_write
from investor.navellier import alpha_beta

warnings.filterwarnings("ignore")

MIN_CAP = 2e9          # 市值下限 (美元)
MIN_PRICE = 5          # 股價下限
MIN_WEEKS = 53         # 至少 53 週週收盤 (52 週報酬與 Alpha/SD 需要)
MIN_INDUSTRY = 8       # 產業至少這麼多檔才算產業動能
CHUNK = 150            # 價格分批下載
BENCH = "SPY"


# ───────────── 名單與價格 ─────────────
def universe(min_cap=MIN_CAP):
    """全市場名單: DataFrame(index=代號, columns=[name, industry, sector, cap])。"""
    rows = tradingview.fetch_market(min_cap)
    df = pd.DataFrame.from_dict(rows, orient="index")
    return df.rename(columns={"description": "name", "market_cap_basic": "cap"})


def weekly_prices(tickers, cutoff, path=None):
    """週收盤 (週五) 價格表, 只含 cutoff (最近一個已收完的週五) 以前的週。依 cutoff 存檔, 同一週內不重抓;
    分批下載、每批存檔, 被 Yahoo 限流中斷後再跑會接續。"""
    path = path or paths.MARKET_PRICES
    cut = pd.Timestamp(cutoff)
    cache = None
    try:
        cache = pd.read_pickle(path)
    except (OSError, ValueError, EOFError, ModuleNotFoundError, AttributeError):
        cache = None
    if cache is None or cache.attrs.get("cutoff") != str(cutoff):
        cache = pd.DataFrame()
        cache.attrs["cutoff"] = str(cutoff)
    want = list(dict.fromkeys(list(tickers) + [BENCH]))
    todo = [t for t in want if t not in cache.columns]
    for i in range(0, len(todo), CHUNK):
        part = todo[i:i + CHUNK]
        d = retry(lambda: yf.download(part, period="2y", interval="1d", auto_adjust=True, progress=False, threads=True))
        if d is None or d.empty:
            raise RuntimeError("價格下載為空 (可能被 Yahoo 限流), 已完成的部分已存檔, 稍後再按一次會接續")
        close = d["Close"] if isinstance(d.columns, pd.MultiIndex) else d[["Close"]].rename(columns={"Close": part[0]})
        wk = close.resample("W-FRI").last()
        wk = wk[wk.index <= cut]
        for t in part:                                   # 抓不到的也記一欄 (全空), 避免每次重抓
            if t not in wk.columns:
                wk[t] = np.nan
        attrs = dict(cache.attrs)
        cache = pd.concat([cache, wk[part]], axis=1)
        cache.attrs.update(attrs)
        _save_prices(cache, path)
        time.sleep(0.5)
    return cache[[t for t in want if t in cache.columns]]


def _save_prices(df, path):
    paths.ensure_parent(path)
    tmp = path.with_suffix(".tmp")
    df.to_pickle(tmp)
    tmp.replace(path)


# ───────────── 指標 (純計算) ─────────────
def indicators(px: pd.DataFrame, bench: pd.Series) -> pd.DataFrame:
    """最後一週的各項指標 (每檔一列)。px: 週收盤 (列=週, 欄=代號), bench: SPY 週收盤。
    A13/A26/A52: 週超額報酬 (相對 SPY) 的平均 / 標準差 x sqrt(52) (年化, 即 Navellier 的 reward/risk);
    UP26: 近 26 週贏 SPY 的週數比例; 40 週線上彎: 現在的 40 週均線高於 4 週前。"""
    wk = px.pct_change(fill_method=None)
    ex = wk.sub(bench.pct_change(fill_method=None), axis=0)
    last = px.iloc[-1]
    out = pd.DataFrame(index=px.columns)
    n = px.notna().sum()
    for k in (13, 26, 52):
        w = ex.iloc[-k:]
        sd = w.std()
        out[f"A{k}"] = (w.mean() / sd * np.sqrt(52)).where((w.notna().sum() >= k) & (sd > 0))
    out["UP26"] = (ex.iloc[-26:] > 0).sum() / ex.iloc[-26:].notna().sum()
    for k in (4, 13, 26, 52):
        out[f"R{k}"] = last / px.iloc[-1 - k] - 1
    ma10, ma40 = px.iloc[-10:].mean(), px.iloc[-40:].mean()
    ma40_prev = px.iloc[-44:-4].mean()
    out["ABOVE10"] = last > ma10
    out["ABOVE40"] = last > ma40
    out["MA40UP"] = ma40 > ma40_prev
    out["HI52"] = last / px.iloc[-52:].max() - 1
    roll26 = px.rolling(26, min_periods=26).max()
    out["NEWHI"] = (px.iloc[-4:] >= roll26.iloc[-4:]).any()
    out["VOL"] = wk.iloc[-26:].std() * np.sqrt(52)
    out["PRICE"] = last
    out["NWEEKS"] = n
    return out


def score(ind: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    """全市場百分位、產業動能與標記。ind: indicators(); meta: universe() (industry, cap...)。只評「可比較」的股票。"""
    df = ind.join(meta[["name", "industry", "sector", "cap"]], how="inner")
    df = df[(df["cap"] >= MIN_CAP) & (df["PRICE"] >= MIN_PRICE) & (df["NWEEKS"] >= MIN_WEEKS) & df["A52"].notna()].copy()
    for k in ("A13", "A26", "A52", "R26"):
        df["p" + k] = df[k].rank(pct=True)
    # 產業動能: 產業半年報酬中位數, 在所有產業 (>= MIN_INDUSTRY 檔) 中的百分位
    g = df.groupby("industry")
    size = g["R26"].transform("size")
    med = g["R26"].median()
    big = med[g.size() >= MIN_INDUSTRY]
    df["IND_MOM"] = df["industry"].map(big.rank(pct=True)).where(size >= MIN_INDUSTRY)
    df["P"] = (df.pA26 >= .8) & (df.UP26 >= .6) & df.ABOVE40 & df.MA40UP & (df.HI52 >= -.1)
    df["TAIL"] = df.IND_MOM >= .7
    df["HV"] = df.VOL >= .45
    df["BOTH"] = (df.pA26 >= .9) & (df.pA52 >= .9)
    df["EARLY"] = (df.pA13 >= .9) & (df.pA26 < .7) & df.ABOVE40 & df.MA40UP
    df["CAND"] = df.P | (df.pA26 >= .9) | ((df.pA13 >= .9) & df.ABOVE40 & df.MA40UP)
    return df


def tags(r) -> str:
    t = []
    if r.P:
        t.append("持續強勢")
    if r.BOTH:
        t.append("年與半年都強")
    if r.TAIL:
        t.append("產業順風")
    if r.HV:
        t.append("高波動")
    if r.EARLY:
        t.append("只有短期強")
    return "、".join(t)


def industries(df: pd.DataFrame) -> pd.DataFrame:
    """產業動能表 (>= MIN_INDUSTRY 檔的產業), 依半年報酬中位數由高到低。"""
    g = df.groupby("industry")
    t = pd.DataFrame({"檔數": g.size(), "半年報酬中位%": g["R26"].median() * 100, "季報酬中位%": g["R13"].median() * 100,
                      "站上40週線%": g["ABOVE40"].mean() * 100, "持續強勢檔數": g["P"].sum(), "候選檔數": g["CAND"].sum()})
    t = t[t["檔數"] >= MIN_INDUSTRY].sort_values("半年報酬中位%", ascending=False)
    t["產業動能百分位"] = t["半年報酬中位%"].rank(pct=True) * 100
    return t.reset_index().rename(columns={"industry": "產業"})


def regime(bench: pd.Series) -> dict:
    """大盤環境: SPY 是否在 40 週線上 (回測中, 大盤在 40 週線下時各訊號幾乎失效)。"""
    ma40 = bench.iloc[-40:].mean()
    return {"spy": float(bench.iloc[-1]), "ma40": float(ma40), "above": bool(bench.iloc[-1] > ma40),
            "dist": float(bench.iloc[-1] / ma40 - 1)}


def rows(df: pd.DataFrame) -> pd.DataFrame:
    """候選股的顯示列 (依產業動能、再依 A26 排序)。"""
    c = df[df.CAND].copy()
    out = pd.DataFrame({
        "代號": c.index, "公司": c["name"].values, "公司_tip": c["name"].values, "產業": c["industry"].values,
        "標記": [tags(r) for r in c.itertuples()],
        "A13分位": c.pA13.values * 100, "A26分位": c.pA26.values * 100, "A52分位": c.pA52.values * 100,
        "贏SPY週%": c.UP26.values * 100, "13週%": c.R13.values * 100, "26週%": c.R26.values * 100,
        "離52週高%": c.HI52.values * 100, "波動%": c.VOL.values * 100,
        "40週線": ["站上・上彎" if a and u else "站上" if a else "跌破" for a, u in zip(c.ABOVE40, c.MA40UP)],
        "市值億": c["cap"].values / 1e8, "產業動能": c["IND_MOM"].values * 100,
    })
    return out.sort_values(["產業動能", "A26分位"], ascending=[False, False], na_position="last").reset_index(drop=True)


# ───────────── 主流程 ─────────────
def compute():
    """抓名單與價格 (同一週內用快取), 算指標。回傳顯示所需的全部結果, 並把本週候選存進歷史 (供日後驗證)。"""
    cutoff = alpha_beta.complete_week_cutoff()
    meta = universe()
    px = weekly_prices(list(meta.index), cutoff)
    if BENCH not in px.columns or px[BENCH].dropna().empty:
        raise RuntimeError("抓不到 SPY 價格")
    bench = px[BENCH].dropna()
    px = px.drop(columns=[BENCH]).loc[bench.index]
    usable = px.notna().sum() >= MIN_WEEKS
    if usable.sum() < len(meta) * 0.6:
        raise RuntimeError(f"全市場動能榜: 只有 {int(usable.sum())}/{len(meta)} 檔有足夠價格資料, 疑似被 Yahoo 限流; 未更新")
    df = score(indicators(px.loc[:, usable], bench), meta)
    _append_history(cutoff, df)
    return {"asof": str(cutoff), "regime": regime(bench), "industries": industries(df), "rows": rows(df),
            "n_universe": int(len(df)), "n_cand": int(df.CAND.sum()), "n_p": int(df.P.sum())}


def _append_history(cutoff, df):
    """每週候選與其指標存一行 (同一週覆蓋), 之後可回頭驗證這些標記的實際表現。"""
    import json
    rec = {"week": str(cutoff), "stocks": {t: {"A13": round(float(r.pA13), 3), "A26": round(float(r.pA26), 3),
                                               "A52": round(float(r.pA52), 3), "P": bool(r.P), "TAIL": bool(r.TAIL),
                                               "price": round(float(r.PRICE), 2), "ind": r.industry}
                                           for t, r in df[df.CAND].iterrows()}}
    lines = []
    if paths.MOMENTUM_HISTORY.exists():
        lines = [ln for ln in paths.MOMENTUM_HISTORY.read_text().splitlines()
                 if ln.strip() and json.loads(ln).get("week") != str(cutoff)]
    lines.append(json.dumps(rec, ensure_ascii=False))
    atomic_write(paths.MOMENTUM_HISTORY, "\n".join(lines) + "\n")
