"""股票池 (成分股名單): S&P 500 與 Nasdaq 100。全專案唯一的成分股來源。

- S&P 500: Wikipedia。
- Nasdaq 100: Nasdaq 官方網站使用的公開 API (Wikipedia 目前沒有成分股表)。這不是正式文件化的 API, 可能改版;
  Nasdaq 100 為 100 家公司, 但 Alphabet 有 GOOG/GOOGL 兩種股票, 所以代號約 101 個。
  Nasdaq 官方資料沒有板塊, 借用 S&P 500 的 GICS 分類; 不在 S&P 500 內的少數幾檔板塊留空。
- 抓取失敗時退回最近一次成功抓到的本地快取 (data/cache/universe_*.csv); 筆數明顯異常也視為失敗。

標準欄位: symbol (yfinance 格式, '.' 換成 '-'), name, sector。
"""
import io
import re

import pandas as pd
import requests

from investor import paths

UNIVERSES = {"sp500": "S&P 500", "ndx": "Nasdaq 100"}

_WIKI = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_NASDAQ = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
_SUFFIX = re.compile(r"\s+(Common Stock|Class [A-C]\b|Ordinary Shares?|American Depositary|Depositary|Capital Stock).*$", re.I)


def _with_fallback(name: str, fetch, lo: int, hi: int) -> pd.DataFrame:
    cache = paths.universe_cache(name)
    try:
        df = fetch()
        if not lo <= len(df) <= hi:
            raise ValueError(f"成分股數量異常: {len(df)}")
        paths.ensure_parent(cache)
        df.to_csv(cache, index=False)
        return df
    except Exception as e:
        if cache.exists():
            print(f"  ⚠ {UNIVERSES[name]} 成分股抓取失敗 ({str(e)[:80]}), 改用本地快取 {cache.name}", flush=True)
            return pd.read_csv(cache)
        raise RuntimeError(f"抓不到 {UNIVERSES[name]} 成分股且沒有本地快取: {e}")


def _fetch_sp500() -> pd.DataFrame:
    html = requests.get(_WIKI, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    c = pd.read_html(io.StringIO(html))[0]
    return pd.DataFrame({"symbol": c["Symbol"].str.replace(".", "-", regex=False),     # BRK.B -> BRK-B
                         "name": c["Security"], "sector": c["GICS Sector"]}).drop_duplicates("symbol")


def _fetch_nasdaq100() -> pd.DataFrame:
    r = requests.get(_NASDAQ, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    rows = r.json()["data"]["data"]["rows"]
    return pd.DataFrame({"symbol": [x["symbol"].strip().upper().replace(".", "-") for x in rows],
                         "name": [_SUFFIX.sub("", str(x["companyName"])).strip(" ,") for x in rows]}).drop_duplicates("symbol")


def sp500() -> pd.DataFrame:
    return _with_fallback("sp500", _fetch_sp500, 480, 520)


def nasdaq100() -> pd.DataFrame:
    df = _with_fallback("ndx", _fetch_nasdaq100, 90, 110)[["symbol", "name"]].copy()
    sector = dict(zip(*sp500()[["symbol", "sector"]].T.values))
    df["sector"] = df["symbol"].map(sector).fillna("")
    return df


def members(name: str) -> pd.DataFrame:
    """依股票池代碼 ('sp500' / 'ndx') 回傳標準欄位 (symbol, name, sector)。"""
    if name == "sp500":
        return sp500()
    if name == "ndx":
        return nasdaq100()
    raise ValueError(f"未知的股票池: {name!r}")


def for_screens(name: str) -> pd.DataFrame:
    """選股功能(screens)沿用的欄位名稱: Symbol / Security / GICS Sector。"""
    m = members(name)
    return pd.DataFrame({"Symbol": m["symbol"], "Security": m["name"], "GICS Sector": m["sector"]})
