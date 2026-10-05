"""預估快照與修正計算: 每次執行存一份預估共識快照, 比較前後快照算修正%。

快照結構 (data/navellier/estimates.jsonl, 每行一個 JSON):
{
  "date": "YYYY-MM-DD",
  "data": {
    "TICKER": {
      "eps_fq": float,         # 本季 EPS 預估
      "eps_next_fq": float,    # 下季 EPS 預估
      "eps_next_fy": float,    # 下會計年度 EPS 預估
      "rev_fq": float,         # 本季營收預估
      "rev_next_fq": float,    # 下季營收預估
      "target": float,         # 平均目標股價
      "rec": float,            # 建議評級 (1=buy, 5=sell)
      "rec_n": int,            # 分析師人數
      "next_report": int       # 下次財報日 (unix 秒)
    }
  }
}

修正計算:
- 比較「最新快照」與「距今約 28 天前最接近、且至少相隔 7 天」的快照
- 修正% = (eps_next_fq_now - eps_next_fq_prev) / abs(eps_next_fq_prev) * 100
- 條件:
  * 兩次 next_report 相同 (代表同一預估期間, 季度滾動時會改變)
  * abs(prev) >= 0.01 (基期太小時忽略)
  * 若條件不符或沒有足夠舊快照 -> None (略過)
"""
import json
from datetime import date, timedelta

from investor import paths
from investor.data_sources import tradingview
from investor.fileio import atomic_write


