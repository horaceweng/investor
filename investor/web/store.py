"""網頁目前顯示的各項結果 (data/state.pkl): 記憶體中的字典 + 鎖 + 原子存取。

結構: state[功能][股票池] = {...結果, "ts": 更新時間}; 大師買進(buys)與股價表現(perf)與股票池無關, 直接存。
"""
import pickle
import threading
from datetime import datetime

from investor import paths
from investor.fileio import atomic_write

state = {}
lock = threading.Lock()


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def save() -> None:
    atomic_write(paths.STATE_FILE, pickle.dumps(state))


def load() -> None:
    try:
        state.update(pickle.loads(paths.STATE_FILE.read_bytes()))
    except Exception:
        pass
    for mod, marker in (("losers", "df"), ("value", "pe"), ("magic", "res")):   # 舊格式(單一股票池) -> 依股票池分開存
        if isinstance(state.get(mod), dict) and marker in state[mod]:
            state[mod] = {"sp500": state[mod]}
    if "rows" in (state.get("nav") or {}):
        state["nav"] = {"watchlist": state["nav"]}
