"""存取政策: 決定一個 HTTP 請求能不能進來。純函式, 不碰 socket, 可單獨測試。

三道檢查 (全部通過才放行):
1. Host / Origin 必須是本機網址, 或明確設定允許的主機名稱 (防 DNS rebinding、其他網站對本機服務發請求)。
2. 非本機網址 (= 經 tailscale serve 進來): 若設了 Tailscale 帳號名單, 標頭 Tailscale-User-Login 必須在名單內
   (公開的 Funnel 請求沒有這個標頭, 所以進不來)。
3. 若設了裝置名單, 發送者的 Tailscale IP 必須屬於名單內的裝置。IP 取 X-Forwarded-For 的「最後一段」: 反向代理
   (tailscaled) 會把真實來源 IP 附加在最後, 前面各段是客戶端自己填的, 不可信。
本機網址 (127.0.0.1 / localhost) 不需任何標頭, 本機使用不受影響。
"""
from urllib.parse import urlparse

from investor.web import auth


class AccessPolicy:
    def __init__(self, extra_hosts=(), ts_users=(), ts_devices=(), device_of=auth.device_of):
        self.allowed_hosts = auth.LOOPBACK | {h.lower() for h in extra_hosts}
        self.ts_users = {u.lower() for u in ts_users}
        self.ts_devices = {d.lower() for d in ts_devices}
        self._device_of = device_of

    @staticmethod
    def host_of(headers) -> str:
        host = headers.get("Host") or ""
        return "::1" if host.startswith("[") else host.rsplit(":", 1)[0].strip("[]").lower()

    def allows(self, headers) -> bool:
        host = self.host_of(headers)
        origin = headers.get("Origin")
        if not (host in self.allowed_hosts and
                (origin is None or (urlparse(origin).hostname or "").lower() in self.allowed_hosts)):
            return False
        if host in auth.LOOPBACK:
            return True
        if self.ts_users and (headers.get("Tailscale-User-Login") or "").strip().lower() not in self.ts_users:
            return False
        if self.ts_devices:
            ip = (headers.get("X-Forwarded-For") or "").split(",")[-1].strip()
            if self._device_of(ip) not in self.ts_devices:
                return False
        return True
