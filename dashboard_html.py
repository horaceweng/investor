"""儀表板頁面產生: 股價表現計算 + HTML 渲染 (由 main.py 的伺服器呼叫)。"""
import html
import json
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from navellier.run_weekly import MODES, load_cooling, load_mode, load_watchlist
from sp500_losers import UNIVERSES
from yf_util import retry

PERF = ["1M", "3M", "6M", "1Y"]
OFFSETS = {"1M": pd.DateOffset(months=1), "3M": pd.DateOffset(months=3),
           "6M": pd.DateOffset(months=6), "1Y": pd.DateOffset(years=1)}


# ───────────────────────── 股價表現 ─────────────────────────
def performance(tickers) -> dict:
    """回傳 {代號: {1M,3M,6M,1Y}} (%); 調整後收盤價, 以各檔最後一個交易日回推。"""
    tickers = sorted({t for t in tickers if t})
    if not tickers:
        return {}
    px = retry(lambda: yf.download(tickers, start=date.today() - timedelta(days=400),
                                   auto_adjust=True, progress=False)["Close"])
    if isinstance(px, pd.Series):
        px = px.to_frame(tickers[0])
    out = {}
    for t in px.columns:
        s = px[t].dropna()
        if s.empty:
            continue
        last, ld = s.iloc[-1], s.index[-1]
        row = {}
        for lab, off in OFFSETS.items():
            prior = s[s.index <= ld - off]
            row[lab] = (last / prior.iloc[-1] - 1) * 100 if len(prior) else float("nan")
        out[t] = row
    return out


# ───────────────────────── 表格 ─────────────────────────
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
        f'<th data-k="{"t" if k in ("text", "ticker", "series", "co") else "n"}">{esc(lab)}</th>' for _, lab, k in cols)
    if with_perf:
        head += "".join(f'<th data-k="n">{p}</th>' for p in PERF)
    head += "".join(f'<th data-k="{"t" if k in ("text", "ticker", "series", "co") else "n"}">{esc(lab)}</th>' for _, lab, k in tail)
    rows = []
    for i, r in enumerate(df.to_dict("records"), 1):
        tds = f'<td class="n rk" data-v="{i}">{i}</td>'
        for c, _, k in cols:
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


# ───────────────────────── 分頁內容 ─────────────────────────
FACTOR_LABELS = [("sales_yoy", "營收年增%"), ("margin_exp_yoy_pp", "營業利益率年增(pp)"), ("earn_yoy", "EPS年增%"),
                 ("earn_momentum_pp", "盈餘動能(pp)"), ("surprise_avg", "財報驚喜均值%"),
                 ("fcf_yoy", "FCF年增%"), ("roe_ttm", "ROE(TTM)%")]


def _stamp(mod):
    return f'資料更新於 {mod["ts"]}' if mod else "尚無資料"


