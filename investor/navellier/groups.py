"""自訂觀察清單的分類: 只決定表格怎麼分段顯示 (評級本身是對「同產業全市場」比較, 見 navellier.peers)。

分類存在 data/user/groups.json (值得備份): {"groups": [{"name": 分類名, "tickers": [代號...]}, ...]}, 順序即顯示順序。
只套用在自訂觀察清單; 沒被分到任何類的股票歸「未分類」。純計算與讀寫, 不連網。
"""
import json

from investor import paths
from investor.fileio import atomic_write, read_json
from investor.navellier import settings

UNGROUPED = "未分類"


def load():
    """[(分類名, [代號...])], 依檔案順序; 檔案不存在或壞掉 -> 空 list (等於不分類)。"""
    data = read_json(paths.GROUPS, {})
    out = []
    for g in (data.get("groups") or []) if isinstance(data, dict) else []:
        try:
            name = str(g["name"]).strip()
            tks = [t for t in (settings.normalize_ticker(x) for x in g["tickers"]) if t]
        except (KeyError, TypeError):
            continue
        if name and tks:
            out.append((name, tks))
    return out


def save(groups):
    atomic_write(paths.GROUPS, json.dumps({"groups": [{"name": n, "tickers": t} for n, t in groups]},
                                          ensure_ascii=False, indent=1) + "\n")


def parse_text(text):
    """編輯器文字 -> [(分類名, [代號...])]。每行「分類名: 代號 代號 …」(冒號全形也可), '#' 之後為註解。
    同一檔出現在兩個分類會丟 ValueError (一檔只能在一類)。"""
    groups, seen = [], {}
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name, sep, rest = line.replace("：", ":").partition(":")
        name = name.strip()
        if not sep or not name:
            raise ValueError(f"格式錯誤 (應為「分類名: 代號 代號…」): {line[:30]}")
        tickers, bad = settings.split_tickers(rest)
        if bad:
            raise ValueError(f"「{name}」有無法辨識的代號: " + "、".join(bad[:5]))
        for t in tickers:
            if t in seen:
                raise ValueError(f"{t} 同時在「{seen[t]}」與「{name}」, 一檔只能放一類")
            seen[t] = name
        if tickers:
            groups.append((name, tickers))
    return groups


def dump_text(groups):
    return "\n".join(f"{n}: {' '.join(t)}" for n, t in groups) + ("\n" if groups else "")


def assign(tickers, groups=None):
    """-> ({代號: 分類名}, [分類順序])。沒分到類的歸「未分類」(排最後); 只列出清單內的代號。"""
    groups = load() if groups is None else groups
    where = {t: n for n, ts in groups for t in ts}
    order = [n for n, ts in groups if any(t in tickers for t in ts)]
    amap = {t: where.get(t, UNGROUPED) for t in tickers}
    if UNGROUPED in amap.values():
        order.append(UNGROUPED)
    return amap, order


def active(mode, tickers):
    """目前這個股票池要不要分組: 只有自訂觀察清單、且確實有設分類才分。回傳 ({代號: 分類}, 順序) 或 (None, [])。"""
    if mode != "watchlist":
        return None, []
    if not load():
        return None, []
    return assign(tickers)


def signature(mode, tickers):
    """分類設定的指紋: 分類改了, 已存的評級就過期 (autorefresh 用它判斷要不要重算)。"""
    amap, order = active(mode, tickers)
    return None if amap is None else json.dumps([order, sorted(amap.items())], ensure_ascii=False)


def add_tickers(assignments, groups=None):
    """把股票放進分類: assignments = {代號: 分類名}; 不存在的分類會新建, 該股原本在別類則移過去。回傳新的 [(分類名, [代號...])]。"""
    groups = [(n, list(ts)) for n, ts in (load() if groups is None else groups)]
    for raw, name in assignments.items():
        t, name = settings.normalize_ticker(str(raw)), str(name).strip()
        if t is None:
            raise ValueError(f"無法辨識的代號: {raw!r}")
        if not name or len(name) > 40 or ":" in name or "：" in name or "#" in name:
            raise ValueError(f"分類名稱無效: {name!r}")
        groups = [(n, [x for x in ts if x != t]) for n, ts in groups]
        for n, ts in groups:
            if n == name:
                ts.append(t)
                break
        else:
            groups.append((name, [t]))
    return [(n, ts) for n, ts in groups if ts]
