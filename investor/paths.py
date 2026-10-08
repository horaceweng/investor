"""所有檔案路徑集中在這裡。其他模組一律從這裡取, 不再各自拼相對路徑或依賴目前工作目錄。

執行期資料全部在 data/ (不納入版本控制):
  data/config/    本機設定與機密: SEC User-Agent、遠端存取名單、密碼、session 金鑰
  data/user/      使用者設定: 自訂觀察清單、股票池選擇、動能門檻 (值得備份)
  data/navellier/ Navellier 評級產出: 每週歷史、報告
  data/cache/     可重新產生的快取: SEC 申報、基本面、成分股、選股中間檔
  data/exports/   各選股程式單獨執行時匯出的 CSV
  data/state.pkl  網頁目前顯示的各項結果
"""
import shutil
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parent

DATA = ROOT / "data"
CONFIG = DATA / "config"
USER = DATA / "user"
NAVELLIER = DATA / "navellier"
CACHE = DATA / "cache"
EXPORTS = DATA / "exports"

STATE_FILE = DATA / "state.pkl"
SERVER_LOG = DATA / "server.log"            # 由 launchd 寫入 (見 README), 程式本身不寫

SEC_CACHE = CACHE / "13f"
FUNDAMENTALS_CACHE = CACHE / "fundamentals.json"
SCREEN_CACHE = CACHE / "screens"
MAGIC_PARTIAL = CACHE / "magic_partial.csv"
PEER_SCORES = CACHE / "peer_scores.json"
MARKET_PRICES = CACHE / "market_weekly.pkl"

WATCHLIST = USER / "watchlist.txt"
POOL_MODE = USER / "universe.txt"
COOLING = USER / "cooling.json"
GROUPS = USER / "groups.json"
HISTORY = NAVELLIER / "history.jsonl"
MOMENTUM_HISTORY = NAVELLIER / "momentum_history.jsonl"
ESTIMATES = NAVELLIER / "estimates.jsonl"

MANAGERS_CSV = PACKAGE / "superinvestors" / "managers.csv"


def universe_cache(name: str) -> Path:
    return CACHE / f"universe_{name}.csv"


def ensure_dir(path: Path) -> Path:
    """建立資料夾 (含上層), 回傳原路徑。"""
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_parent(path: Path) -> Path:
    """建立「檔案」的上層資料夾, 回傳原路徑 (檔案本身不動)。傳入的一律視為檔案路徑, 不靠副檔名猜測。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


# 舊版(平鋪結構)的檔案位置 -> 新位置。啟動時自動搬一次; 已搬過或新位置已有同名檔就略過, 不會覆蓋。
def _legacy_map() -> dict:
    """舊位置 -> 新位置。在呼叫時才組出 (不在 import 時固定), 路徑常數被改動 (如測試指到暫存資料夾) 時才會跟著走。"""
    return {
        **{f"data/{n}": CONFIG / n for n in ("sec_user_agent.txt", "allowed_hosts.txt", "tailscale_users.txt",
                                             "tailscale_devices.txt", "dashboard_password.txt", "session_secret")},
        "data_navellier/watchlist.txt": WATCHLIST,
        "data_navellier/universe.txt": POOL_MODE,
        "data_navellier/cooling.json": COOLING,
        "data_navellier/history.jsonl": HISTORY,
        "data_navellier/fund_cache.json": FUNDAMENTALS_CACHE,
        "data_navellier/nasdaq100.csv": universe_cache("ndx"),
        "data_navellier/sp500.csv": universe_cache("sp500"),
        "13f_cache": SEC_CACHE,
        "sp500_fundamentals.csv": SCREEN_CACHE / "sp500_fundamentals.csv",
        "ndx_fundamentals.csv": SCREEN_CACHE / "ndx_fundamentals.csv",
        "sp500_magic_raw.csv": SCREEN_CACHE / "sp500_magic_raw.csv",
        "ndx_magic_raw.csv": SCREEN_CACHE / "ndx_magic_raw.csv",
        "sp500_magic_partial.csv": MAGIC_PARTIAL,
        "sp500_losers.csv": EXPORTS / "losers_sp500.csv",
        "sp500_magic.csv": EXPORTS / "magic_sp500.csv",
        "portfolio_buys.csv": EXPORTS / "superinvestor_buys.csv",
    }


def migrate_legacy() -> list:
    """把舊位置的檔案搬到新位置, 回傳實際搬了哪些 (供日誌)。可重複執行。
    新位置已有同名檔就不覆蓋, 也不自動刪除舊檔 (留在原處供人工確認); 資料夾則逐檔合併, 不會整個搬進去變成巢狀。"""
    moved = []
    for old, new in _legacy_map().items():
        src = ROOT / old
        if not src.exists():
            continue
        if src.is_dir() and new.is_dir():             # 目的資料夾已存在: 合併 (逐檔搬, 已有的不覆蓋), 不可整個搬進去變成巢狀
            for f in list(src.iterdir()):
                if not (new / f.name).exists():
                    shutil.move(str(f), str(new / f.name))
            if not any(src.iterdir()):
                src.rmdir()
            moved.append(old)
        elif not new.exists():
            ensure_parent(new)
            shutil.move(str(src), str(new))
            moved.append(old)
    # data_navellier 剩下的報告檔 (report_*.json / latest_report_*.json) 歸到 navellier/
    legacy_dir = ROOT / "data_navellier"
    if legacy_dir.is_dir():
        for f in list(legacy_dir.glob("*report*.json")):
            ensure_parent(NAVELLIER / f.name)
            if not (NAVELLIER / f.name).exists():
                shutil.move(str(f), str(NAVELLIER / f.name))
                moved.append(f"data_navellier/{f.name}")
        if not any(legacy_dir.iterdir()):
            legacy_dir.rmdir()
    return moved
