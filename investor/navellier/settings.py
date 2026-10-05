"""使用者設定: 自訂觀察清單、股票池選擇、動能冷卻門檻。檔案都在 data/user/ (值得備份), 寫入一律原子操作。"""
import re
import unicodedata

from investor import paths
from investor.fileio import atomic_write, read_json, write_json

# 頁首「選股股票池」的選項; 順序即按鈕順序。watchlist = 自訂觀察清單, 其餘為指數成分股
MODES = {"watchlist": "自訂觀察清單", "sp500": "S&P 500", "ndx": "Nasdaq 100"}
DEFAULT_WATCHLIST = ["NVDA", "TSM", "AMD", "AVGO", "GOOG", "MSFT", "AAPL", "MU", "SNDK",
                     "COST", "BRK-B", "TSLA", "SPCX"]
DEFAULT_COOLING = {"warn": 0.6, "remove": 0.4}     # 量化分數在股票池的分位低於此值 -> 警示 / 建議剔除
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


# ───────────── 觀察清單 ─────────────
_SPLIT = re.compile(r"[\s,;、]+")


def normalize_ticker(tok):
    """全形轉半形、去掉前綴 $、轉大寫、'.' -> '-' (BRK.B -> BRK-B); 不合法回傳 None。"""
    t = unicodedata.normalize("NFKC", tok).strip().lstrip("$").upper().replace(".", "-")
    return t if TICKER_RE.match(t) else None


def split_tickers(text):
    """回傳 (有效代號[去重, 保序], 無法辨識的原始字串)。
    以逗號/分號/頓號/空白/換行分隔 (全形標點也可); '#' 之後為註解。"""
    good, bad = [], []
    for line in unicodedata.normalize("NFKC", text).splitlines():
        for tok in _SPLIT.split(line.split("#")[0].strip()):
            if not tok:
                continue
            t = normalize_ticker(tok)
            if t is None:
                bad.append(tok)
            elif t not in good:
                good.append(t)
    return good, bad


def parse_tickers(text):
    good, bad = split_tickers(text)
    if bad:
        raise ValueError("無法辨識的代號: " + "、".join(bad[:8]))
    return good


def load_watchlist():
    if paths.WATCHLIST.exists():
        t, _ = split_tickers(paths.WATCHLIST.read_text())      # 檔案中有壞掉的行就略過, 不讓整個頁面壞掉
        if t:
            return t
    return list(DEFAULT_WATCHLIST)


def save_watchlist(tickers):
    if not tickers:
        raise ValueError("觀察清單不能是空的，至少要有 1 檔")
    atomic_write(paths.WATCHLIST, "\n".join(tickers) + "\n")     # 原子寫入, 更新工作同時讀取也不會讀到半份


def toggle_watchlist(ticker):
    """加入/移出一檔; 回傳 (新清單, 是否在清單中)。"""
    t = normalize_ticker(ticker)
    if t is None:
        raise ValueError(f"無法辨識的代號: {ticker!r}")
    cur = load_watchlist()
    if t in cur:
        if len(cur) == 1:
            raise ValueError("清單至少要保留 1 檔")
        cur.remove(t)
        member = False
    else:
        cur.append(t)
        member = True
    save_watchlist(cur)
    return cur, member


# ───────────── 股票池選擇 ─────────────
def load_mode():
    try:
        m = paths.POOL_MODE.read_text().strip()
        return m if m in MODES else "sp500"
    except OSError:
        return "sp500"


def save_mode(mode):
    if mode not in MODES:
        raise ValueError(f"未知的股票池: {mode!r}")
    atomic_write(paths.POOL_MODE, mode + "\n")


# ───────────── 動能冷卻門檻 ─────────────
def load_cooling():
    c = read_json(paths.COOLING, {})
    try:
        w, r = float(c["warn"]), float(c["remove"])
        if 0 < r < w < 1:
            return {"warn": w, "remove": r}
    except (KeyError, ValueError, TypeError):
        pass
    return dict(DEFAULT_COOLING)


def save_cooling(warn, remove):
    """warn / remove 為 0~1 的分位門檻, 需 0 < remove < warn < 1。"""
    warn, remove = float(warn), float(remove)
    if not 0 < remove < warn < 1:
        raise ValueError("門檻需滿足 0 < 剔除 < 警示 < 100 (%)")
    write_json(paths.COOLING, {"warn": warn, "remove": remove})
