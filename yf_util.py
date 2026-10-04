"""yfinance 限流處理: 遇到 YFRateLimitError 時指數退避重試。"""
import time

from yfinance.exceptions import YFRateLimitError


def retry(fn, tries: int = 4, base: float = 5.0):
    for k in range(tries):
        try:
            return fn()
        except YFRateLimitError:
            if k == tries - 1:
                raise
            time.sleep(base * 2 ** k)


def require_enough(valid: int, total: int, what: str, cache: str, min_ratio: float = 0.6):
    """抓回資料太少(多半是被 Yahoo 限流)時中止, 避免覆蓋既有快取。"""
    if valid < total * min_ratio:
        raise RuntimeError(f"{what}: 只抓到 {valid}/{total} 筆有效資料, 疑似被 Yahoo 限流; "
                           f"未覆蓋快取 {cache}。請稍後重試, 或加 --cached 使用快取。")
