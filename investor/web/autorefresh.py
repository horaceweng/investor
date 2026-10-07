"""自動更新: 程式啟動與每次載入頁面時檢查資料是否過期, 過期才在背景更新; 已是最新的就完全不上網。

- 技術面: 每個交易日一次 (週末沿用週五的資料)。只抓一次 TradingView, 不碰評級。
- 評級:   每週一次 -- 最近一個已收完的週五 (alpha_beta.complete_week_cutoff) 變了才重算;
          同一週內重算結果本來就相同, 所以不重複上網。
更新失敗後隔一段時間才會再試, 避免每次重新整理頁面都重打一次被限流的來源。
"""
import time
from datetime import date, timedelta

from investor.navellier import alpha_beta, groups, settings
from investor.web import jobs, store

RETRY_AFTER = {"nav": 6 * 3600, "tech": 3600}    # 秒; 評級很重又常被 Yahoo 限流, 退避久一點
_last_try = {}


def last_trading_day(today=None):
    """最近一個平日 (週末 -> 週五)。不處理國定假日: 假日當天多抓一次也只是一個請求。"""
    d = today or date.today()
    return d - timedelta(days=max(0, d.weekday() - 4))


def due(state, mode, today=None):
    """回傳這個股票池需要的更新: "nav" (評級) / "tech" (技術面) / None (都是最新)。"""
    today = today or date.today()
    nav = (state.get("nav") or {}).get(mode)
    if not nav or nav.get("asof") != str(alpha_beta.complete_week_cutoff(today)):
        return "nav"
    if mode == "watchlist" and sorted(nav.get("tickers") or []) != sorted(settings.load_watchlist()):
        return "nav"                     # 觀察清單增減了 (編輯器、☆ 點選或直接改檔都算): 新股票要納入評級
    if nav.get("groups_sig") != groups.signature(mode, nav.get("tickers") or []):
        return "nav"                     # 分類改了: 組內排名要重算 (不需等下一週)
    if nav.get("tech_date") != str(last_trading_day(today)):
        return "tech"
    return None


def maybe_start(state=None, now=None):
    """啟動背景更新 (若需要); 不阻塞。回傳啟動的工作名稱或 None。"""
    state = store.state if state is None else state
    mode = settings.load_mode()
    if mode == "watchlist" and not settings.load_watchlist():
        return None
    with store.lock:
        kind = due(state, mode)
    now = time.time() if now is None else now
    last = _last_try.get((kind, mode))
    if not kind or (last is not None and now - last < RETRY_AFTER[kind]):
        return None
    if jobs.start_job(kind):
        _last_try[(kind, mode)] = now
        return kind
    return None
