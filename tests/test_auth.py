"""密碼登入: session cookie 簽章、過期、竄改、改密碼使舊 session 失效、失敗鎖定。"""
import time
import unittest
from unittest import mock

from investor.web import auth
from tests.helpers import temp_data


class Cookie(unittest.TestCase):
    def test_roundtrip_and_flags(self):
        with temp_data():
            c = auth.make_cookie("pw", secure=True)
            self.assertIn("HttpOnly", c)
            self.assertIn("SameSite=Strict", c)
            self.assertIn("Secure", c)
            header = c.split(";")[0]
            self.assertTrue(auth.valid_cookie(header, "pw"))
            self.assertNotIn("Secure", auth.make_cookie("pw", secure=False))

    def test_wrong_password_or_tampering_rejected(self):
        with temp_data():
            header = auth.make_cookie("pw").split(";")[0]
            self.assertFalse(auth.valid_cookie(header, "other"))                 # 改密碼 -> 舊 session 失效
            name, _, val = header.partition("=")
            exp, _, sig = val.partition(".")
            self.assertFalse(auth.valid_cookie(f"{name}={int(exp) + 999}.{sig}", "pw"))   # 改期限
            self.assertFalse(auth.valid_cookie(f"{name}={exp}.{'0' * len(sig)}", "pw"))   # 偽造簽章
            self.assertFalse(auth.valid_cookie("", "pw"))
            self.assertFalse(auth.valid_cookie(None, "pw"))

    def test_expired_rejected(self):
        with temp_data():
            header = auth.make_cookie("pw").split(";")[0]
            with mock.patch.object(time, "time", return_value=time.time() + 31 * 86400):
                self.assertFalse(auth.valid_cookie(header, "pw"))

    def test_session_secret_file_is_private(self):
        with temp_data():
            auth.make_cookie("pw")
            f = auth.paths.CONFIG / "session_secret"
            self.assertTrue(f.exists())
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)


class PasswordAndThrottle(unittest.TestCase):
    def test_check_password(self):
        self.assertTrue(auth.check_password("secret", "secret"))
        self.assertFalse(auth.check_password("Secret", "secret"))
        self.assertFalse(auth.check_password("", "secret"))

    def test_lockout_after_repeated_failures(self):
        t = auth.Throttle()
        for _ in range(auth.MAX_FAILS - 1):
            t.fail()
        self.assertEqual(t.locked_for(), 0)
        t.fail()
        self.assertGreater(t.locked_for(), 0)
        t.ok()                                                                   # 登入成功即清除
        self.assertEqual(t.locked_for(), 0)

    def test_login_page_escapes_message(self):
        self.assertNotIn("<script>", auth.login_page("<script>alert(1)</script>"))


class Config(unittest.TestCase):
    def test_env_overrides_file_and_lists_are_lowercased(self):
        with temp_data():
            (auth.paths.CONFIG).mkdir(parents=True)
            (auth.paths.CONFIG / "allowed_hosts.txt").write_text("File.Host\n")
            with mock.patch.dict("os.environ", {"DASHBOARD_HOSTS": "Env.Host, other.host"}, clear=False):
                _, hosts, _, _ = auth.config()
            self.assertEqual(hosts, {"env.host", "other.host"})
            self.assertEqual(auth.config()[1], {"file.host"})


if __name__ == "__main__":
    unittest.main()
