"""TradingView 公開 screener: 一次抓整個股票池的基本面與預估資料 (無歷史, 只有當下共識)。

利用 tradingview-screener 套件 (https://github.com/tstewart161/tradingview_screener)
查詢 market='america' 的股票, 支持逐季歷史與當下預估, 無需登入與 API 金鑰。

代號對應: 專案用 'BRK-B', TradingView 用 'BRK.B' (- 換成 .)。同名多上市時只留 type='stock', 取市值最大者。
"""
import time
import warnings

try:
    from tradingview_screener import Query, col
except ImportError as e:
    raise ImportError(
        "tradingview-screener 未安裝。請執行:\n"
        "  .venv/bin/pip install tradingview-screener\n"
        "或在 requirements.txt 中加入此套件"
    ) from e

FUND_COLUMNS = [
    "total_revenue_fq_h",           # 逐季營收歷史 (list, 最新在前, 約 12 季)
    "net_income_fq_h",              # 逐季淨利歷史
    "earnings_per_share_diluted_fq_h",  # 逐季稀釋 EPS 歷史
    "free_cash_flow_fq_h",          # 逐季自由現金流歷史
]

ESTIMATE_COLUMNS = [
    "earnings_per_share_forecast_fq",       # 本季 EPS 預估
    "earnings_per_share_forecast_next_fq",  # 下季 EPS 預估
    "earnings_per_share_forecast_next_fy",  # 下會計年度 EPS 預估
    "revenue_forecast_fq",                  # 本季營收預估
    "revenue_forecast_next_fq",             # 下季營收預估
    "price_target_average",                 # 平均目標股價
    "recommendation_mark",                  # 分析師建議 (數值: 1=buy, 5=sell)
    "recommendation_total",                 # 分析師人數
    "earnings_release_next_date",           # 下次財報日 (unix 秒)
    "earnings_release_date",                # 最近一次財報日 (unix 秒)
    "eps_surprise_percent_fq",              # 最新季 EPS 驚喜%
    "revenue_surprise_percent_fq",          # 最新季營收驚喜%
]

TECH_COLUMNS = [
    "close",                                # 最新收盤價
    "RSI",                                  # 相對強弱指數 (0-100)
    "SMA50",                                # 50 日均線
    "SMA200",                               # 200 日均線
    "price_52_week_high",                   # 52 週高價
    "price_52_week_low",                    # 52 週低價
    "Recommend.All",                        # 綜合建議 (-1~1, 負=賣出, 正=買進)
    "MACD.macd",                            # MACD 線
    "MACD.signal",                          # MACD 信號線
    "Perf.1M",                              # 1 個月績效%
    "relative_volume_10d_calc",             # 10 日平均相對成交量
]

# 同業比較用: 逐季歷史 + ROE + 最新季驚喜 + TradingView 產業分類
PEER_COLUMNS = FUND_COLUMNS + [
    "return_on_equity",                 # ROE (TradingView 算好的數字)
    "eps_surprise_percent_fq",          # 最新季 EPS 驚喜%
    "industry",                         # TradingView 產業 (如 Semiconductors、Marine Shipping)
]

MAX_BATCH = 200
RETRY_DELAY = 5
MAX_TRIES = 4


def _normalize_ticker(t: str) -> str:
    """專案代號 (BRK-B) 轉 TradingView 格式 (BRK.B)。"""
    return t.replace("-", ".")


def _denormalize_ticker(t: str) -> str:
    """TradingView 格式 (BRK.B) 轉專案代號 (BRK-B)。"""
    return t.replace(".", "-")


def _retry_fn(fn, tries: int = MAX_TRIES, base_delay: float = RETRY_DELAY):
    """指數退避重試: 5秒、10秒、20秒。"""
    last_exc = None
    for k in range(tries):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if k < tries - 1:
                delay = base_delay * (2 ** k)
                time.sleep(delay)
    raise last_exc


def fetch(tickers: list, columns: list) -> dict:
    """抓取 TradingView 資料。

    Args:
        tickers: 股票代號列表 (使用專案格式, 如 'BRK-B')
        columns: 要抓的欄位列表 (FUND_COLUMNS 或 ESTIMATE_COLUMNS 的子集)

    Returns:
        {專案代號: {欄位: 值}} 字典; NaN/numpy.nan 轉為 None、其他數值保留為 float/int/str

    Raises:
        RuntimeError: 抓回有效筆數太少(疑似被限流)時拋出
    """
    if not tickers:
        return {}

    result = {}

    # 分批查詢 (TradingView 限制每次最多 1000 筆, 但保險起見用 200)
    for batch_start in range(0, len(tickers), MAX_BATCH):
        batch = tickers[batch_start : batch_start + MAX_BATCH]
        tv_tickers = [_normalize_ticker(t) for t in batch]

        def query_batch():
            q = Query().set_markets("america").select(
                "name", "exchange", "type", "market_cap_basic",
                *columns
            ).where(col("name").isin(tv_tickers)).limit(1000)
            count, df = q.get_scanner_data()
            return count, df

        try:
            count, df = _retry_fn(query_batch)
        except Exception as e:
            warnings.warn(f"TradingView 批次查詢失敗 (代號 {batch}): {e}")
            raise RuntimeError(f"TradingView 查詢失敗: {e}")

        if df is None or df.empty:
            continue

        # 過濾: 只留普通股與存託憑證 (ADR: TSM、ARM、BABA、NVO…, type='dr'; 原本漏掉 dr, 這些股票沒有預估與技術面);
        # ETF 不在這個股票掃描器裡, 抓不到。同名代號取市值最大者
        df = df[df["type"].isin(["stock", "dr"])].copy() if "type" in df.columns else df.copy()

        # 同名代號可能有多個上市 (e.g., 同一公司在多個交易所), 取市值最大者
        if "name" in df.columns and "market_cap_basic" in df.columns:
            df["market_cap_basic"] = pd.to_numeric(df["market_cap_basic"], errors="coerce")
            df = df.sort_values("market_cap_basic", ascending=False, na_position="last")
            df = df.drop_duplicates(subset=["name"], keep="first")

        # 轉換格式: TradingView 代號轉回專案格式
        for idx, row in df.iterrows():
            tv_name = row.get("name", "")
            proj_ticker = _denormalize_ticker(tv_name)

            # 只保留原本查詢的代號 (避免抓到無關的同名公司)
            if proj_ticker not in batch:
                continue

            entry = {}
            for col_name in columns:
                v = row.get(col_name)
                # 轉換 NaN / numpy.nan 為 None; 其他數值保留
                if v is not None and isinstance(v, float):
                    if pd.isna(v):
                        entry[col_name] = None
                    else:
                        entry[col_name] = v
                elif v is None or (isinstance(v, float) and pd.isna(v)):
                    entry[col_name] = None
                else:
                    entry[col_name] = v

            result[proj_ticker] = entry

    # 檢查抓回的有效筆數
    valid = len(result)
    total = len(tickers)
    if valid < total * 0.6:  # 比照 yahoo.require_enough 的 60% 門檻
        raise RuntimeError(
            f"TradingView: 只抓到 {valid}/{total} 筆有效資料，疑似被限流。"
        )

    return result


