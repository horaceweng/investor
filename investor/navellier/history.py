"""Navellier 每週量化分數歷史 (data/navellier/history.jsonl): 每週一筆, 以該週週五日期為 key。

過去幾週的分數由歷史價格回算 (source="backfill"); 實際執行寫入的 (source="live") 不會被之後的回算覆蓋。
歷史只用於「近期 Alpha/SD」欄位, 不用來觸發動能冷卻旗標 (見 rating.py)。
"""
import json
from datetime import date, timedelta

from investor import paths
from investor.fileio import atomic_write


def week_end(d):
    """含 d 當天或其後的第一個週五 (舊格式以執行日為 key, 其資料屬於該週)。"""
    dt = date.fromisoformat(d)
    return str(dt + timedelta(days=(4 - dt.weekday()) % 7))


def load_history():
    """回傳 {週五日期: {"scores": {...}, "source": "live"|"backfill"|"legacy"}}"""
    recs = {}
    if paths.HISTORY.exists():
        for line in paths.HISTORY.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            wk = week_end(r["date"]) if r.get("source") is None else r["date"]
            recs[wk] = {"scores": r["scores"], "source": r.get("source", "legacy")}
    return recs


def save_history(recs):
    lines = [json.dumps({"date": d, "scores": recs[d]["scores"], "source": recs[d]["source"]})
             for d in sorted(recs)]
    atomic_write(paths.HISTORY, "\n".join(lines) + "\n")


def merge_history(recs, wk_hist, cutoff):
    """live 紀錄保留; 其餘(legacy/backfill)以回算值覆蓋; 本週(cutoff)一律寫成 live。"""
    for wk, scores in wk_hist.items():
        old = recs.get(wk)
        if wk == cutoff:
            recs[wk] = {"scores": {**(old["scores"] if old and old["source"] == "live" else {}), **scores},
                        "source": "live"}
        elif old and old["source"] == "live":
            recs[wk] = {"scores": {**scores, **old["scores"]}, "source": "live"}
        else:
            recs[wk] = {"scores": scores, "source": "backfill"}
    return recs