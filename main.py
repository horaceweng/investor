#!/usr/bin/env python3
"""S&P 500 選股儀表板 - 本機網頁程式。

用法:
  .venv/bin/python main.py              # 啟動伺服器並開啟瀏覽器 (http://127.0.0.1:8765)
  .venv/bin/python main.py --port 9000 --no-open

網頁上的按鈕可更新資料; 更新在背景執行, 資料存於 data/state.pkl, 下次啟動直接顯示。
首次啟動若沒有資料, 會先用既有快取(基本面/財報)快速建立一份。
"""
import argparse
import json
import os
import pickle
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd

import portfolio_buys
import sp500_losers
import sp500_magic
import sp500_value
import auth
from dashboard_html import FACTOR_LABELS, build_page, performance
from navellier import run_weekly as nav_run

os.chdir(os.path.dirname(os.path.abspath(__file__)))  # 相對路徑(data/, 13f_cache/ 等)一律以專案目錄為準

DATA_DIR = "data"
STATE_FILE = os.path.join(DATA_DIR, "state.pkl")
QUARTER = None  # 13F 季底日期; None = 自動偵測最新一季 (可用 --quarter 手動指定)

state = {}                      # 各模組結果
state_lock = threading.Lock()
job = {"running": False, "task": "", "label": "", "step": "", "started": 0, "finished": 0, "error": None}
job_lock = threading.Lock()
AUTH_PW, EXTRA_HOSTS, TS_USERS, TS_DEVICES = auth.config()   # 密碼 / 額外主機名稱 / 允許的 Tailscale 帳號 / 允許的 Tailscale 裝置 (都沒設 = 只限本機、不需登入)
THROTTLE = auth.Throttle()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def save_state():
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "wb") as f:
        pickle.dump(state, f)
    os.replace(tmp, STATE_FILE)


def load_state():
    try:
        with open(STATE_FILE, "rb") as f:
            state.update(pickle.load(f))
    except Exception:
        pass
    for mod, marker in (("losers", "df"), ("value", "pe"), ("magic", "res")):   # 舊格式(單一股票池) -> 依股票池分開存
        if isinstance(state.get(mod), dict) and marker in state[mod]:
            state[mod] = {"sp500": state[mod]}
    if "rows" in (state.get("nav") or {}):      # 舊格式(單一股票池) -> 依股票池分開存
        state["nav"] = {"watchlist": state["nav"]}


# ───────────────────────── 各模組的更新動作 ─────────────────────────
def do_losers(cached, uni):
    df, base, last = sp500_losers.compute(13, 40, uni)
    state.setdefault("losers", {})[uni] = {"df": df, "base": base, "last": last, "ts": now()}


def do_value(cached, uni):
    r = sp500_value.compute(40, cached, uni)
    state.setdefault("value", {})[uni] = {**r, "ts": now()}


def do_buys(cached, uni):
    df, n_mgr, quarters = portfolio_buys.compute(QUARTER, 20, 4)
    state["buys"] = {"df": df, "n_mgr": n_mgr, "quarters": quarters, "quarter": quarters[-1], "ts": now()}


def do_magic(cached, uni):
    res, counts = sp500_magic.compute(30, cached, uni)
    state.setdefault("magic", {})[uni] = {"res": res, "counts": counts, "ts": now()}