def load_snapshots():
    """讀取所有快照 (若檔案不存在或格式錯誤, 回傳空列表)。"""
    snapshots = []
    if paths.ESTIMATES.exists():
        for line in paths.ESTIMATES.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                snapshots.append(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                pass
    return snapshots


def _load_snapshots():
    """向後相容: 呼叫公開的 load_snapshots()。"""
    return load_snapshots()


def _save_snapshots(snapshots):
    """寫入快照 (原子操作)。"""
    lines = [json.dumps(s, ensure_ascii=False) for s in snapshots]
    atomic_write(paths.ESTIMATES, "\n".join(lines) + "\n" if lines else "")


def snapshot(tickers, today=None):
    """抓取預估快照並存檔 (包含技術面資料)。

    Args:
        tickers: 股票代號列表
        today: 快照日期 (預設: 今天, 格式 "YYYY-MM-DD")

    Returns:
        None (在磁碟上寫入快照; 若抓到的有效筆數太少, 不寫檔並回傳 None)
    """
    if today is None:
        today = str(date.today())

    try:
        tv_data = tradingview.fetch_estimates(tickers)  # 含預估 + 技術面
    except Exception as e:
        import warnings
        warnings.warn(f"預估快照失敗: {e}")
        return None

    if not tv_data:
        import warnings
        warnings.warn("預估快照: 抓不到任何有效資料")
        return None

    # 轉換 TradingView 欄位到快照格式
    from investor.navellier import technicals
    import warnings
    snap_data = {}
    for ticker, tv_row in tv_data.items():
        snap_data[ticker] = {
            "eps_fq": tv_row.get("earnings_per_share_forecast_fq"),
            "eps_next_fq": tv_row.get("earnings_per_share_forecast_next_fq"),
            "eps_next_fy": tv_row.get("earnings_per_share_forecast_next_fy"),
            "rev_fq": tv_row.get("revenue_forecast_fq"),
            "rev_next_fq": tv_row.get("revenue_forecast_next_fq"),
            "target": tv_row.get("price_target_average"),
            "rec": tv_row.get("recommendation_mark"),
            "rec_n": tv_row.get("recommendation_total"),
            "next_report": tv_row.get("earnings_release_next_date"),
        }

        # 技術面資料 (抓不到時只警告, 不影響快照)
        try:
            tech = technicals.derive_technicals(tv_row, today)
            snap_data[ticker]["tech"] = {
                "close": tv_row.get("close"),
                "rsi": tech.get("rsi"),
                "sma50": tv_row.get("SMA50"),
                "sma200": tv_row.get("SMA200"),
                "high52": tv_row.get("price_52_week_high"),
                "macd_hist": tech.get("macd_hist"),
                "rec": tv_row.get("Recommend.All"),
                "next_report": tech.get("next_report"),
            }
        except Exception as e:
            warnings.warn(f"技術面計算失敗 ({ticker}): {e}")

    # 讀取現有快照, 同日覆蓋, 寫回
    snapshots = _load_snapshots()
    # 移除當天已有的快照
    snapshots = [s for s in snapshots if s.get("date") != today]
    # 加入新快照
    snapshots.append({"date": today, "data": snap_data})
    # 按日期排序 (最新在後, 便於 compute_revisions 讀取)
    snapshots.sort(key=lambda s: s["date"])

    _save_snapshots(snapshots)
    return snap_data


def revision(prev, cur):
    """計算單檔修正%。

    Args:
        prev: 舊快照資料 (dict)
        cur: 新快照資料 (dict)

    Returns:
        {
            "est_revision": 修正% (float 或 None),
            "est_revision_fy": 下會計年度 EPS 修正% (float 或 None),
            "est_days": 相隔天數 (int)
        }
    """
    # 財報日必須相同 (否則季度滾動會算出假修正)
    if prev.get("next_report") != cur.get("next_report"):
        return {"est_revision": None, "est_revision_fy": None, "est_days": None}

    result = {"est_revision": None, "est_revision_fy": None, "est_days": None}

    # 下季 EPS 修正
    eps_prev = prev.get("eps_next_fq")
    eps_cur = cur.get("eps_next_fq")
    if eps_prev is not None and eps_cur is not None and abs(eps_prev) >= 0.01:
        result["est_revision"] = (eps_cur - eps_prev) / abs(eps_prev) * 100

    # 下會計年度 EPS 修正 (輔助顯示, 不進評分)
    eps_fy_prev = prev.get("eps_next_fy")
    eps_fy_cur = cur.get("eps_next_fy")
    if eps_fy_prev is not None and eps_fy_cur is not None and abs(eps_fy_prev) >= 0.01:
        result["est_revision_fy"] = (eps_fy_cur - eps_fy_prev) / abs(eps_fy_prev) * 100

    return result


def compute_revisions(snapshots, today=None):
    """比較最新快照與約 28 天前的快照, 計算各檔修正%。

    Args:
        snapshots: _load_snapshots() 的結果 (按日期排序)
        today: 基準日 (預設: 今天, 格式 "YYYY-MM-DD")

    Returns:
        {ticker: {"est_revision": %, "est_revision_fy": %, "est_days": 天數}}
    """
    if today is None:
        today = str(date.today())

    if not snapshots:
        return {}

    # 找最新快照
    latest = None
    latest_date = None
    for s in reversed(snapshots):
        if s.get("date") <= today:
            latest = s
            latest_date = s.get("date")
            break

    if not latest or not latest.get("data"):
        return {}

    # 找最接近「約 28 天前」且「至少相隔 7 天」的快照
    today_dt = date.fromisoformat(today)
    target_date = today_dt - timedelta(days=28)
    target_range_start = today_dt - timedelta(days=35)  # 最遠 35 天
    target_range_end = today_dt - timedelta(days=7)      # 最近 7 天

    prev = None
    prev_date = None
    prev_distance = None
    for s in reversed(snapshots):
        s_date = date.fromisoformat(s.get("date"))
        if target_range_start <= s_date <= target_range_end:
            distance = abs((s_date - target_date).days)
            if prev_distance is None or distance < prev_distance:
                prev = s
                prev_date = s_date
                prev_distance = distance

    if not prev or not prev.get("data"):
        return {}

    # 計算各檔修正%
    revisions = {}
    latest_data = latest.get("data", {})
    prev_data = prev.get("data", {})
    latest_dt = date.fromisoformat(latest_date)
    days_apart = (latest_dt - prev_date).days

    for ticker in latest_data:
        if ticker not in prev_data:
            continue

        cur = latest_data[ticker]
        prev_snap = prev_data[ticker]

        rev = revision(prev_snap, cur)
        rev["est_days"] = days_apart
        revisions[ticker] = rev

    return revisions


def get_technicals(today=None):
    """取得今天快照中的技術面資料。

    Args:
        today: 基準日 (預設: 今天, 格式 "YYYY-MM-DD")

    Returns:
        {ticker: {"rsi": ..., "rsi_zone": ..., "trend": ..., ...}}
        或如果快照中沒有 tech 欄位時為空 dict
    """
    if today is None:
        today = str(date.today())

    snaps = load_snapshots()
    if not snaps:
        return {}

    # 找今天的快照
    latest = None
    for s in reversed(snaps):
        if s.get("date") <= today:
            latest = s
            break

    if not latest or not latest.get("data"):
        return {}

    # 從快照提取技術面資料並計算
    from investor.navellier import technicals
    tech_results = {}
    for ticker, snap_ticker in latest["data"].items():
        if "tech" in snap_ticker:
            tech_data = snap_ticker["tech"]
            # 用 derive_technicals 計算完整的技術指標
            tech = technicals.derive_from_snapshot(tech_data, today)
            tech_results[ticker] = tech

    return tech_results
