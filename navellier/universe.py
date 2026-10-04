"""股票池: Nasdaq 100 成分股。

來源: Nasdaq 官方網站使用的公開 API (Wikipedia 目前沒有成分股表)。這不是正式文件化的 API,
可能改版; 失敗時退回最近一次成功抓到的本地快取 data_navellier/nasdaq100.csv。
Nasdaq 100 為 100 家公司, 但 Alphabet 有 GOOG/GOOGL 兩種股票, 所以代號約 101 個。
"""
import re
import sys
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

URL = "https://api.nasdaq.com/api/quote/list-type/nasdaq100"
CACHE = Path(__file__).resolve().parent.parent / "data_navellier" / "nasdaq100.csv"
_SUFFIX = re.compile(r"\s+(Common Stock|Class [A-C]\b|Ordinary Shares?|American Depositary|Depositary|Capital Stock).*$", re.I)


def _clean(name):
    return _SUFFIX.sub("", str(name)).strip(" ,")


def get_nasdaq100() -> pd.DataFrame:
    """回傳 DataFrame[symbol, name]; symbol 已把 '.' 換成 '-' (yfinance 格式)。"""
    try:
        r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=30)
        r.raise_for_status()
        rows = r.json()["data"]["data"]["rows"]
        df = pd.DataFrame({"symbol": [x["symbol"].strip().upper().replace(".", "-") for x in rows],
                           "name": [_clean(x["companyName"]) for x in rows]}).drop_duplicates("symbol")
        if not 90 <= len(df) <= 110:
            raise ValueError(f"成分股數量異常: {len(df)}")
        CACHE.parent.mkdir(exist_ok=True)
        df.to_csv(CACHE, index=False)
        return df
    except Exception as e:
        if CACHE.exists():
            print(f"  ⚠ 成分股抓取失敗 ({str(e)[:80]}), 改用本地快取 {CACHE.name}", flush=True)
            return pd.read_csv(CACHE)
        raise RuntimeError(f"抓不到 Nasdaq 100 成分股且沒有本地快取: {e}")


SP500_CACHE = CACHE.parent / "sp500.csv"


def get_sp500() -> pd.DataFrame:
    """S&P 500 成分股 (Wikipedia, 與其他分頁同一來源)。回傳 DataFrame[symbol, name, sector]。失敗時用本地快取。"""
    try:
        from sp500_losers import get_constituents
        c = get_constituents()
        df = pd.DataFrame({"symbol": c["Symbol"], "name": c["Security"], "sector": c["GICS Sector"]}).drop_duplicates("symbol")
        if not 480 <= len(df) <= 520:
            raise ValueError(f"成分股數量異常: {len(df)}")
        SP500_CACHE.parent.mkdir(exist_ok=True)
        df.to_csv(SP500_CACHE, index=False)
        return df
    except Exception as e:
        if SP500_CACHE.exists():
            print(f"  ⚠ S&P 500 成分股抓取失敗 ({str(e)[:80]}), 改用本地快取 {SP500_CACHE.name}", flush=True)
            return pd.read_csv(SP500_CACHE)
        raise RuntimeError(f"抓不到 S&P 500 成分股且沒有本地快取: {e}")
