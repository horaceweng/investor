"""各分頁的內容 (標題、說明、表格欄位): 每個分頁一個函式, 回傳 dict 交給 page.build_page 組裝。

dict 欄位: id / title / task(更新按鈕對應的工作) / btn / stamp / heading / desc / notes / table [/ extra / notes_end / na]
"""
import html
import json

from investor.navellier.settings import MODES, load_cooling, load_mode, load_watchlist
from investor.universe import UNIVERSES
from investor.web.presenters import FACTOR_LABELS
from investor.web.render.cells import esc, table


class Ctx:
    """頁首選定的股票池 (sp500 / ndx / watchlist) 與衍生的顯示用資訊。"""

    def __init__(self, state: dict):
        self.perf = (state.get("perf") or {}).get("data", {})
        self.mode = load_mode()
        self.uni = self.mode if self.mode in UNIVERSES else None     # 自訂觀察清單: 五個選股分頁不適用
        self.label = UNIVERSES.get(self.uni, "")                      # 顯示用名稱
        self.label_html = html.escape(self.label)                     # S&P 500 -> S&amp;P 500
        self.sp = self.uni == "sp500"


def _stamp(mod):
    return f'資料更新於 {mod["ts"]}' if mod else "尚無資料"


def losers_tab(state, c):
    ul, uh, sp, perf, uni = c.label, c.label_html, c.sp, c.perf, c.uni
    split_note = ("⚠ CTVA、HONA、FDXF 的跌幅疑似受分拆影響而失真，請先核對。" if sp
                  else "⚠ HON、HONA 等近期分拆/改名的公司，價格歷史可能失真，請先核對。")
    L = (state.get("losers") or {}).get(uni)
    return dict(
        id="s1", title="13週跌幅", task="losers", btn="更新股價與跌幅", stamp=_stamp(L),
        heading=f"過去 13 週跌幅最大 40 檔 — {ul}" + (f" ({L['base']} → {L['last']})" if L else ""),
        desc=f"{uh} 成分股，13 週調整後收盤價漲跌幅由低到高。",
        notes=[split_note],
        table=table(L["df"], [("代號", "代號", "ticker"), ("Security", "公司", "text"), ("GICS Sector", "板塊", "text"),
                              ("起始價", "13週前", "num"), ("最新價", "最新價", "num"), ("13週%", "13週", "pct")], perf) if L else "")


def value_tabs(state, c):
    """本益比最低 / 淨值比最低 / 殖利率最高 三個分頁 (共用同一份基本面資料)。"""
    ul, sp, perf, uni = c.label, c.sp, c.perf, c.uni
    tabs = []
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
    return tabs


def buys_tab(state, c):
    perf = c.perf
    B = state.get("buys")
    if B:
        qcols = [(f"買進@{q[:7]}", f"買進@{q[2:7]}", "int") for q in B["quarters"][1:]]
        bt = table(B["df"], [("代號", "代號", "ticker"), ("公司", "公司", "text"), ("買進家數", "買進家數", "int")] + qcols +
                   [("新建倉", "新建倉", "int"), ("持有家數", "持有家數", "int"), ("持有家數變化", "持有變化", "delta")], perf)
    return dict(
        id="s5", title="大師買進", task="buys", btn="更新 13F", stamp=_stamp(B),
        heading="價值投資人買進家數最多 20 檔" + (f" ({B['quarter']} 季)" if B else ""),
        desc=(f"{B['n_mgr']} 位價值型基金經理人的 SEC 13F。" if B else "") +
             "買進 = 新建倉，或持股較上季增加 5% 以上。「買進@」欄為各季買進家數，用來看趨勢；滑鼠移到「買進家數」可看買進者名單。",
        notes=["13F 在季底後最多 45 天才公布，只含美國上市股票的多頭部位，是落後資訊。",
               "已排除 ETF、選擇權，以及所有持有人都是新建倉的分拆新股。名單含 Dodge &amp; Cox、Boston Partners 等持股數百檔的大型資管，會墊高大型股的家數。"],
        table=bt if B else "")


