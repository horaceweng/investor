"""更新步驟: 把各功能的計算結果寫進 store.state, 並定義「工作」(按鈕) 由哪些步驟組成。

step 函式簽名統一為 (cached, universe): cached=True 時盡量用快取 (首次建立資料); universe 是頁首選的股票池
('sp500' / 'ndx' / 'watchlist'); 不依股票池的功能 (大師買進、Navellier 自己讀設定) 會忽略它。
"""
from investor.data_sources.prices import performance
from investor.navellier import rating, settings
from investor.screens import losers, magic, value
from investor.superinvestors import buys
from investor.web import presenters, store

QUARTER = None   # 13F 季底日期; None = 自動偵測最新一季 (啟動時可用 --quarter 指定)


def do_losers(cached, uni):
    df, base, last = losers.compute(13, 40, uni)
    store.state.setdefault("losers", {})[uni] = {"df": df, "base": base, "last": last, "ts": store.now()}


def do_value(cached, uni):
    r = value.compute(40, cached, uni)
    store.state.setdefault("value", {})[uni] = {**r, "ts": store.now()}


def do_buys(cached, uni):
    df, n_mgr, quarters = buys.compute(QUARTER, 20, 4)
    store.state["buys"] = {"df": df, "n_mgr": n_mgr, "quarters": quarters, "quarter": quarters[-1], "ts": store.now()}


def do_magic(cached, uni):
    res, counts = magic.compute(30, cached, uni)
    store.state.setdefault("magic", {})[uni] = {"res": res, "counts": counts, "ts": store.now()}


def do_nav(cached, uni=None):
    mode = settings.load_mode()
    r = rating.run(verbose=False, mode=mode)
    store.state.setdefault("nav", {})[mode] = {
        "rows": presenters.navellier_rows(r, set(settings.load_watchlist())),
        "factors": presenters.navellier_factors(r), "asof": r["asof"],
        "missing": r["missing"], "tickers": r["tickers"], "ts": store.now()}


def do_perf(cached, uni=None):
    """所有已存結果中的股票, 一次算 1M/3M/6M/1Y 股價表現。"""
    frames = []
    for v in (store.state.get("losers") or {}).values(): frames.append(v["df"])
    for v in (store.state.get("value") or {}).values(): frames += [v[k] for k in ("pe", "pb", "yield")]
    if "buys" in store.state: frames.append(store.state["buys"]["df"])
    for v in (store.state.get("magic") or {}).values(): frames.append(v["res"])
    for v in (store.state.get("nav") or {}).values(): frames.append(v["rows"])
    tickers = [t for f in frames for t in f["代號"]]
    store.state["perf"] = {"data": performance(tickers), "ts": store.now()}


POOL_SCREENS = {"losers", "value", "magic"}   # 依股票池計算的選股功能 (自訂觀察清單不適用)
STEPS = {"losers": ("13 週跌幅", do_losers), "value": ("價值面基本面", do_value),
         "buys": ("大師 13F", do_buys), "magic": ("神奇公式財報", do_magic),
         "nav": ("Navellier 評級", do_nav)}
TASKS = {                                     # 工作(按鈕) -> (顯示名稱, 步驟清單)
    "losers": ("更新股價與跌幅", ["losers"]),
    "value": ("更新基本面", ["value"]),
    "buys": ("更新 13F", ["buys"]),
    "magic": ("更新神奇公式財報", ["magic"]),
    "nav": ("更新 Navellier 評級", ["nav"]),
    "all": ("全部更新", ["losers", "value", "buys", "magic", "nav"]),
    "init": ("首次建立資料 (使用快取)", ["losers", "value", "buys", "magic", "nav"]),
}


def applicable(task: str, mode: str) -> bool:
    """這個工作在目前的股票池下有沒有意義: 只含「依股票池的選股」的工作, 在自訂觀察清單下不適用。"""
    return not (mode == "watchlist" and all(m in POOL_SCREENS for m in TASKS[task][1]))
