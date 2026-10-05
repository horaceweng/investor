"""存取政策: 重點是「只放行指定的 Tailscale 帳號 + 裝置」, 以及曾經出現過的 X-Forwarded-For 偽造漏洞。"""
import unittest

from investor.web.access import AccessPolicy

HOST = "mini.example.ts.net"
USER = "me@example.com"
DEVICES = {"100.1.1.1": "iphone", "100.1.1.2": "macbook", "100.1.1.3": "linux-box", "100.1.1.9": "mini"}


def policy(**kw):
    return AccessPolicy(extra_hosts=[HOST], ts_users=[USER], ts_devices=["iphone", "macbook"],
                        device_of=lambda ip: DEVICES.get(ip), **kw)


def req(host=HOST, user=USER, xff=None, origin=None):
    h = {"Host": host}
    if user is not None:
        h["Tailscale-User-Login"] = user
    if xff is not None:
        h["X-Forwarded-For"] = xff
    if origin is not None:
        h["Origin"] = origin
    return h


class Localhost(unittest.TestCase):
    def test_local_needs_no_headers(self):
        p = policy()
        self.assertTrue(p.allows({"Host": "127.0.0.1:8765"}))
        self.assertTrue(p.allows({"Host": "localhost:8765"}))
        self.assertTrue(p.allows({"Host": "[::1]:8765"}))

    def test_default_policy_is_local_only(self):
        p = AccessPolicy()
        self.assertTrue(p.allows({"Host": "localhost:8765"}))
        self.assertFalse(p.allows({"Host": HOST}))

    def test_foreign_origin_blocked_even_locally(self):
        self.assertFalse(policy().allows({"Host": "localhost:8765", "Origin": "https://evil.com"}))
        self.assertTrue(policy().allows({"Host": "localhost:8765", "Origin": "http://localhost:8765"}))


class Remote(unittest.TestCase):
    def test_allowed_devices_pass(self):
        self.assertTrue(policy().allows(req(xff="100.1.1.1")))      # iPhone
        self.assertTrue(policy().allows(req(xff="100.1.1.2")))      # MacBook Pro

    def test_other_devices_of_same_account_blocked(self):
        self.assertFalse(policy().allows(req(xff="100.1.1.3")))     # 同帳號的 Linux 主機
        self.assertFalse(policy().allows(req(xff="100.1.1.9")))     # 伺服器本機自己經 tailnet 網址

    def test_missing_or_unknown_source_blocked(self):
        self.assertFalse(policy().allows(req(xff=None)))
        self.assertFalse(policy().allows(req(xff="8.8.8.8")))
        self.assertFalse(policy().allows(req(xff="")))

    def test_wrong_or_missing_account_blocked(self):                # 公開的 Funnel 請求沒有帳號標頭
        self.assertFalse(policy().allows(req(user=None, xff="100.1.1.1")))
        self.assertFalse(policy().allows(req(user="other@example.com", xff="100.1.1.1")))

    def test_account_is_case_insensitive(self):
        self.assertTrue(policy().allows(req(user=USER.upper(), xff="100.1.1.1")))

    def test_unknown_host_blocked(self):
        self.assertFalse(policy().allows(req(host="evil.com", xff="100.1.1.1")))
        self.assertFalse(policy().allows(req(host="100.90.1.1:8443", xff="100.1.1.1")))      # 用 IP 連 (憑證/Host 都對不上)

    def test_remote_with_foreign_origin_blocked(self):
        self.assertFalse(policy().allows(req(xff="100.1.1.1", origin="https://evil.com")))
        self.assertTrue(policy().allows(req(xff="100.1.1.1", origin=f"https://{HOST}:8443")))


class ForwardedForSpoofing(unittest.TestCase):
    """回歸測試: 曾經取 X-Forwarded-For 的「第一段」, 同帳號的 Linux 主機可冒充 iPhone。
    反向代理把真實來源附加在最後, 前面各段由客戶端控制, 一律不可信。"""

    def test_attacker_cannot_prepend_an_allowed_ip(self):
        self.assertFalse(policy().allows(req(xff="100.1.1.1, 100.1.1.3")))     # 偽造 iPhone, 真實是 Linux
        self.assertFalse(policy().allows(req(xff="100.1.1.2, 100.1.1.3")))

    def test_only_the_last_hop_counts(self):
        self.assertTrue(policy().allows(req(xff="100.1.1.3, 100.1.1.1")))      # 前面是什麼都沒關係, 最後是 iPhone


if __name__ == "__main__":
    unittest.main()
