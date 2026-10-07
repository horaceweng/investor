"""JSON API 的各項操作: 每個一個函式, 吃解析好的請求內容、回傳 (HTTP 狀態碼, 內容)。不碰 socket, 可單獨測試。

輸入不合法時丟 ValueError (訊息會原樣回給前端)。
"""
from investor.navellier import groups, settings
from investor.web import jobs, steps


def watchlist_set(body: dict):
    raw = body.get("tickers")
    text = "\n".join(str(x) for x in raw) if isinstance(raw, list) else str(body.get("text", ""))
    tickers = settings.parse_tickers(text)
    settings.save_watchlist(tickers)
    return 200, {"tickers": tickers}


def watchlist_toggle(body: dict):
    tickers, member = settings.toggle_watchlist(str(body.get("ticker", "")))
    return 200, {"tickers": tickers, "member": member}


def groups_set(body: dict):
    """儲存自訂觀察清單的分類 (文字格式: 每行「分類名: 代號 代號…」); 存好後背景重算評級 (組內排名)。"""
    parsed = groups.parse_text(str(body.get("text", "")))
    groups.save(parsed)
    return 200, {"groups": len(parsed), "started": jobs.start_job("nav")}


def groups_assign(body: dict):
    """把新加入、尚未分類的股票放進分類 ({"assign": {代號: 分類名}}), 存好後背景重算評級。"""
    assign = body.get("assign")
    if not isinstance(assign, dict) or not assign:
        raise ValueError("沒有要分類的股票")
    groups.save(groups.add_tickers(assign))
    return 200, {"assigned": len(assign), "started": jobs.start_job("nav")}


def cooling_set(body: dict):
    try:
        settings.save_cooling(float(body["warn"]) / 100, float(body["remove"]) / 100)
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"門檻無效: {e}")
    return 200, settings.load_cooling()


def universe_set(body: dict):
    settings.save_mode(str(body.get("mode", "")))
    return 200, {"mode": settings.load_mode()}


def update(task: str):
    if task not in steps.TASKS or task == "init":
        return 400, {"error": "unknown task"}
    if not steps.applicable(task, settings.load_mode()):
        return 400, {"error": "此功能不適用自訂觀察清單"}
    started = jobs.start_job(task)
    return (202 if started else 409), {"started": started}


# 路徑 -> (函式, 請求內容大小上限 bytes)。update 沒有請求內容, 由 server 把 query 的 task 傳入。
JSON_POST = {
    "/api/watchlist": (watchlist_set, 500_000),         # 只是防止異常大的請求, 不限制檔數
    "/api/watchlist/toggle": (watchlist_toggle, 500_000),
    "/api/groups": (groups_set, 200_000),
    "/api/groups/assign": (groups_assign, 200_000),
    "/api/cooling": (cooling_set, 1000),
    "/api/universe": (universe_set, 1000),
}
