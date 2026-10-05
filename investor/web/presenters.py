"""把 Navellier 評級結果整理成網頁表格用的 DataFrame (純函式, 不碰狀態與網路, 可單獨測試)。"""
import pandas as pd

# 因子明細表的欄位 (鍵, 標題); 鍵對應 navellier.fundamentals 的因子
FACTOR_LABELS = [("sales_yoy", "營收年增%"), ("margin_exp_yoy_pp", "營業利益率年增(pp)"), ("earn_yoy", "EPS年增%"),
                 ("earn_accel_streak", "盈餘動能(連續季數)"), ("earn_accel_pct", "盈餘動能(成長率變化%)"), ("surprise_avg", "財報驚喜均值%"),
                 ("fcf_yoy", "FCF年增%"), ("roe_ttm", "ROE(TTM)%"), ("est_revision", "預估修正%")]


TECH_COLS = ["RSI", "RSI_tip", "RSI區間", "趨勢", "距52週高%", "MACD", "技術評等", "財報日", "財報日_tip", "提示"]


def tech_cells(tech: dict, overall, cooling) -> dict:
    """技術面各欄位 (含提示文字)。tech: technicals.derive_technicals 的結果 (或含同名鍵的 dict, 可為空);
    overall: 綜合評級; cooling: 冷卻判定字串。評級頁與「只更新技術面」共用, 兩邊顯示一致。"""
    from investor.navellier import technicals
    rsi, zone = tech.get("rsi"), tech.get("rsi_zone")
    rsi_tip = "" if rsi is None else f"RSI {rsi:.1f}" + (" (過熱, >=70)" if zone == "過熱" else " (超賣, <=30)" if zone == "超賣" else "")
    nr, days = tech.get("next_report"), tech.get("days_to_report")
    nr_tip = "" if not nr else f"{nr} (倒數 {days} 天)" if days is not None else nr
    hints = technicals.hints(tech, fund_grade=overall, cooling_flag=cooling or "") if tech else []
    return {"RSI": rsi, "RSI_tip": rsi_tip, "RSI區間": zone, "趨勢": tech.get("trend"),
            "距52週高%": tech.get("off_high_pct"), "MACD": tech.get("macd_dir"), "技術評等": tech.get("tech_rating"),
            "財報日": nr, "財報日_tip": nr_tip, "提示": "；".join(hints)}


def apply_technicals(rows: pd.DataFrame, tech: dict) -> pd.DataFrame:
    """用新的技術面資料 {代號: derive_technicals 結果} 更新評級表的技術面欄位, 其餘欄位 (評級、基本面…) 原封不動。"""
    out = rows.copy()
    for i, r in out.iterrows():
        for k, v in tech_cells(tech.get(r["代號"]) or {}, r.get("綜合評級"), r.get("動能_tip")).items():
            out.at[i, k] = v
    return out


def navellier_rows(r: dict, mine: set) -> pd.DataFrame:
    """r: rating.run() 的結果; mine: 自訂觀察清單 (用來標 ★)。依綜合分由高到低排序。"""
    rows = []
    for t in r["tickers"]:
        c, ser = r["report"][t], r["series"].get(t, [])
        cool = c.get("cooling") or ""
        short = ("❌ 建議剔除" if cool.startswith("❌") else "🔻 冷卻警示" if cool.startswith("🔻")
                 else "✅ 正常" if cool.startswith("✅") else "—")
        ok = c.get("eligible")   # 資料不足 52 週者, 統計上不可靠的數字一律不顯示
        hist = " → ".join(f"{x:.2f}" for x in ser[-6:]) if ok else ""

        tc = tech_cells(c, c.get("overall"), c.get("cooling"))
        rows.append({"代號": t, "公司": r["names"].get(t, ""), "公司_tip": r["names"].get(t, ""), "板塊": r["sectors"].get(t, ""), "清單": "★" if t in mine else "", "綜合評級": c.get("overall"), "綜合分": c.get("combined"),
                     "基本面評級": c.get("fund_grade"),
                     "基本面評級_tip": f"基本面均分 {c['fund_avg']} (1–5，5 最好)" if c.get("fund_avg") else "",
                     "量化評級_tip": f"量化五分位 {c['quant_quintile']}/5" if c.get("quant_quintile") else "", "Alpha/SD": c.get("alpha_over_sd") if ok else None,
                     "量化評級": c.get("nav_grade"), "Beta5Y": c.get("beta_5y") if ok else None,
                     "Alpha5Y%": c.get("alpha_ann_5y_pct") if ok else None,
                     "動能": short, "動能_tip": cool, "量化分位%": c["nav_pct"] * 100 if c.get("nav_pct") is not None else None, "近期分數": hist, "近期分數_tip": hist,
                     **tc})
    df = pd.DataFrame(rows).sort_values("綜合分", ascending=False, na_position="last").reset_index(drop=True)
    return df