def fetch_industry(industries: list, min_cap: float = 2e9) -> dict:
    """抓整個美股市場「指定產業」內、市值 >= min_cap 的股票 (同業比較基準): {專案代號: {PEER_COLUMNS..., market_cap_basic}}。
    普通股與 ADR 都算; 同名多上市取市值最大者。"""
    if not industries:
        return {}

    def query():
        return (Query().set_markets("america")
                .select("name", "type", "market_cap_basic", *PEER_COLUMNS)
                .where(col("industry").isin(list(industries)), col("market_cap_basic") >= min_cap,
                       col("type").isin(["stock", "dr"]))
                .limit(5000).get_scanner_data())
    try:
        _, df = _retry_fn(query)
    except Exception as e:
        raise RuntimeError(f"TradingView 同業查詢失敗: {e}")
    if df is None or df.empty:
        raise RuntimeError("TradingView 同業查詢沒有回傳任何資料 (疑似被限流)")
    df = df[~df["name"].str.contains("/", regex=False)]            # 優先股 (RNR/PF、HIG/PG…) 不是同業, 會污染比較
    df = df.sort_values("market_cap_basic", ascending=False, na_position="last").drop_duplicates(subset=["name"], keep="first")
    out = {}
    for _, row in df.iterrows():
        entry = {}
        for c in PEER_COLUMNS + ["market_cap_basic"]:
            v = row.get(c)
            entry[c] = None if (v is None or (isinstance(v, float) and pd.isna(v))) else v
        out[_denormalize_ticker(row["name"])] = entry
    return out


def fetch_market(min_cap: float = 2e9) -> dict:
    """全市場名單: NYSE/NASDAQ/AMEX 上市、市值 >= min_cap 的普通股與 ADR (不含優先股、場外交易)。
    回傳 {專案代號: {description, industry, sector, market_cap_basic}}。"""
    cols = ["description", "industry", "sector", "market_cap_basic"]

    def query():
        return (Query().set_markets("america").select("name", "type", "exchange", *cols)
                .where(col("market_cap_basic") >= min_cap, col("type").isin(["stock", "dr"]),
                       col("exchange").isin(["NYSE", "NASDAQ", "AMEX"]))
                .limit(8000).get_scanner_data())
    try:
        _, df = _retry_fn(query)
    except Exception as e:
        raise RuntimeError(f"TradingView 全市場名單查詢失敗: {e}")
    if df is None or len(df) < 500:
        raise RuntimeError(f"TradingView 全市場名單只有 {0 if df is None else len(df)} 檔, 疑似被限流")
    df = df[~df["name"].str.contains("/", regex=False)]
    df = df.sort_values("market_cap_basic", ascending=False, na_position="last").drop_duplicates(subset=["name"], keep="first")
    return {_denormalize_ticker(r["name"]): {c: (None if (isinstance(r[c], float) and pd.isna(r[c])) else r[c]) for c in cols}
            for _, r in df.iterrows()}


def fetch_peer_targets(tickers: list) -> dict:
    """抓指定股票自己的同業比較欄位 (含 industry、市值); 不在 TradingView 的 (如 ETF) 不會出現在結果裡。抓取失敗會丟 RuntimeError。"""
    return fetch(tickers, PEER_COLUMNS)


def fetch_fundamentals(tickers: list) -> dict:
    """抓取基本面資料 (逐季歷史)。"""
    return fetch(tickers, FUND_COLUMNS)


def fetch_estimates(tickers: list) -> dict:
    """抓取分析師預估資料 (當下共識, 無歷史) + 技術面資料。
    一次請求同時抓預估與技術面, 減少網路往返。"""
    return fetch(tickers, ESTIMATE_COLUMNS + TECH_COLUMNS)


def fetch_technicals(tickers: list) -> dict:
    """抓取技術面資料 (日線, 當下值)。通常與 fetch_estimates 共用, 此函式供單獨使用。"""
    return fetch(tickers, TECH_COLUMNS)


# 為了避免 import 時的循環依賴, pandas 延遲 import
import pandas as pd