def magic_tab(state, c):
    ul, uh, sp, perf, uni = c.label, c.label_html, c.sp, c.perf, c.uni
    M = (state.get("magic") or {}).get(uni)
    return dict(
        id="s6", title="神奇公式", task="magic", btn="更新財報", stamp=_stamp(M),
        heading=f"神奇公式 Top 30 (Greenblatt) — {ul}",
        desc="盈餘殖利率(EBIT÷EV)與資本報酬率(EBIT÷投入資本)各自排名後加總，越小越好。" +
             (f"股票池 {M['counts'][0]} 檔 → 排除金融/公用事業 {M['counts'][1]} → 可排名 {M['counts'][2]}。" if M else ""),
        notes=["輕資產公司的資本報酬率可達數百%以上，在此公式中屬正常。", "Alphabet 等有兩種股票的公司各佔兩個名額。",
               f"原書股票池為全市場，這裡只用 {uh}，因此不是原汁原味的版本。財報抓不到的公司不會出現在排名中。"] +
              ([] if sp else ["Nasdaq 100 中沒有 GICS 板塊的少數公司(如 ASML、ARM)無法依板塊排除金融/公用事業；成分股只有約 100 檔，Top 30 已占三成。"]),
        table=table(M["res"], [("代號", "代號", "ticker"), ("Security", "公司", "text"), ("GICS Sector", "板塊", "text"),
                               ("市值(B)", "市值(B)", "num"), ("盈餘殖利率%", "盈餘殖利率", "plain"), ("資本報酬率%", "資本報酬率", "plain"),
                               ("EY名次", "EY名次", "int"), ("ROC名次", "ROC名次", "int"), ("總名次", "總名次", "int")], perf) if M else "")


def _watchlist_editor(mode, N):
    """分頁上方的設定區: 自訂清單的編輯器(平常只顯示一行摘要, 按「編輯清單」才展開) 或股票池說明, 加上動能門檻。"""
    editor = ""
    if mode == "watchlist":
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
    return editor


def _navellier_columns(mode):
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

    # 技術面欄位 (日線, 僅供參考, 不影響評級)
    tech_cols = [("RSI", "RSI", "num"), ("RSI區間", "RSI區間", "text"), ("趨勢", "趨勢", "tone"), ("距52週高%", "距高%", "num"),
                 ("MACD", "MACD", "tone"), ("技術評等", "技術評等", "tone"), ("提示", "提示", "text")]
    ncols += tech_cols

    ntail = [("近期分數", "近期 Alpha/SD (舊→新)", "series")]   # 長文字放最後, 不擠掉股價表現欄
    fcols = [("代號", "代號", "ticker")] + [(k, lab, "num") for k, lab in FACTOR_LABELS]
    fcols += [("下次財報", "下次財報", "text"), ("最新財報", "最新財報", "text"),     # 財報相關屬基本面
              ("EPS驚喜%", "最新季EPS驚喜", "pct"), ("營收驚喜%", "最新季營收驚喜", "pct"), ("財報結果", "結果", "tone")]
    return ncols, ntail, fcols


def _navellier_body(N, mode, perf):
    """評級表 + 基本面因子明細表。★ 以「現在的」清單為準 (切換後重新整理也不會退回舊狀態), 不用更新當下存的值。"""
    ncols, ntail, fcols = _navellier_columns(mode)
    nbody = ""
    if N:
        rows_now = N["rows"].copy()      # ★ 以「現在的」清單為準(切換後重新整理也不會退回舊狀態), 不用更新當下存的值
        mine = set(load_watchlist())
        rows_now["清單"] = rows_now["代號"].map(lambda t: "★" if t in mine else "")
        nbody = (table(rows_now, ncols, perf, tail=ntail) +
                 '<h3>基本面因子明細 <span class="stamp">(滑鼠移到數字上可看該因子在股票池內的五分位 1–5，5 最好)</span></h3>' +
                 table(N["factors"], fcols, perf, with_perf=False, compact=True))
    return nbody