def do_nav(cached, uni=None):
    mode = nav_run.load_mode()
    r = nav_run.run(verbose=False, mode=mode)
    mine = set(nav_run.load_watchlist())
    rows = []
    for t in r["tickers"]:
        c, ser = r["report"][t], r["series"].get(t, [])
        cool = c.get("cooling") or ""
        short = ("❌ 建議剔除" if cool.startswith("❌") else "🔻 冷卻警示" if cool.startswith("🔻")
                 else "✅ 正常" if cool.startswith("✅") else "—")
        ok = c.get("eligible")   # 資料不足 52 週者, 統計上不可靠的數字一律不顯示
        hist = " → ".join(f"{x:.2f}" for x in ser[-6:]) if ok else ""
        rows.append({"代號": t, "公司": r["names"].get(t, ""), "公司_tip": r["names"].get(t, ""), "板塊": r["sectors"].get(t, ""), "清單": "★" if t in mine else "", "綜合評級": c.get("overall"), "綜合分": c.get("combined"),
                     "基本面評級": c.get("fund_grade"),
                     "基本面評級_tip": f"基本面均分 {c['fund_avg']} (1–5，5 最好)" if c.get("fund_avg") else "",
                     "量化評級_tip": f"量化五分位 {c['quant_quintile']}/5" if c.get("quant_quintile") else "", "Alpha/SD": c.get("alpha_over_sd") if ok else None,
                     "量化評級": c.get("nav_grade"), "Beta5Y": c.get("beta_5y") if ok else None,
                     "Alpha5Y%": c.get("alpha_ann_5y_pct") if ok else None,
                     "動能": short, "動能_tip": cool, "量化分位%": c["nav_pct"] * 100 if c.get("nav_pct") is not None else None, "近期分數": hist, "近期分數_tip": hist})
    df = pd.DataFrame(rows).sort_values("綜合分", ascending=False, na_position="last").reset_index(drop=True)
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
            if tip:
                row[key + "_tip"] = tip
        frows.append(row)
    state.setdefault("nav", {})[mode] = {"rows": df, "factors": pd.DataFrame(frows), "asof": r["asof"],
                                         "missing": r["missing"], "tickers": r["tickers"], "ts": now()}


def do_perf(cached, uni=None):
    frames = []
    for v in (state.get("losers") or {}).values(): frames.append(v["df"])
    for v in (state.get("value") or {}).values(): frames += [v[k] for k in ("pe", "pb", "yield")]
    if "buys" in state: frames.append(state["buys"]["df"])
    for v in (state.get("magic") or {}).values(): frames.append(v["res"])
    for v in (state.get("nav") or {}).values(): frames.append(v["rows"])
    tickers = [t for f in frames for t in f["代號"]]
    state["perf"] = {"data": performance(tickers), "ts": now()}


POOL_SCREENS = {"losers", "value", "magic"}   # 依股票池計算的選股功能
STEPS = {"losers": ("13 週跌幅", do_losers), "value": ("價值面基本面", do_value),
         "buys": ("大師 13F", do_buys), "magic": ("神奇公式財報", do_magic),
         "nav": ("Navellier 評級", do_nav)}
TASKS = {
    "losers": ("更新股價與跌幅", ["losers"]),
    "value": ("更新基本面", ["value"]),
    "buys": ("更新 13F", ["buys"]),
    "magic": ("更新神奇公式財報", ["magic"]),
    "nav": ("更新 Navellier 評級", ["nav"]),
    "all": ("全部更新", ["losers", "value", "buys", "magic", "nav"]),
    "init": ("首次建立資料 (使用快取)", ["losers", "value", "buys", "magic", "nav"]),
}


def run_job(task):
    label, modules = TASKS[task]
    cached = task == "init"
    errors = []
    uni = nav_run.load_mode()         # 頁首選的股票池; 工作開始時鎖定, 中途切換不影響這次更新
    if uni == "watchlist":            # 自訂觀察清單: 跌幅/本益比/淨值比/殖利率/神奇公式不適用
        modules = [m for m in modules if m not in POOL_SCREENS]
    n = len(modules) + 1
    for i, m in enumerate(modules, 1):
        name, fn = STEPS[m]
        job["step"] = f"{i}/{n} {name}"
        try:
            fn(cached, uni)
            with state_lock:
                save_state()          # 每個模組成功就先存檔, 後面失敗也不會丟
        except Exception as e:
            errors.append(f"{name}: {str(e)[:300]}")
    job["step"] = f"{n}/{n} 計算 1M/3M/6M/1Y 股價表現"
    try:
        do_perf(cached, uni)
        with state_lock:
            save_state()
    except Exception as e:
        errors.append(f"股價表現: {str(e)[:300]}")
    job.update(running=False, finished=time.time(), error=" ｜ ".join(errors) or None, step="")