def navellier_factors(r: dict) -> pd.DataFrame:
    """基本面因子明細 (每檔一列); 值得說明的數字附上 _tip (提示文字), 虧損/轉盈/反彈等以 _txt 顯示文字。"""
    frows = []
    QKEY = {"earn_accel_streak": "earn_accel", "earn_accel_pct": "earn_accel"}   # 顯示欄位 -> 排名用因子
    STATE = {"earn_yoy": "earn_state", "fcf_yoy": "fcf_state"}                         # 虧損/轉盈狀態欄位
    ACCEL = ("earn_accel_streak", "earn_accel_pct")
    for t in r["tickers"]:
        row, fd = {"代號": t}, r["fundamentals"][t]
        gr = fd.get("earn_growth_pct")
        gtxt = "近 4 季 EPS 季增率: " + "、".join("—" if v is None else f"{v:+.0f}%" for v in gr) + "；" if gr else ""
        for key, _ in FACTOR_LABELS:
            q = r["factor_quintiles"].get(t, {}).get(QKEY.get(key, key))
            tip = f"分位 {q}/5" if q else ""
            st = fd.get(STATE.get(key, ""))
            v = fd.get(key)
            missing = v is None or v != v
            if key in ACCEL:
                st = "loss" if fd.get("earn_state") == "loss" else None
                case = fd.get("earn_accel_case")
                if key == "earn_accel_pct" and case in ("turnaround", "rebound", "declining"):
                    gp, gn = (gr[2], gr[3]) if gr else (None, None)
                    why = {"turnaround": ("轉盈", "前期 EPS 為負或零，成長率無法計算，本季已轉為正，視為最佳"),
                           "rebound": ("反彈", f"前一季成長率為負({gp:+.0f}%，EPS 較再前一季下滑)，最新季回升到 {gn:+.0f}%；"
                                              "前一季為負時，變化率的正負號會反、無法用百分比表示，視為最佳" if gp is not None and gn is not None else ""),
                           "declining": ("衰退中", f"前一季 {gp:+.0f}%、最新季 {gn:+.0f}%，EPS 連續兩季下滑" if gp is not None and gn is not None else "")}[case]
                    row[key + "_txt"], row[key + "_tip"] = why[0], gtxt + why[1] + "；" + tip
                    continue
            if st in ("loss", "turnaround"):
                row[key + "_txt"] = "虧損" if st == "loss" else "轉盈"
                row[key + "_tip"] = ("最新季虧損，視為最差；" if st == "loss" else "由虧轉盈，視為最佳；") + tip
                continue
            if missing:
                continue
            row[key] = v * 100 if key in ("sales_yoy", "earn_yoy", "fcf_yoy") else v   # 成長率為小數, 轉成 %
            if key == "earn_accel_streak":
                tip = f"連續 {int(v)} 季「盈餘成長率為正，且比前一季更高」；" + gtxt + tip
            elif key == "earn_accel_pct":
                gp, gn = (gr[2], gr[3]) if gr else (None, None)
                chg = (f"成長率由 {gp:+.0f}% 變為 {gn:+.0f}%，變化率 = ({gn:.0f}% − {gp:.0f}%) ÷ {gp:.0f}% = {v:+.0f}%；"
                       if gp is not None and gn is not None and gp > 0 else "")
                tip = chg + gtxt + tip
            elif key == "est_revision":
                days = fd.get("est_days")
                if days is not None:
                    tip = f"相隔 {days} 天的快照比較；" + tip
            if tip:
                row[key + "_tip"] = tip
        frows.append(row)
    return pd.DataFrame(frows)
