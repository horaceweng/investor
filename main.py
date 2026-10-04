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
    for t in r["tickers"]:
        row = {"代號": t}
        for key, _ in FACTOR_LABELS:
            v = r["fundamentals"][t].get(key)
            if v is None or v != v:
                continue
            row[key] = v * 100 if key in ("sales_yoy", "earn_yoy", "fcf_yoy") else v   # 成長率為小數, 轉成 %
            q = r["factor_quintiles"].get(t, {}).get(key)
            if q:
                row[key + "_tip"] = f"分位 {q}/5"
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
        # 只接受本機網址, 防止 DNS rebinding / 其他網站對 localhost 發請求
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        return host in ("127.0.0.1", "localhost") and (
            origin is None or urlparse(origin).hostname in ("127.0.0.1", "localhost"))

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
        if path == "/":
            with state_lock:
                page = build_page(state)
            return self._send(200, page)
        if path == "/api/status":
            return self._send(200, json.dumps(job), "application/json")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self._host_ok() or self.headers.get("X-Requested-With") != "dashboard":
            return self._send(403, "forbidden", "text/plain")
        u = urlparse(self.path)
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
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--quarter", default=None, help="13F 季底日期; 省略則自動偵測最新一季")
    args = ap.parse_args()
    QUARTER = args.quarter

    load_state()
    if not state:
        print("尚無資料, 以快取建立初始資料 (約 1 分鐘)…")
        start_job("init")

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"儀表板已啟動: {url}   (Ctrl+C 結束)")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已結束")


if __name__ == "__main__":
    main()