def start_job(task):
    with job_lock:
        if job["running"]:
            return False
        job.update(running=True, task=task, label=TASKS[task][0], step="準備中", started=time.time(), error=None)
    threading.Thread(target=run_job, args=(task,), daemon=True).start()
    return True


# ───────────────────────── HTTP ─────────────────────────
class Handler(BaseHTTPRequestHandler):
    server_version = "dashboard"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        # 只接受本機網址與明確設定過的主機名稱, 防止 DNS rebinding / 其他網站對本機服務發請求
        allowed = auth.LOOPBACK | EXTRA_HOSTS
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower() if not (self.headers.get("Host") or "").startswith("[") else "::1"
        origin = self.headers.get("Origin")
        if not (host in allowed and (origin is None or (urlparse(origin).hostname or "").lower() in allowed)):
            return False
        if host not in auth.LOOPBACK:                   # 經 tailscale serve 進來的請求
            if TS_USERS and (self.headers.get("Tailscale-User-Login") or "").strip().lower() not in TS_USERS:
                return False                            # 必須是指定的 Tailscale 帳號
            if TS_DEVICES:                              # 而且必須是指定的裝置 (由發送者的 Tailscale IP 反查)
                # 取「最後一段」: 反向代理(tailscaled)會把真實來源 IP 附加在最後; 前面各段是客戶端自己填的, 不可信
                ip = (self.headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
                if auth.device_of(ip) not in TS_DEVICES:
                    return False
        return True

    def _authed(self):
        return not AUTH_PW or auth.valid_cookie(self.headers.get("Cookie"), AUTH_PW)

    def _secure(self):
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def _redirect(self, where, cookie=None):
        self.send_response(303)
        self.send_header("Location", where)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, "forbidden", "text/plain")
        path = urlparse(self.path).path
        if AUTH_PW and path == "/login":
            return self._redirect("/") if self._authed() else self._send(200, auth.login_page())
        if AUTH_PW and path == "/logout":
            return self._redirect("/login", auth.clear_cookie())
        if not self._authed():
            if path.startswith("/api/"):
                return self._send(401, '{"error":"unauthorized"}', "application/json")
            return self._redirect("/login")
        if path == "/":
            with state_lock:
                page = build_page(state, logout=bool(AUTH_PW))
            return self._send(200, page)
        if path == "/api/status":
            return self._send(200, json.dumps(job), "application/json")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, "forbidden", "text/plain")
        u = urlparse(self.path)
        if AUTH_PW and u.path == "/login":                       # 表單登入 (沒有 X-Requested-With, 靠 Host/Origin 檢查與 SameSite cookie)
            wait = THROTTLE.locked_for()
            if wait:
                return self._send(429, auth.login_page(f"嘗試次數過多，請 {wait} 秒後再試"))
            n = min(int(self.headers.get("Content-Length") or 0), 2000)
            pw = (parse_qs(self.rfile.read(n).decode("utf-8", "replace")).get("password") or [""])[0]
            if auth.check_password(pw, AUTH_PW):
                THROTTLE.ok()
                return self._redirect("/", auth.make_cookie(AUTH_PW, self._secure()))
            THROTTLE.fail()
            return self._send(401, auth.login_page("密碼錯誤"))
        if self.headers.get("X-Requested-With") != "dashboard":
            return self._send(403, "forbidden", "text/plain")
        if not self._authed():
            return self._send(401, '{"error":"unauthorized"}', "application/json")
        if u.path in ("/api/watchlist", "/api/watchlist/toggle"):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > 500_000:             # 只是防止異常大的請求, 不限制檔數
                    raise ValueError("內容過長")
                body = json.loads(self.rfile.read(n) or b"{}")
                if u.path == "/api/watchlist/toggle":
                    tickers, member = nav_run.toggle_watchlist(str(body.get("ticker", "")))
                    return self._send(200, json.dumps({"tickers": tickers, "member": member}), "application/json")
                raw = body.get("tickers")
                text = "\n".join(str(x) for x in raw) if isinstance(raw, list) else str(body.get("text", ""))
                tickers = nav_run.parse_tickers(text)
                nav_run.save_watchlist(tickers)
            except (ValueError, json.JSONDecodeError) as e:
                return self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False), "application/json")
            return self._send(200, json.dumps({"tickers": tickers}), "application/json")
        if u.path == "/api/cooling":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                c = json.loads(self.rfile.read(min(n, 1000)) or b"{}")
                nav_run.save_cooling(float(c["warn"]) / 100, float(c["remove"]) / 100)
            except (ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
                return self._send(400, json.dumps({"error": f"門檻無效: {e}"}, ensure_ascii=False), "application/json")
            return self._send(200, json.dumps(nav_run.load_cooling()), "application/json")
        if u.path == "/api/universe":
            try:
                n = int(self.headers.get("Content-Length") or 0)
                nav_run.save_mode(str(json.loads(self.rfile.read(min(n, 1000)) or b"{}").get("mode", "")))
            except (ValueError, json.JSONDecodeError) as e:
                return self._send(400, json.dumps({"error": str(e)}, ensure_ascii=False), "application/json")
            return self._send(200, json.dumps({"mode": nav_run.load_mode()}), "application/json")
        if u.path != "/api/update":
            return self._send(404, "not found", "text/plain")
        task = (parse_qs(u.query).get("task") or [""])[0]
        if task not in TASKS or task == "init":
            return self._send(400, '{"error":"unknown task"}', "application/json")
        if all(m in POOL_SCREENS for m in TASKS[task][1]) and nav_run.load_mode() == "watchlist":
            return self._send(400, json.dumps({"error": "此功能不適用自訂觀察清單"}, ensure_ascii=False), "application/json")
        ok = start_job(task)
        self._send(202 if ok else 409, json.dumps({"started": ok}), "application/json")


def main():
    global QUARTER
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="綁定位址; 預設只限本機。非本機位址需先設定密碼")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--quarter", default=None, help="13F 季底日期; 省略則自動偵測最新一季")
    args = ap.parse_args()
    QUARTER = args.quarter

    load_state()
    if not state:
        print("尚無資料, 以快取建立初始資料 (約 1 分鐘)…")
        start_job("init")

    remote = args.host not in auth.LOOPBACK or bool(EXTRA_HOSTS)
    if remote and not (AUTH_PW or TS_USERS or TS_DEVICES):
        raise SystemExit("拒絕啟動: 已設定對外綁定位址或額外允許的主機名稱, 但沒有設密碼、也沒有指定 Tailscale 帳號。\n"
                         "請設定 DASHBOARD_PASSWORD / data/dashboard_password.txt, 或 DASHBOARD_TAILSCALE_USERS / data/tailscale_users.txt。")
    if args.host not in auth.LOOPBACK and not AUTH_PW:
        raise SystemExit("拒絕啟動: 綁定在非本機位址時一定要設密碼 (Tailscale 標頭在區網內可被偽造, 只在綁定本機時才可信)。")
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"儀表板已啟動: {url}   (Ctrl+C 結束)")
    if AUTH_PW or TS_USERS:
        print(f"  遠端存取: 密碼登入={'是' if AUTH_PW else '否'}; 限定 Tailscale 帳號={len(TS_USERS)} 個; 限定裝置={sorted(TS_DEVICES) or '不限'}; 額外允許的主機名稱={sorted(EXTRA_HOSTS) or '無'}")
    if args.host not in auth.LOOPBACK:
        print(f"  ⚠ 綁定在 {args.host}: 本程式只提供未加密的 HTTP, 請只在受信任的網路使用, 或改用 Tailscale serve / HTTPS 反向代理")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已結束")


if __name__ == "__main__":
    main()