def make_tabs(state: dict) -> list:
    perf = (state.get("perf") or {}).get("data", {})
    tabs = []
    mode = load_mode()                           # 頁首選的股票池: watchlist / sp500 / ndx
    uni = mode if mode in UNIVERSES else None    # 自訂觀察清單: 五個選股分頁不適用
    ul = UNIVERSES.get(uni, "")                  # 顯示用名稱
    uh = html.escape(ul)                                 # S&P 500 -> S&amp;P 500
    sp = uni == "sp500"
    split_note = ("⚠ CTVA、HONA、FDXF 的跌幅疑似受分拆影響而失真，請先核對。" if sp
                  else "⚠ HON、HONA 等近期分拆/改名的公司，價格歷史可能失真，請先核對。")

    L = (state.get("losers") or {}).get(uni)
    tabs.append(dict(
        id="s1", title="13週跌幅", task="losers", btn="更新股價與跌幅", stamp=_stamp(L),
        heading=f"過去 13 週跌幅最大 40 檔 — {ul}" + (f" ({L['base']} → {L['last']})" if L else ""),
        desc=f"{uh} 成分股，13 週調整後收盤價漲跌幅由低到高。",
        notes=[split_note],
        table=table(L["df"], [("代號", "代號", "ticker"), ("Security", "公司", "text"), ("GICS Sector", "板塊", "text"),
                              ("起始價", "13週前", "num"), ("最新價", "最新價", "num"), ("13週%", "13週", "pct")], perf) if L else ""))

    V = (state.get("value") or {}).get(uni)
    cnt = V["counts"] if V else (0, 0, 0, 0)
    vcols = [("代號", "代號", "ticker"), ("Security", "公司", "text"), ("GICS Sector", "板塊", "text"),
             ("股價", "股價", "num"), ("本益比", "本益比", "num"), ("股價淨值比", "淨值比", "num"), ("殖利率%", "殖利率", "plain")]
    for sid, key, title, heading, desc, notes in (
        ("s2", "pe", "本益比最低", f"本益比最低 40 檔 (trailing PE，僅正值) — {ul}",
         f"虧損公司(本益比為負)已排除；有效資料 {cnt[0]} / {cnt[3]} 檔。",
         ["低本益比常見於景氣循環股、金融股，或市場預期獲利下滑的公司(value trap)。"]),
        ("s3", "pb", "淨值比最低", f"股價淨值比最低 40 檔 (僅正值) — {ul}",
         f"淨值為負者已排除；有效資料 {cnt[1]} / {cnt[3]} 檔。",
         (["BRK-B 的淨值比顯示 0.00 是資料錯誤，不可信。", "CTVA 的 0.32 疑受分拆影響。"] if sp
          else ["淨值很低或為負常見於大量回購或輕資產的公司，需搭配其他指標判讀。"])),
        ("s4", "yield", "殖利率最高", f"股息殖利率最高 40 檔 — {ul}",
         f"殖利率 = 年化股利 ÷ 股價；有資料者 {cnt[2]} / {cnt[3]} 檔。",
         ["殖利率高有時是股價大跌造成，不代表股利安全。" + ("MO、CCI 等淨值為負的公司只會出現在這張表。" if sp else "")]),
    ):
        tabs.append(dict(id=sid, title=title, task="value", btn="更新基本面", stamp=_stamp(V), heading=heading,
                         desc=desc, notes=notes + ([] if sp else ["Nasdaq 100 中不在 S&amp;P 500 的少數公司(如 ASML、ARM、PDD)沒有 GICS 板塊，板塊欄顯示空白。"]),
                         table=table(V[key], vcols, perf) if V else ""))

    B = state.get("buys")
    if B:
        qcols = [(f"買進@{q[:7]}", f"買進@{q[2:7]}", "int") for q in B["quarters"][1:]]
        bt = table(B["df"], [("代號", "代號", "ticker"), ("公司", "公司", "text"), ("買進家數", "買進家數", "int")] + qcols +
                   [("新建倉", "新建倉", "int"), ("持有家數", "持有家數", "int"), ("持有家數變化", "持有變化", "delta")], perf)
    tabs.append(dict(
        id="s5", title="大師買進", task="buys", btn="更新 13F", stamp=_stamp(B),
        heading=f"價值投資人買進家數最多 20 檔" + (f" ({B['quarter']} 季)" if B else ""),
        desc=(f"{B['n_mgr']} 位價值型基金經理人的 SEC 13F。" if B else "") +
             "買進 = 新建倉，或持股較上季增加 5% 以上。「買進@」欄為各季買進家數，用來看趨勢；滑鼠移到「買進家數」可看買進者名單。",
        notes=["13F 在季底後最多 45 天才公布，只含美國上市股票的多頭部位，是落後資訊。",
               "已排除 ETF、選擇權，以及所有持有人都是新建倉的分拆新股。名單含 Dodge &amp; Cox、Boston Partners 等持股數百檔的大型資管，會墊高大型股的家數。"],
        table=bt if B else ""))

    M = (state.get("magic") or {}).get(uni)
    tabs.append(dict(
        id="s6", title="神奇公式", task="magic", btn="更新財報", stamp=_stamp(M),
        heading=f"神奇公式 Top 30 (Greenblatt) — {ul}",
        desc="盈餘殖利率(EBIT÷EV)與資本報酬率(EBIT÷投入資本)各自排名後加總，越小越好。" +
             (f"股票池 {M['counts'][0]} 檔 → 排除金融/公用事業 {M['counts'][1]} → 可排名 {M['counts'][2]}。" if M else ""),
        notes=["輕資產公司的資本報酬率可達數百%以上，在此公式中屬正常。", "Alphabet 等有兩種股票的公司各佔兩個名額。",
               f"原書股票池為全市場，這裡只用 {uh}，因此不是原汁原味的版本。財報抓不到的公司不會出現在排名中。"] +
              ([] if sp else ["Nasdaq 100 中沒有 GICS 板塊的少數公司(如 ASML、ARM)無法依板塊排除金融/公用事業；成分股只有約 100 檔，Top 30 已占三成。"]),
        table=table(M["res"], [("代號", "代號", "ticker"), ("Security", "公司", "text"), ("GICS Sector", "板塊", "text"),
                               ("市值(B)", "市值(B)", "num"), ("盈餘殖利率%", "盈餘殖利率", "plain"), ("資本報酬率%", "資本報酬率", "plain"),
                               ("EY名次", "EY名次", "int"), ("ROC名次", "ROC名次", "int"), ("總名次", "總名次", "int")], perf) if M else ""))

    mode = load_mode()
    N = (state.get("nav") or {}).get(mode)
    n_tk = len((N.get("tickers") or N.get("watchlist") or [])) if N else 0
    editor = ""
    if mode == "watchlist":
        miss = sorted(N["missing"]) if N and N.get("missing") else []
        miss = sorted(N["missing"]) if N and N.get("missing") else []
        wl_now = load_watchlist()
        editor += ('<div class="wlbar" id="wlbar"><span>自訂觀察清單：<b>%d</b> 檔</span>'
                   '<button type="button" id="wledit">編輯清單</button></div>' % len(wl_now))
        editor += ('<div class="wled" id="wled" hidden data-list="%s" data-missing="%s">'
                   '<div class="wlhead"><b>編輯自訂觀察清單</b><span id="wlcount"></span><span id="wldirty"></span></div>'
                   '<div class="chips" id="wlchips"></div>'
                   '<div class="wladd"><input id="wladd" type="text" spellcheck="false" autocomplete="off" '
                   'placeholder="輸入代號，可一次貼上多檔（逗號、空白、換行、全形標點都可以），按 Enter 加入">'
                   '<button type="button" id="wladdbtn">加入</button></div>'
                   '<div class="wlact"><button type="button" id="wlsave" class="primary">儲存並更新</button>'
                   '<button type="button" id="wlcancel">取消</button>'
                   '<button type="button" id="wlreset">還原</button><button type="button" id="wlclear">全部清除</button>'
                   '<span id="wlmsg"></span></div></div>'
                   % (esc(json.dumps(wl_now)), esc(json.dumps(miss))))
    else:
        src = "S&amp;P 500 成分股(來源: Wikipedia)" if mode == "sp500" else "Nasdaq 100 成分股(來源: Nasdaq 官方網站)"
        editor += (f'<p class="stamp">目前股票池：{src}；點「清單」欄的 ☆/★ 可把該股加入或移出自訂觀察清單'
                   '（切到「自訂觀察清單」並按「更新評級」後生效）。</p>')
    cl = load_cooling()
    editor += ('<div class="wl"><label>動能區間門檻：量化分數在股票池的分位低於 '
               f'<input id="cw" class="numin" type="number" min="2" max="99" value="{round(cl["warn"] * 100)}">% → 🔻警示；低於 '
               f'<input id="cr" class="numin" type="number" min="1" max="98" value="{round(cl["remove"] * 100)}">% → ❌建議剔除</label>'
               '<button type="button" id="cbsave">儲存門檻並更新</button><span id="cmsg"></span></div>')
    ncols = [("代號", "代號", "ticker")]
    if mode in ("ndx", "sp500"):
        ncols += [("公司", "公司", "co")]
        if mode == "sp500":
            ncols += [("板塊", "板塊", "co")]
        ncols += [("清單", "清單", "star")]
    ncols += [("綜合評級", "綜合", "text"), ("綜合分", "綜合分", "num"),
              ("基本面評級", "基本面", "text"), ("量化評級", "量化", "text"),
              ("Alpha/SD", "Alpha/SD", "num"), ("量化分位%", "量化分位", "plain"), ("Beta5Y", "Beta(5Y)", "num"),
              ("Alpha5Y%", "Alpha(5Y)", "plain"), ("動能", "動能", "text")]
    ntail = [("近期分數", "近期 Alpha/SD (舊→新)", "series")]   # 長文字放最後, 不擠掉股價表現欄
    fcols = [("代號", "代號", "ticker")] + [(k, lab, "num") for k, lab in FACTOR_LABELS]
    nbody = ""
    if N:
        rows_now = N["rows"].copy()      # ★ 以「現在的」清單為準(切換後重新整理也不會退回舊狀態), 不用更新當下存的值
        mine = set(load_watchlist())
        rows_now["清單"] = rows_now["代號"].map(lambda t: "★" if t in mine else "")
        nbody = (table(rows_now, ncols, perf, tail=ntail) +
                 '<h3>基本面因子明細 <span class="stamp">(滑鼠移到數字上可看該因子在股票池內的五分位 1–5，5 最好)</span></h3>' +
                 table(N["factors"], fcols, perf, with_perf=False, compact=True))
    label = MODES[mode]
    tabs.append(dict(
        id="s7", title="Navellier 評級", task="nav", btn="更新評級", stamp=_stamp(N), extra=editor,
        heading=f"Navellier 風格評級 — {label}" + (f" ({n_tk} 檔)" if N else ""),
        desc=(f"資料截至 {N['asof']} 那週收盤。" if N else "") +
             "綜合分 = 30% 基本面 + 70% 量化；量化 = 52 週週超額報酬的 Alpha ÷ 標準差(reward/risk)。評級 A 最好、E 最差。",
        notes=[f"評級是「{label}」內的相對排名(五分位)，不是絕對好壞" +
               ("；清單只有十幾檔時，A 只代表清單裡最強的前 20%。" if mode == "watchlist" else "；換股票池要重新更新。"),
               "資料不足 52 週(如近期上市)、或可計算的基本面因子少於 3 個(如部分外國公司)的股票不評級，以 N/A 顯示。" +
               ("金融業(銀行、保險等)沒有一般的營收/營業利益結構，可計算的因子較少，評級與其他產業的可比性較低。" if mode == "sp500" else ""),
               "與原書的差異：沒有「剔除軋空造成的 Alpha」(需空單資料，書中未公開細節)；基本面 8 個因子缺「分析師預估修正」(需付費資料)，只用其餘 7 個。",
               "動能區間是「水準」規則，是我們自訂的，書中沒有給數字門檻：量化分數的分位低於警示門檻 → 🔻，低於剔除門檻 → ❌(預設 60% / 40%，可在上方調整)。分位是相對於目前的股票池，自訂清單只有十幾檔時很粗略。",
               "為什麼不用「連續下滑幾週」：我們用 S&P 500 近 7 年回測，連降 2 週、3 週的出現頻率和純隨機一模一樣，被標記後的表現也不比平均差，已移除。分數水準較高者之後 13 週超額報酬較高(最高 20% +1.89%、最低 20% −0.30%)，但 2020–2022 年沒有效果，不是穩定規律；且含生存者偏差、未扣成本。「近期 Alpha/SD」欄僅供參考，過去幾週由歷史價格回算。",
               "只計算已收完的週(週六起才算)，同一週內重複更新結果相同。基本面首次需逐檔抓取(S&amp;P 500 約數分鐘)，之後 3 天內用快取；被 Yahoo 限流時已完成的部分會存檔，再按一次會接續。"] +
              ([f"價格資料缺漏: {', '.join(N['missing'])}"] if N and N["missing"] else []),
        table=nbody, notes_end=True))
    if uni is None:
        for t in tabs:
            if t["id"] in ("s1", "s2", "s3", "s4", "s6"):
                t.update(na=True, heading=t["title"], desc="", notes=[], table="", stamp="", btn="")
    order = ["s1", "s2", "s3", "s4", "s6", "s7", "s5"]        # 大師買進(s5)放最右邊
    tabs.sort(key=lambda t: order.index(t["id"]))
    return tabs


