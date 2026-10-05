"""技術面指標計算: 由 TradingView 原始日線資料算出技術信號 (純計算, 不連網, 可單獨測試)。

技術面僅供顯示參考, 不進評分與篩選。
"""
from datetime import datetime, date, timezone


def derive_technicals(raw, today=None):
    """由 TradingView 日線資料計算技術面指標。

    Args:
        raw: {欄位: 值} dict, 包含 close, RSI, SMA50, SMA200, price_52_week_high,
             Recommend.All, MACD.macd, MACD.signal, earnings_release_next_date 等
        today: 基準日 (預設: 今天, 格式 "YYYY-MM-DD")

    Returns:
        {
            "rsi": float,
            "rsi_zone": "過熱" | "超賣" | None,
            "trend": "多頭" | "回檔" | "空頭" | None,
            "off_high_pct": float (<=0),
            "macd_hist": float,
            "macd_dir": "轉強" | "轉弱" | None,
            "tech_rating": "強力買進" | "買進" | "中立" | "賣出" | "強力賣出" | None,
            "next_report": "YYYY-MM-DD" | None,
            "days_to_report": int | None,
            "report_soon": bool,
        }
    """
    if today is None:
        today = str(date.today())

    result = {
        "rsi": None,
        "rsi_zone": None,
        "trend": None,
        "off_high_pct": None,
        "macd_hist": None,
        "macd_dir": None,
        "tech_rating": None,
        "next_report": None,
        "days_to_report": None,
        "report_soon": False,
    }

    # RSI
    rsi = raw.get("RSI")
    if rsi is not None and not (isinstance(rsi, float) and rsi != rsi):  # not NaN
        result["rsi"] = float(rsi)
        if rsi >= 70:
            result["rsi_zone"] = "過熱"
        elif rsi <= 30:
            result["rsi_zone"] = "超賣"

    # 趨勢 (close vs SMA50 vs SMA200)
    close = raw.get("close")
    sma50 = raw.get("SMA50")
    sma200 = raw.get("SMA200")
    if close is not None and sma50 is not None and sma200 is not None:
        close = float(close)
        sma50 = float(sma50)
        sma200 = float(sma200)
        if close > sma50 and close > sma200:
            result["trend"] = "多頭"
        elif close > sma200 and close <= sma50:
            result["trend"] = "回檔"
        elif close <= sma200:
            result["trend"] = "空頭"

    # 距 52 週高 (%)
    high52 = raw.get("price_52_week_high")
    if close is not None and high52 is not None:
        high52 = float(high52)
        if high52 > 0:
            result["off_high_pct"] = (float(close) / high52 - 1) * 100

    # MACD
    macd = raw.get("MACD.macd")
    signal = raw.get("MACD.signal")
    if macd is not None and signal is not None:
        macd = float(macd)
        signal = float(signal)
        result["macd_hist"] = macd - signal
        if result["macd_hist"] > 0:
            result["macd_dir"] = "轉強"
        elif result["macd_hist"] < 0:
            result["macd_dir"] = "轉弱"

    # 技術評等 (Recommend.All)
    rec = raw.get("Recommend.All")
    if rec is not None and not (isinstance(rec, float) and rec != rec):
        rec = float(rec)
        if rec >= 0.5:
            result["tech_rating"] = "強力買進"
        elif rec >= 0.1:
            result["tech_rating"] = "買進"
        elif rec > -0.1:
            result["tech_rating"] = "中立"
        elif rec > -0.5:
            result["tech_rating"] = "賣出"
        else:
            result["tech_rating"] = "強力賣出"

    # 下次財報日
    next_report_unix = raw.get("earnings_release_next_date")
    if next_report_unix is not None:
        try:
            next_report_date = datetime.utcfromtimestamp(int(next_report_unix)).date()
            result["next_report"] = str(next_report_date)

            today_date = date.fromisoformat(today)
            days_diff = (next_report_date - today_date).days
            if days_diff >= 0:                       # 財報日當天 (0 天) 也要提醒
                result["days_to_report"] = days_diff
                if days_diff <= 7:
                    result["report_soon"] = True
        except (ValueError, OSError, OverflowError):
            pass

    return result


def derive_from_snapshot(tech, today=None):
    """由快照裡存的 tech 子物件 (close/rsi/sma50/sma200/high52/macd_hist/rec/next_report) 還原完整技術面。
    快照欄位名與 TradingView 原始欄位不同, 這裡對應回去再交給 derive_technicals, 讓讀快照與即時抓取結果一致。"""
    raw = {"close": tech.get("close"), "RSI": tech.get("rsi"), "SMA50": tech.get("sma50"), "SMA200": tech.get("sma200"),
           "price_52_week_high": tech.get("high52"), "Recommend.All": tech.get("rec"),
           "MACD.macd": tech.get("macd_hist"), "MACD.signal": 0.0 if tech.get("macd_hist") is not None else None}
    nr = tech.get("next_report")
    if nr:
        try:
            raw["earnings_release_next_date"] = datetime(*map(int, str(nr).split("-")), tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    return derive_technicals(raw, today)


def hints(row, fund_grade=None, cooling_flag=None):
    """組合技術面提示字串 (繁中)。

    Args:
        row: derive_technicals() 的結果 dict
        fund_grade: 評級 ("A"~"E" 或 "N/A"); rating.py 傳入的是「綜合評級」(overall), 參數名沿用舊稱
        cooling_flag: 冷卻旗標字串 (從 rating.cooling_flag() 回傳)

    Returns:
        字串列表 (可為空)
    """
    hints_list = []

    # 評級 A 且 RSI 過熱
    if fund_grade == "A" and row.get("rsi_zone") == "過熱":
        hints_list.append("等回檔")

    # 評級 A 或 B 且趨勢回檔
    if fund_grade in ("A", "B") and row.get("trend") == "回檔":
        hints_list.append("可留意")

    # 冷卻規則判定「建議移除」且趨勢空頭
    if cooling_flag and "❌ 建議剔除" in cooling_flag and row.get("trend") == "空頭":
        hints_list.append("弱勢確認")

    # 財報在 N 天內
    if row.get("report_soon"):
        days = row.get("days_to_report")
        if days is not None:
            hints_list.append(f"財報在 {days} 天內")

    return hints_list
