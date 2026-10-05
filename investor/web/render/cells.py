"""表格元件: 儲存格 (cell) 與整張表 (table), 輸出 HTML 字串。"""
import html

import pandas as pd

from investor.data_sources.prices import PERF


TONE_UP = {"多頭", "轉強", "買進", "強力買進"}
TONE_DN = {"空頭", "轉弱", "賣出", "強力賣出"}


def esc(x):
    return html.escape(str(x))


def cell(kind, v, extra=""):
    """kind: text / ticker / num / int / delta / pct(帶漲跌色) / plain(百分比不上色)。"""
    missing = v is None or (isinstance(v, float) and pd.isna(v)) or v == ""
    if missing:
        return '<td class="na" data-v="-1e18">—</td>'
    if kind == "ticker":
        return (f'<td class="tk" data-v="{esc(v)}"><a href="https://finance.yahoo.com/quote/{esc(v)}" '
                f'target="_blank" rel="noopener">{esc(v)}</a></td>')
    if kind == "co":
        return f'<td class="co" data-v="{esc(v)}"{extra}>{esc(v)}</td>'
    if kind == "series":
        return f'<td class="sr" data-v="{esc(v)}"{extra}>{esc(v)}</td>'
    if kind == "tone":   # 文字依漲跌語意上色 (紅漲綠跌由 CSS 的 up/dn 變數決定)
        cls = "up" if v in TONE_UP else "dn" if v in TONE_DN else ""
        return f'<td class="tx {cls}" data-v="{esc(v)}"{extra}>{esc(v)}</td>'
    if kind == "text":
        return f'<td class="tx" data-v="{esc(v)}"{extra}>{esc(v)}</td>'
    v = float(v) + 0.0   # 把 -0.0 正規化為 0.0, 避免顯示 -0.00
    if kind == "pct":
        cls = "up" if v > 0 else "dn" if v < 0 else ""
        return f'<td class="n {cls}" data-v="{v}">{v:+,.1f}%</td>'
    if kind == "plain":
        return f'<td class="n" data-v="{v}">{v:,.2f}%</td>'
    if kind == "int":
        return f'<td class="n" data-v="{v}"{extra}>{v:,.0f}</td>'
    if kind == "delta":
        return f'<td class="n" data-v="{v}">{v:+,.0f}</td>'
    return f'<td class="n" data-v="{v}"{extra}>{v:,.2f}</td>'


def table(df, cols, perf, with_perf=True, tail=(), compact=False):
    """cols: [(欄位名, 標題, kind)]; 自動附加 1M/3M/6M/1Y 欄。"""
    head = "<th>#</th>" + "".join(
        f'<th data-k="{"t" if k in ("text", "tone", "ticker", "series", "co") else "n"}">{esc(lab)}</th>' for _, lab, k in cols)
    if with_perf:
        head += "".join(f'<th data-k="n">{p}</th>' for p in PERF)
    head += "".join(f'<th data-k="{"t" if k in ("text", "tone", "ticker", "series", "co") else "n"}">{esc(lab)}</th>' for _, lab, k in tail)
    rows = []
    for i, r in enumerate(df.to_dict("records"), 1):
        tds = f'<td class="n rk" data-v="{i}">{i}</td>'
        for c, _, k in cols:
            txt = r.get(c + "_txt")
            if txt and not (isinstance(r.get(c), (int, float)) and r.get(c) == r.get(c)):    # 虧損/轉盈: 顯示文字而非「—」
                tds += (f'<td class="n" data-v="{-1e18 if txt == "虧損" else 1e18}" title="{esc(r.get(c + "_tip", ""))}">'
                        f'<b>{esc(txt)}</b></td>')
                continue
            if k == "star":
                on = bool(r.get(c))
                tds += (f'<td class="star{" on" if on else ""}" data-tk="{esc(r.get("代號"))}" data-v="{1 if on else 0}" '
                        f'title="點一下加入/移出自訂觀察清單">{"★" if on else "☆"}</td>')
                continue
            tip = r.get(c + "_tip") or (r.get("買進者") if c == "買進家數" else None)
            tds += cell(k, r.get(c), f' title="{esc(tip)}"' if tip else "")
        if with_perf:
            for p in PERF:
                tds += cell("pct", perf.get(r.get("代號"), {}).get(p))
        for c, _, k in tail:
            tip = r.get(c + "_tip")
            tds += cell(k, r.get(c), f' title="{esc(tip)}"' if tip else "")
        rows.append(f"<tr>{tds}</tr>")
    box, tcls = ("scroll fit", "sortable compact") if compact else ("scroll", "sortable")
    return (f'<div class="{box}"><table class="{tcls}"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>')
