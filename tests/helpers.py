"""測試用工具: 把 investor.paths 指到暫存資料夾, 測試不會碰到真正的資料。"""
import contextlib
import tempfile
from pathlib import Path
from unittest import mock

from investor import paths


@contextlib.contextmanager
def temp_data():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        data = root / "data"
        over = {
            "ROOT": root, "DATA": data, "CONFIG": data / "config", "USER": data / "user",
            "NAVELLIER": data / "navellier", "CACHE": data / "cache", "EXPORTS": data / "exports",
            "STATE_FILE": data / "state.pkl", "SEC_CACHE": data / "cache" / "13f",
            "FUNDAMENTALS_CACHE": data / "cache" / "fundamentals.json", "SCREEN_CACHE": data / "cache" / "screens",
            "MAGIC_PARTIAL": data / "cache" / "magic_partial.csv", "PEER_SCORES": data / "cache" / "peer_scores.json", "WATCHLIST": data / "user" / "watchlist.txt",
            "POOL_MODE": data / "user" / "universe.txt", "COOLING": data / "user" / "cooling.json", "GROUPS": data / "user" / "groups.json",
            "HISTORY": data / "navellier" / "history.jsonl", "ESTIMATES": data / "navellier" / "estimates.jsonl",
        }
        with contextlib.ExitStack() as st:
            for k, v in over.items():
                st.enter_context(mock.patch.object(paths, k, v))
            yield root