# ───────────────────────── 頁面 ─────────────────────────
CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1c2330;--mut:#667085;--line:#e4e7ec;--acc:#1f5fbf;--red:#d92d20;--green:#12805c;--note:#fff7e6;--noteb:#f1c76b}
@media(prefers-color-scheme:dark){:root{--bg:#0f141b;--card:#171e28;--fg:#e6eaf0;--mut:#98a2b3;--line:#2a3441;--acc:#6ea8ff;--red:#ff6b5e;--green:#3fcf9b;--note:#2a2415;--noteb:#6b5a1f}}
:root[data-color=tw]{--up:var(--red);--dn:var(--green)}
:root[data-color=us]{--up:var(--green);--dn:var(--red)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,"PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif}
header{padding:20px 16px 4px;max-width:1760px;margin:0 auto}
h1{margin:0;font-size:22px}
.sub{color:var(--mut);font-size:13px;margin-top:4px}
.bar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:12px}
button{border:1px solid var(--line);background:var(--card);color:var(--fg);padding:7px 12px;border-radius:8px;cursor:pointer;font:inherit}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
button:disabled{opacity:.45;cursor:not-allowed}
#msg{font-size:13px;color:var(--mut)}#msg.err{color:var(--red)}#msg.run{color:var(--acc)}
.spin{display:inline-block;width:11px;height:11px;border:2px solid var(--acc);border-right-color:transparent;border-radius:50%;animation:sp .8s linear infinite;vertical-align:-1px;margin-right:6px}
@keyframes sp{to{transform:rotate(360deg)}}
nav{display:flex;gap:6px;flex-wrap:wrap;padding:12px 16px;max-width:1760px;margin:0 auto}
nav button.dim{opacity:.5}
nav button.on{background:var(--acc);border-color:var(--acc);color:#fff}
main{max-width:1760px;margin:0 auto;padding:0 16px 40px}
section{display:none}section.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px;margin-bottom:12px}
.card .top{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;align-items:flex-start}
.card h2{margin:0 0 4px;font-size:17px}
.card p{margin:4px 0;color:var(--mut)}
.stamp{font-size:12px;color:var(--mut);margin-top:2px}
.note{background:var(--note);border:1px solid var(--noteb);border-radius:8px;padding:8px 12px;margin:8px 0;font-size:13px}
.wlbar{display:flex;gap:12px;align-items:center;margin:10px 0;color:var(--mut)}
.wlbar b{color:var(--fg)}
.wlbar[hidden],.wled[hidden]{display:none}
.wled{margin:10px 0;padding:12px 14px;border:1px solid var(--line);border-radius:10px;background:var(--bg)}
.wlhead{display:flex;gap:12px;align-items:baseline;margin-bottom:8px}
#wlcount{color:var(--mut);font-size:13px}#wldirty{color:var(--acc);font-size:12px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px;min-height:30px}
.chip{display:inline-flex;align-items:center;gap:2px;padding:3px 4px 3px 10px;border:1px solid var(--line);border-radius:999px;background:var(--card);font-weight:600;font-size:13px}
.chip.bad{border-color:var(--red);color:var(--red)}
.chip button{border:0;background:transparent;padding:0 6px;cursor:pointer;color:var(--mut);font-size:15px;line-height:1;border-radius:50%}
.chip button:hover{color:var(--red)}
.wladd{display:flex;gap:8px;margin-bottom:10px}
.wladd input{flex:1;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
.wlact{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
td.star{cursor:pointer;text-align:center;color:var(--mut);user-select:none;font-size:15px}
td.star.on{color:#e0a800}td.star:hover{color:var(--acc)}
#toast{position:fixed;right:16px;bottom:16px;max-width:380px;padding:10px 14px;background:var(--fg);color:var(--bg);border-radius:8px;font-size:13px;opacity:0;transition:opacity .2s;pointer-events:none;z-index:50}
#toast.show{opacity:.95}
.wl{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:10px 0}
.wl label{flex:1;min-width:260px;display:flex;gap:8px;align-items:center;color:var(--mut)}
.wl input.numin{flex:none;width:64px;margin:0 4px;text-align:right}
.wl input{flex:1;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);font:inherit}
#wlmsg,#cmsg{font-size:13px;color:var(--red)}
h3{font-size:15px;margin:22px 0 8px}
.empty{padding:28px;text-align:center;color:var(--mut);border:1px dashed var(--line);border-radius:12px}
.scroll{overflow:auto;max-height:620px;border:1px solid var(--line);border-radius:12px;background:var(--card)}
.scroll::-webkit-scrollbar{height:14px;width:14px}
.scroll::-webkit-scrollbar-corner{background:var(--bg)}
@supports not selector(::-webkit-scrollbar){.scroll{scrollbar-width:auto;scrollbar-color:var(--mut) var(--bg)}}
.scroll::-webkit-scrollbar-track{background:var(--bg)}
.scroll::-webkit-scrollbar-thumb{background:var(--mut);border-radius:7px;border:3px solid var(--bg)}
td.co{max-width:170px;overflow:hidden;text-overflow:ellipsis}
table{border-collapse:collapse;width:100%;min-width:760px}
.scroll.fit{display:inline-block;max-width:100%;vertical-align:top}
table.compact{width:auto;min-width:0}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th{position:sticky;top:0;z-index:2;background:var(--card);box-shadow:0 1px 0 var(--line);text-align:right;font-weight:600;cursor:pointer;user-select:none;font-size:13px}
th[data-k=t]{text-align:left}
th.asc::after{content:" ▲";color:var(--acc)}th.desc::after{content:" ▼";color:var(--acc)}
td.n{text-align:right;font-variant-numeric:tabular-nums}
td.tx{max-width:260px;overflow:hidden;text-overflow:ellipsis}
td.sr{font-size:12px;color:var(--mut);font-variant-numeric:tabular-nums}
td:first-child,td:nth-child(2){position:sticky;background:var(--card);z-index:1}
th:first-child,th:nth-child(2){z-index:3}
th:first-child,td:first-child{left:0;min-width:40px}
th:nth-child(2),td:nth-child(2){left:40px;border-right:1px solid var(--line)}
.filter{margin:0 0 8px;display:flex;gap:10px;align-items:center;font-size:13px;color:var(--mut)}
.filter input{width:320px;max-width:100%;padding:7px 10px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);font:inherit}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;overflow:hidden;margin:6px 0}
.seg button{border:0;border-radius:0;background:var(--card)}
.seg button.on{background:var(--acc);color:#fff}
td.na{text-align:right;color:var(--mut)}
td.rk{color:var(--mut)}
td.tk a{color:var(--acc);text-decoration:none;font-weight:600}
td.up{color:var(--up)}td.dn{color:var(--dn)}
tbody tr:hover{background:color-mix(in srgb,var(--acc) 8%,transparent)}
footer{max-width:1760px;margin:0 auto;padding:0 16px 40px;color:var(--mut);font-size:12px}
@media(max-width:600px){h1{font-size:19px}}
"""

JS = r"""
const $=(s,r=document)=>r.querySelector(s),$$=(s,r=document)=>[...r.querySelectorAll(s)];
const store={get(k){try{return localStorage.getItem(k)}catch(e){return null}},set(k,v){try{localStorage.setItem(k,v)}catch(e){}}};
const ROWS=15;
function fit(){$$('section.on .scroll').forEach(b=>{const rows=$$('tbody tr',b);if(rows.length<=ROWS){b.style.maxHeight='none';return}
  const th=$('thead',b).offsetHeight,rh=(rows.find(r=>r.offsetHeight)||rows[0]).offsetHeight;if(!rh)return;
  const sb=b.scrollWidth>b.clientWidth?14:0;   // 需要橫向捲動時, 捲軸(14px)也佔框高
  b.style.maxHeight=(th+rh*ROWS+2+sb)+'px'})}   // 表頭 + 15 列 + 框線 (+橫向捲軸); 超過的列在框內垂直捲動
function show(id){$$('section').forEach(s=>s.classList.toggle('on',s.id===id));$$('nav button').forEach(b=>b.classList.toggle('on',b.dataset.t===id));store.set('tab',id);history.replaceState(null,'','#tab-'+id);fit()}
$$('nav button').forEach(b=>b.onclick=()=>show(b.dataset.t));
show(location.hash.startsWith('#tab-')&&$('#'+location.hash.slice(5))?location.hash.slice(5):($('#'+store.get('tab'))?store.get('tab'):'s1'));
$$('table.sortable').forEach(t=>{$$('th',t).forEach((th,i)=>th.onclick=()=>{
  const dir=th.classList.contains('asc')?'desc':'asc';$$('th',t).forEach(x=>x.classList.remove('asc','desc'));th.classList.add(dir);
  const num=th.dataset.k==='n',tb=$('tbody',t),rows=$$('tr',tb);
  rows.sort((a,b)=>{const x=a.children[i].dataset.v,y=b.children[i].dataset.v;const c=num?(parseFloat(x)-parseFloat(y)):x.localeCompare(y);return dir==='asc'?c:-c});
  rows.forEach(r=>tb.appendChild(r))})});
$$('table.sortable').forEach(t=>{const rows=$$('tbody tr',t);if(rows.length<=60)return;
  const box=document.createElement('div');box.className='filter';box.innerHTML='<input type="search" placeholder="篩選: 輸入代號 / 公司 / 評級 / 板塊…" spellcheck="false"> <span></span>';
  t.parentElement.before(box);const inp=$('input',box),cnt=$('span',box);
  inp.oninput=()=>{const q=inp.value.trim().toLowerCase();let n=0;rows.forEach(r=>{const ok=!q||r.textContent.toLowerCase().includes(q);r.style.display=ok?'':'none';if(ok)n++});cnt.textContent=q?n+' / '+rows.length+' 列':''}});
window.addEventListener('load',fit);window.addEventListener('resize',fit);
const root=document.documentElement;
function applyColor(m){root.dataset.color=m;$('#cbtn').textContent=m==='us'?'配色: 綠漲紅跌':'配色: 紅漲綠跌'}
applyColor(store.get('color')||'tw');
$('#cbtn').onclick=()=>{const m=root.dataset.color==='tw'?'us':'tw';store.set('color',m);applyColor(m)};

// ── 更新按鈕 / 進度 ──
let wasRunning=false,timer=null;
function render(s){
  const msg=$('#msg');
  $$('[data-task],#wlsave,#cbsave').forEach(b=>b.disabled=s.running);
  if(s.running){const sec=Math.max(0,Math.round(Date.now()/1000-s.started));
    msg.className='run';msg.innerHTML='<span class="spin"></span>'+s.label+' — '+s.step+'（已 '+sec+' 秒）'}
  else if(s.error){msg.className='err';msg.textContent='上次更新有錯誤: '+s.error}
  else{msg.className='';msg.textContent=s.finished?'上次更新完成 '+new Date(s.finished*1000).toLocaleString('zh-TW'):''}
}
async function poll(){
  let s;try{s=await (await fetch('/api/status')).json()}catch(e){timer=setTimeout(poll,3000);return}
  render(s);
  if(s.running){wasRunning=true;timer=setTimeout(poll,1500)}
  else if(wasRunning){location.reload()}
}
$$('[data-task]').forEach(b=>b.onclick=async()=>{
  b.disabled=true;
  const r=await fetch('/api/update?task='+b.dataset.task,{method:'POST',headers:{'X-Requested-With':'dashboard'}});
  if(r.status===409)alert('已有更新在進行中');
  clearTimeout(timer);poll()});
$$('.seg button[data-mode]').forEach(b=>b.onclick=async()=>{
  if(b.classList.contains('on'))return;
  const r=await fetch('/api/universe',{method:'POST',headers:{'X-Requested-With':'dashboard','Content-Type':'application/json'},body:JSON.stringify({mode:b.dataset.mode})});
  if(r.ok)location.reload()});
const cbb=$('#cbsave');
if(cbb)cbb.onclick=async()=>{
  const msg=$('#cmsg');msg.textContent='';
  const r=await fetch('/api/cooling',{method:'POST',headers:{'X-Requested-With':'dashboard','Content-Type':'application/json'},body:JSON.stringify({warn:$('#cw').value,remove:$('#cr').value})});
  const j=await r.json();
  if(!r.ok){msg.textContent=j.error||'儲存失敗';return}
  const u=await fetch('/api/update?task=nav',{method:'POST',headers:{'X-Requested-With':'dashboard'}});
  if(u.status===409)alert('已有更新在進行中，門檻已儲存，稍後請按「更新評級」');
  clearTimeout(timer);poll()};
function toast(m){let t=$('#toast');if(!t){t=document.createElement('div');t.id='toast';document.body.appendChild(t)}
  t.textContent=m;t.classList.add('show');clearTimeout(t._h);t._h=setTimeout(()=>t.classList.remove('show'),4500)}
const JH={'X-Requested-With':'dashboard','Content-Type':'application/json'};

// ── 自訂觀察清單編輯器 (清單沒有檔數上限; 前後端都會驗證代號格式) ──
const wled=$('#wled');
if(wled){
  const saved0=JSON.parse(wled.dataset.list),miss=new Set(JSON.parse(wled.dataset.missing||'[]'));
  let saved=[...saved0],draft=[...saved0];
  const RE=/^[A-Z0-9][A-Z0-9-]{0,9}$/;
  const norm=t=>{t=t.normalize('NFKC').trim().replace(/^\$/,'').toUpperCase().replace(/\./g,'-');return RE.test(t)?t:null};
  const split=s=>s.normalize('NFKC').split('\n').flatMap(l=>l.split('#')[0].split(/[\s,;、]+/)).filter(Boolean);
  const dirty=()=>draft.length!==saved.length||draft.some((t,i)=>t!==saved[i]);
  const msg=(m,err)=>{const e=$('#wlmsg');e.textContent=m||'';e.style.color=err?'var(--red)':'var(--mut)'};
  function draw(){
    $('#wlchips').innerHTML=draft.length?draft.map(t=>'<span class="chip'+(miss.has(t)?' bad':'')+'"'+(miss.has(t)?' title="上次更新查不到價格資料"':'')+'>'+t+
      '<button type="button" data-rm="'+t+'" aria-label="移除 '+t+'">×</button></span>').join(''):'<span class="stamp">清單是空的，請在下方輸入代號</span>';
    $('#wlcount').textContent=draft.length+' 檔';
    $('#wldirty').textContent=dirty()?'● 有未儲存的變更':'';
    $('#wlsave').disabled=!draft.length;
  }
  function add(text){
    const good=[],bad=[],dup=[];
    for(const tok of split(text)){const t=norm(tok);
      if(!t)bad.push(tok);else if(draft.includes(t)||good.includes(t))dup.push(t);else good.push(t)}
    draft.push(...good);draw();
    const parts=[];
    if(good.length)parts.push('已加入 '+good.length+' 檔');
    if(dup.length)parts.push('已在清單中: '+dup.join('、'));
    if(bad.length)parts.push('無法辨識: '+bad.join('、'));
    msg(parts.join('；'),bad.length>0);
  }
  const inp=$('#wladd');
  inp.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();add(inp.value);inp.value=''}});
  inp.addEventListener('paste',()=>setTimeout(()=>{add(inp.value);inp.value=''},0));
  $('#wladdbtn').onclick=()=>{add(inp.value);inp.value='';inp.focus()};
  $('#wlchips').addEventListener('click',e=>{const t=e.target.dataset&&e.target.dataset.rm;if(!t)return;
    draft=draft.filter(x=>x!==t);draw();msg('')});
  const openEd=()=>{draft=[...saved];draw();msg('');wled.hidden=false;$('#wlbar').hidden=true;inp.focus()};
  const closeEd=()=>{wled.hidden=true;$('#wlbar').hidden=false};
  $('#wledit').onclick=openEd;
  $('#wlcancel').onclick=()=>{if(dirty()&&!confirm('放棄未儲存的變更?'))return;draft=[...saved];draw();msg('');closeEd()};
  $('#wlreset').onclick=()=>{draft=[...saved];draw();msg('已還原為上次儲存的清單')};
  $('#wlclear').onclick=()=>{if(draft.length&&confirm('清除全部 '+draft.length+' 檔?(按「儲存並更新」才會生效)')){draft=[];draw();msg('')}};
  $('#wlsave').onclick=async()=>{
    if(inp.value.trim()){add(inp.value);inp.value=''}
    if(!draft.length){msg('清單不能是空的',true);return}
    msg('儲存中…');
    const r=await fetch('/api/watchlist',{method:'POST',headers:JH,body:JSON.stringify({tickers:draft})});
    const j=await r.json().catch(()=>({}));
    if(!r.ok){msg(j.error||'儲存失敗',true);return}
    saved=[...j.tickers];draft=[...saved];draw();msg('');closeEd();   // 儲存後收起編輯器; 進度顯示在頁首
    const u=await fetch('/api/update?task=nav',{method:'POST',headers:{'X-Requested-With':'dashboard'}});
    if(u.status===409)alert('已有更新在進行中，清單已儲存，稍後請按「更新評級」');
    clearTimeout(timer);poll()};
  window.addEventListener('beforeunload',e=>{if(dirty()){e.preventDefault();e.returnValue=''}});
  draw();
}

