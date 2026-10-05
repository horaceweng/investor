"""OpenFIGI: 把 CUSIP 對應成股票代號。"""
import time

import requests

URL = "https://api.openfigi.com/v3/mapping"
EQUITY_TYPES = ("Common Stock", "REIT", "ADR", "Depositary Receipt")


def cusip_to_ticker(cusips) -> dict:
    """{cusip: ticker}; 先限定美國交易所, 查不到的再不限交易所重試; 仍查不到為空字串。"""
    tick = {}
    for exch in ("US", None):
        todo = [c for c in cusips if not tick.get(c)]
        for i in range(0, len(todo), 10):            # 未登入的 API 每次最多 10 筆、每分鐘 25 次
            chunk = todo[i:i + 10]
            jobs = [{"idType": "ID_CUSIP", "idValue": c, **({"exchCode": exch} if exch else {})} for c in chunk]
            r = requests.post(URL, json=jobs, timeout=30)
            for c, res in zip(chunk, r.json() if r.ok else []):
                d = [x for x in res.get("data", []) if x.get("exchCode") == "US"] or res.get("data", [])
                eq = [x for x in d if x.get("securityType") in EQUITY_TYPES]
                tick[c] = (eq or d or [{}])[0].get("ticker", "")
            time.sleep(2.5)
    return tick