def navellier_tab(state, c):
    mode, perf = c.mode, c.perf
    N = (state.get("nav") or {}).get(mode)
    n_tk = len((N.get("tickers") or N.get("watchlist") or [])) if N else 0
    editor = _watchlist_editor(mode, N)
    nbody = _navellier_body(N, mode, perf)
    label = MODES[mode]
    return dict(
        id="s7", title="Navellier 評級", task="nav", btn="更新評級", stamp=_stamp(N), extra=editor,
        heading=f"Navellier 風格評級 — {label}" + (f" ({n_tk} 檔)" if N else ""),
        desc=(f"資料截至 {N['asof']} 那週收盤；技術面欄位更新於 {N.get('tech_ts') or '—'}。" if N else "") +
             "綜合分 = 30% 基本面 + 70% 量化；量化 = 52 週週超額報酬的 Alpha ÷ 標準差(reward/risk)。評級 A 最好、E 最差。",
        notes=[f"評級是「{label}」內的相對排名(五分位)，不是絕對好壞" +
               ("；清單只有十幾檔時，A 只代表清單裡最強的前 20%。" if mode == "watchlist" else "；換股票池要重新更新。"),
               "資料不足 52 週(如近期上市)、或可計算的基本面因子少於 3 個(如部分外國公司)的股票不評級，以 N/A 顯示。" +
               ("金融業(銀行、保險等)沒有一般的營收/營業利益結構，可計算的因子較少，評級與其他產業的可比性較低。" if mode == "sp500" else ""),
               "基本面「盈餘動能」(原書：連續幾季逐漸加大的盈餘正向變化)：一階導數 = 盈餘成長率(本季 EPS ÷ 上季 EPS − 1)；二階導數 = 成長率的變化率 = (最新季成長率 − 前一季成長率) ÷ 前一季成長率，例如成長率由 +347% 變為 +91% 是 −74%；連續季數 = 連續幾季「成長率為正且比前一季更高」(0–3)，同分再比二階導數；需連續 5 季資料。滑鼠移到數字上可看近 4 季的成長率。最新一季虧損者，EPS/FCF 年增視為最差，由虧轉盈視為最佳，只有真正缺資料才略過；可計算因子少於 5 個不評級。",
               "與原書的差異：沒有「剔除軋空造成的 Alpha」(需空單資料，書中未公開細節)；基本面第 8 個因子「分析師預估修正」沒有現成歷史資料，是由我們每次更新時存下 TradingView 的共識 EPS 快照 (data/navellier/estimates.jsonl)，再與約 28 天前比較；快照至少要累積 7 天才有值，在那之前這個因子不計入。",
               "自動更新：開啟程式或重新整理頁面時會檢查——技術面每個交易日自動更新一次（只抓一次 TradingView，不影響評級）；評級每週自動更新一次（新的一週收盤後）。資料已是最新就不會上網；失敗時會隔一陣子再試，並保留舊資料。技術面欄位為取得當下的日線值，僅供參考，不影響評級。",
               "動能區間是「水準」規則，是我們自訂的，書中沒有給數字門檻：量化分數的分位低於警示門檻 → 🔻，低於剔除門檻 → ❌(預設 60% / 40%，可在上方調整)。分位是相對於目前的股票池，自訂清單只有十幾檔時很粗略。",
               "為什麼不用「連續下滑幾週」：我們用 S&P 500 近 7 年回測，連降 2 週、3 週的出現頻率和純隨機一模一樣，被標記後的表現也不比平均差，已移除。分數水準較高者之後 13 週超額報酬較高(最高 20% +1.89%、最低 20% −0.30%)，但 2020–2022 年沒有效果，不是穩定規律；且含生存者偏差、未扣成本。「近期 Alpha/SD」欄僅供參考，過去幾週由歷史價格回算。",
               "只計算已收完的週(週六起才算)，同一週內重複更新結果相同。基本面首次需逐檔抓取(S&amp;P 500 約數分鐘)，之後 3 天內用快取；被 Yahoo 限流時已完成的部分會存檔，再按一次會接續。"] +
              ([f"價格資料缺漏: {', '.join(N['missing'])}"] if N and N["missing"] else []),
        table=nbody, notes_end=True)


def make_tabs(state: dict) -> list:
    c = Ctx(state)
    tabs = [losers_tab(state, c), *value_tabs(state, c), buys_tab(state, c), magic_tab(state, c), navellier_tab(state, c)]
    if c.uni is None:
        for t in tabs:
            if t["id"] in ("s1", "s2", "s3", "s4", "s6"):
                t.update(na=True, heading=t["title"], desc="", notes=[], table="", stamp="", btn="")
    order = ["s1", "s2", "s3", "s4", "s6", "s7", "s5"]        # 大師買進(s5)放最右邊
    tabs.sort(key=lambda t: order.index(t["id"]))
    return tabs
