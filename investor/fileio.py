"""檔案讀寫小工具: 原子寫入 (寫暫存檔再取代, 讀取方不會讀到寫一半的內容) 與 JSON 存取。"""
import json
import os
from pathlib import Path


def atomic_write(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    if isinstance(data, str):
        tmp.write_text(data, encoding="utf-8")
    else:
        tmp.write_bytes(data)
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path, obj, **kw) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=kw.pop("ensure_ascii", False), **kw))
