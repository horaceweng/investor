"""遠端存取用的簡易密碼登入 + 允許的主機名稱 (只用標準函式庫)。

預設(完全沒設定)時行為與以前相同: 只接受本機網址、不需登入。要讓外部裝置連進來:
  - 密碼:       環境變數 DASHBOARD_PASSWORD, 或檔案 data/config/dashboard_password.txt
  - 允許的網址: 環境變數 DASHBOARD_HOSTS (逗號分隔), 或檔案 data/config/allowed_hosts.txt, 例如 Tailscale 的 xxx.ts.net 主機名
  - Tailscale 帳號: 環境變數 DASHBOARD_TAILSCALE_USERS, 或檔案 data/config/tailscale_users.txt (登入名稱, 如 name@example.com)
    `tailscale serve` 會替來自 tailnet 的請求加上 Tailscale-User-Login 標頭 (公開的 Funnel 請求沒有); 設定後, 非本機網址的請求
    必須帶有符合的標頭才放行, 因此「只有登入你的 Tailscale 的裝置」才連得進來。此標頭只有在程式綁定本機(127.0.0.1)時才可信。
  - Tailscale 裝置: 環境變數 DASHBOARD_TAILSCALE_DEVICES, 或檔案 data/config/tailscale_devices.txt (裝置名稱, 如 iphone-14-pro-max)
    帳號只能辨識「人」; 設定裝置名單後, 程式會用 X-Forwarded-For (tailscale serve 帶入的發送者 Tailscale IP) 呼叫
    `tailscale whois` 查出是哪一台裝置, 不在名單內一律拒絕 (同帳號下的其他裝置也不行)。
設了額外主機名稱或對外綁定位址卻沒設密碼也沒設 Tailscale 帳號時, main.py 會拒絕啟動。
注意: 本程式只提供 HTTP, 密碼與 cookie 以明文傳輸; 對外請一律走有加密的通道 (Tailscale / HTTPS 反向代理 / SSH 通道)。
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from html import escape

from investor import paths

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
COOKIE = "dash_session"
SESSION_DAYS = 30
MAX_FAILS, LOCK_SECONDS = 5, 300


def _read(env, fname):
    v = os.environ.get(env, "").strip()
    if v:
        return v
    f = paths.CONFIG / fname
    return f.read_text().strip() if f.exists() else ""


def config():
    """回傳 (密碼, 額外允許的主機名稱, 允許的 Tailscale 帳號, 允許的 Tailscale 裝置名稱)。"""
    pw = _read("DASHBOARD_PASSWORD", "dashboard_password.txt")
    split = lambda t: {h.lower() for h in re.split(r"[,\s]+", t) if h}
    return (pw, split(_read("DASHBOARD_HOSTS", "allowed_hosts.txt")),
            split(_read("DASHBOARD_TAILSCALE_USERS", "tailscale_users.txt")),
            split(_read("DASHBOARD_TAILSCALE_DEVICES", "tailscale_devices.txt")))


_TS_BIN = None
_WHOIS_CACHE = {}          # ip -> (裝置名稱或 None, 過期時間)


def _tailscale_bin():
    global _TS_BIN
    if _TS_BIN is None:
        # launchd 的 PATH 很精簡, 所以逐一試常見安裝位置
        _TS_BIN = next((p for p in (shutil.which("tailscale"), "/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale",
                                    "/Applications/Tailscale.app/Contents/MacOS/Tailscale") if p and os.access(p, os.X_OK)), "")
    return _TS_BIN


def device_of(ip):
    """查出某個 Tailscale IP 屬於哪一台裝置 (回傳小寫的短名稱, 如 'iphone-14-pro-max'); 查不到回傳 None。結果快取 60 秒。"""
    if not re.fullmatch(r"[0-9a-fA-F.:]+", ip or ""):          # 只接受 IP 字元, 避免把任意字串丟給外部指令
        return None
    hit = _WHOIS_CACHE.get(ip)
    if hit and hit[1] > time.time():
        return hit[0]
    name = None
    try:
        out = subprocess.run([_tailscale_bin(), "whois", "--json", ip], capture_output=True, text=True, timeout=5)
        node = (json.loads(out.stdout) or {}).get("Node") or {}
        full = node.get("ComputedName") or node.get("Name") or ""
        name = full.split(".")[0].lower() or None
    except Exception:
        name = None                                             # 查不到 = 不放行 (失敗時一律拒絕)
    _WHOIS_CACHE[ip] = (name, time.time() + 60)
    return name


def _secret():
    f = paths.CONFIG / "session_secret"
    if not f.exists():
        paths.ensure_parent(f)
        fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(secrets.token_hex(32))
    return f.read_text().strip().encode()


def _sign(exp, password):
    pwfp = hashlib.sha256(password.encode()).hexdigest()           # 改密碼 = 所有舊 session 失效
    return hmac.new(_secret(), f"{exp}:{pwfp}".encode(), hashlib.sha256).hexdigest()


def make_cookie(password, secure=False):
    exp = int(time.time()) + SESSION_DAYS * 86400
    flags = "; Path=/; HttpOnly; SameSite=Strict; Max-Age=%d" % (SESSION_DAYS * 86400) + ("; Secure" if secure else "")
    return f"{COOKIE}={exp}.{_sign(exp, password)}{flags}"


def clear_cookie():
    return f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"


def valid_cookie(cookie_header, password):
    for part in (cookie_header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE and "." in v:
            exp, _, sig = v.partition(".")
            if exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(sig, _sign(exp, password)):
                return True
    return False


def check_password(given, password):
    return hmac.compare_digest(hashlib.sha256(given.encode()).digest(), hashlib.sha256(password.encode()).digest())


class Throttle:
    """連續輸錯密碼太多次就暫時鎖住 (全域計算; 經反向代理時所有請求的來源 IP 都一樣, 所以不分 IP)。"""
    def __init__(self):
        self.fails = []

    def locked_for(self):
        now = time.time()
        self.fails = [t for t in self.fails if now - t < LOCK_SECONDS]
        return int(self.fails[0] + LOCK_SECONDS - now) + 1 if len(self.fails) >= MAX_FAILS else 0

    def fail(self):
        self.fails.append(time.time())

    def ok(self):
        self.fails.clear()


def login_page(error=""):
    err = f'<p class="err">{escape(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>登入 - 選股儀表板</title>
<style>:root{{--bg:#f6f7f9;--card:#fff;--fg:#1c2330;--mut:#667085;--line:#e4e7ec;--acc:#1f5fbf;--red:#d92d20}}
@media(prefers-color-scheme:dark){{:root{{--bg:#0f141b;--card:#171e28;--fg:#e6eaf0;--mut:#98a2b3;--line:#2a3441;--acc:#6ea8ff;--red:#ff6b5e}}}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);font:15px/1.5 -apple-system,"PingFang TC","Noto Sans TC",sans-serif}}
form{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px;width:min(340px,90vw)}}
h1{{font-size:18px;margin:0 0 14px}}input{{width:100%;box-sizing:border-box;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);font:inherit}}
button{{margin-top:12px;width:100%;padding:10px;border:0;border-radius:8px;background:var(--acc);color:#fff;font:inherit;cursor:pointer}}
.err{{color:var(--red);font-size:13px;margin:8px 0 0}}.mut{{color:var(--mut);font-size:12px;margin-top:12px}}</style></head><body>
<form method="post" action="/login"><h1>選股儀表板</h1>
<input type="password" name="password" placeholder="密碼" autocomplete="current-password" autofocus required>{err}
<button type="submit">登入</button><div class="mut">登入狀態保留 {SESSION_DAYS} 天</div></form></body></html>"""
