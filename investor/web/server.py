"""HTTP 伺服器: 只負責「收請求 -> 存取檢查/登入 -> 路由 -> 回應」; 實際的操作在 api.py, 頁面在 render/。

用法: .venv/bin/python main.py [--port 8765] [--no-open] [--host 127.0.0.1] [--quarter YYYY-MM-DD]
預設只綁定本機。對外存取 (Tailscale 帳號/裝置名單、密碼) 見 access.py 與 auth.py。
"""
import argparse
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from investor import paths
from investor.web import api, auth, jobs, steps, store
from investor.web.access import AccessPolicy
from investor.web.render import build_page


class Handler(BaseHTTPRequestHandler):
    server_version = "dashboard"
    # 由 main() 設定
    policy = AccessPolicy()
    password = ""                    # 空 = 不需登入
    throttle = auth.Throttle()

    def log_message(self, *a):
        pass

    # ── 回應工具 ──
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json")

    def _redirect(self, where, cookie=None):
        self.send_response(303)
        self.send_header("Location", where)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ── 驗證 ──
    def _authed(self):
        return not self.password or auth.valid_cookie(self.headers.get("Cookie"), self.password)

    def _secure(self):
        return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

    def _login(self):
        """表單登入 (沒有 X-Requested-With, 靠 Host/Origin 檢查與 SameSite cookie 防 CSRF)。"""
        wait = self.throttle.locked_for()
        if wait:
            return self._send(429, auth.login_page(f"嘗試次數過多，請 {wait} 秒後再試"))
        n = min(int(self.headers.get("Content-Length") or 0), 2000)
        pw = (parse_qs(self.rfile.read(n).decode("utf-8", "replace")).get("password") or [""])[0]
        if auth.check_password(pw, self.password):
            self.throttle.ok()
            return self._redirect("/", auth.make_cookie(self.password, self._secure()))
        self.throttle.fail()
        self._send(401, auth.login_page("密碼錯誤"))

    # ── 路由 ──
    def do_GET(self):
        if not self.policy.allows(self.headers):
            return self._send(403, "forbidden", "text/plain")
        path = urlparse(self.path).path
        if self.password and path == "/login":
            return self._redirect("/") if self._authed() else self._send(200, auth.login_page())
        if self.password and path == "/logout":
            return self._redirect("/login", auth.clear_cookie())
        if not self._authed():
            if path.startswith("/api/"):
                return self._json(401, {"error": "unauthorized"})
            return self._redirect("/login")
        if path == "/":
            with store.lock:
                page = build_page(store.state, logout=bool(self.password))
            return self._send(200, page)
        if path == "/api/status":
            return self._send(200, json.dumps(jobs.job), "application/json")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if not self.policy.allows(self.headers):
            return self._send(403, "forbidden", "text/plain")
        u = urlparse(self.path)
        if self.password and u.path == "/login":
            return self._login()
        if self.headers.get("X-Requested-With") != "dashboard":      # 擋掉其他網站的跨站請求
            return self._send(403, "forbidden", "text/plain")
        if not self._authed():
            return self._json(401, {"error": "unauthorized"})
        if u.path == "/api/update":
            return self._json(*api.update((parse_qs(u.query).get("task") or [""])[0]))
        route = api.JSON_POST.get(u.path)
        if route is None:
            return self._send(404, "not found", "text/plain")
        fn, limit = route
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                raise ValueError("內容過長")
            self._json(*fn(json.loads(self.rfile.read(n) or b"{}")))
        except ValueError as e:                  # 含 json.JSONDecodeError
            self._json(400, {"error": str(e)})


def main(argv=None):
    ap = argparse.ArgumentParser(description="選股儀表板 (本機網頁程式)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="綁定位址; 預設只限本機。非本機位址需先設定密碼")
    ap.add_argument("--no-open", action="store_true")
    ap.add_argument("--quarter", default=None, help="13F 季底日期; 省略則自動偵測最新一季")
    args = ap.parse_args(argv)
    steps.QUARTER = args.quarter

    moved = paths.migrate_legacy()               # 舊版(平鋪結構)的資料自動搬到 data/ 新位置, 只做一次
    if moved:
        print(f"已將 {len(moved)} 個舊位置的資料檔搬到新結構 (data/)")

    password, extra_hosts, ts_users, ts_devices = auth.config()
    remote = args.host not in auth.LOOPBACK or bool(extra_hosts)
    if remote and not (password or ts_users or ts_devices):
        raise SystemExit("拒絕啟動: 已設定對外綁定位址或額外允許的主機名稱, 但沒有設密碼、也沒有指定 Tailscale 帳號或裝置。\n"
                         "請設定 DASHBOARD_PASSWORD / data/config/dashboard_password.txt, 或 DASHBOARD_TAILSCALE_USERS / "
                         "data/config/tailscale_users.txt。")
    if args.host not in auth.LOOPBACK and not password:
        raise SystemExit("拒絕啟動: 綁定在非本機位址時一定要設密碼 (Tailscale 標頭在區網內可被偽造, 只在綁定本機時才可信)。")
    Handler.policy = AccessPolicy(extra_hosts, ts_users, ts_devices)
    Handler.password = password

    store.load()
    if not store.state:
        print("尚無資料, 以快取建立初始資料 (約 1 分鐘)…")
        jobs.start_job("init")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"儀表板已啟動: {url}   (Ctrl+C 結束)")
    if password or ts_users:
        print(f"  遠端存取: 密碼登入={'是' if password else '否'}; 限定 Tailscale 帳號={len(ts_users)} 個; "
              f"限定裝置={sorted(ts_devices) or '不限'}; 額外允許的主機名稱={sorted(extra_hosts) or '無'}")
    if args.host not in auth.LOOPBACK:
        print(f"  ⚠ 綁定在 {args.host}: 本程式只提供未加密的 HTTP, 請只在受信任的網路使用, 或改用 Tailscale serve / HTTPS 反向代理")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已結束")