// ── 大股票池表格: 點「清單」欄的 ☆/★ 加入/移出自訂觀察清單 ──
document.addEventListener('click',async e=>{
  const td=e.target.closest&&e.target.closest('td.star');if(!td)return;
  const t=td.dataset.tk;
  const r=await fetch('/api/watchlist/toggle',{method:'POST',headers:JH,body:JSON.stringify({ticker:t})});
  const j=await r.json().catch(()=>({}));
  if(!r.ok){toast(j.error||'操作失敗');return}
  $$('td.star[data-tk="'+t+'"]').forEach(c=>{c.classList.toggle('on',j.member);c.textContent=j.member?'★':'☆';c.dataset.v=j.member?1:0});
  toast((j.member?'已加入 ':'已移出 ')+t+'，自訂觀察清單現有 '+j.tickers.length+' 檔（切到「自訂觀察清單」並按「更新評級」後生效）');
});
poll();
"""


def build_page(state: dict) -> str:
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
<span id="msg"></span></div></header>
<nav>{nav}</nav><main>{"".join(secs)}</main>
<footer>資料來源: Wikipedia(成分股)、Yahoo Finance via yfinance(價格與基本面)、SEC EDGAR 13F-HR、OpenFIGI(代號對應)。
1M/3M/6M/1Y 為調整後收盤價(含股利與分割)的漲跌幅。「全部更新」會重新抓取目前所選股票池的所有資料，約需 10 分鐘，Yahoo 限流時可能失敗，失敗時會保留舊資料。
本頁為量化篩選結果，不構成投資建議；資料可能有延遲或錯誤，請自行核對。</footer>
<script>{JS}</script></body></html>"""
