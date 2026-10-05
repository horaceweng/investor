"""整頁 HTML 組裝: 頁首、分頁按鈕、各分頁內容、頁尾; CSS/JS 是 static/ 下的真正檔案。"""
from pathlib import Path

from investor.navellier.settings import MODES, load_mode
from investor.web.render.cells import esc
from investor.web.render.tabs import make_tabs

_STATIC = Path(__file__).resolve().parent / "static"
CSS = (_STATIC / "app.css").read_text(encoding="utf-8")
JS = (_STATIC / "app.js").read_text(encoding="utf-8")


def build_page(state: dict, logout: bool = False) -> str:
    tabs = make_tabs(state)
    dim = ' class="dim" title="自訂觀察清單不適用"'      # f-string 內不能有反斜線, 先算好
    nav = "".join(f'<button type="button" data-t="{t["id"]}"{dim if t.get("na") else ""}>{esc(t["title"])}</button>' for t in tabs)
    secs = []
    for t in tabs:
        if t.get("na"):
            secs.append(f'<section id="{t["id"]}"><div class="card"><h2>{esc(t["heading"])}</h2>'
                        '<p>此功能不適用「自訂觀察清單」。請在頁首的「選股股票池」選擇 S&amp;P 500 或 Nasdaq 100；'
                        '自訂觀察清單目前只用於「Navellier 評級」。</p></div></section>')
            continue
        notes = "".join(f'<div class="note">{n}</div>' for n in t["notes"])
        body = t["table"] or '<div class="empty">尚無資料，請按「更新」按鈕抓取。</div>'
        tail_notes = ""
        if t.get("notes_end"):      # 這個分頁的說明較長, 放在表格之後
            tail_notes, notes = f'<div class="card" style="margin-top:14px"><h3 style="margin-top:0">說明與警示</h3>{notes}</div>', ""
        secs.append(
            f'<section id="{t["id"]}"><div class="card"><div class="top"><div><h2>{esc(t["heading"])}</h2>'
            f'<div class="stamp">{esc(t["stamp"])}</div></div>'
            f'<button type="button" data-task="{t["task"]}">{esc(t["btn"])}</button></div>'
            f'<p>{t["desc"]}</p>{t.get("extra", "")}{notes}</div>{body}{tail_notes}</section>')
    pf = state.get("perf")
    pf_txt = f'股價表現更新於 {pf["ts"]}' if pf else "股價表現尚未計算"
    return f"""<!doctype html><html lang="zh-Hant" data-color="tw"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>選股儀表板</title><style>{CSS}</style></head><body>
<header><h1>選股儀表板</h1>
<div class="sub">{esc(pf_txt)} ｜ 點欄位標題可排序，點代號開啟 Yahoo Finance</div>
<div class="bar"><button type="button" class="primary" data-task="all">全部更新</button>
<button type="button" id="cbtn"></button>
<span class="stamp" style="margin-left:10px" title="下面各分頁顯示的都是這個股票池的結果；自訂觀察清單只作用於 Navellier 評級">選股股票池</span>
<div class="seg" style="margin:0">{"".join(f'<button type="button" data-mode="{k}" class="{"on" if k == load_mode() else ""}">{esc(v)}</button>' for k, v in MODES.items())}</div>
<span id="msg"></span>{'<a class="stamp" href="/logout" style="margin-left:auto">登出</a>' if logout else ""}</div></header>
<nav>{nav}</nav><main>{"".join(secs)}</main>
<footer>資料來源: Wikipedia(成分股)、Yahoo Finance via yfinance(價格與基本面)、SEC EDGAR 13F-HR、OpenFIGI(代號對應)。
1M/3M/6M/1Y 為調整後收盤價(含股利與分割)的漲跌幅。「全部更新」會重新抓取目前所選股票池的所有資料，約需 10 分鐘，Yahoo 限流時可能失敗，失敗時會保留舊資料。
本頁為量化篩選結果，不構成投資建議；資料可能有延遲或錯誤，請自行核對。</footer>
<script>{JS}</script></body></html>"""
