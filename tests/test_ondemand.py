"""隨需啟動: 閒置才結束 (有請求、有更新在跑、有到點的排程都不結束); 退避時間存檔後重啟仍記得。"""
import time
import unittest
from unittest import mock

from investor import paths
from investor.web import autorefresh, jobs, ondemand
from tests.helpers import temp_data


class Stop(Exception):
    pass


class Watchdog(unittest.TestCase):
    def setUp(self):
        self.srv = mock.Mock()
        self.p = [mock.patch.object(ondemand.autorefresh, "scheduled_start", return_value=None),
                  mock.patch.object(ondemand.time, "sleep", side_effect=Stop),       # 只跑一輪
                  mock.patch.dict(jobs.job, {"running": False})]
        self.sched = self.p[0].start()
        for p in self.p[1:]:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.p])

    def run_once(self, idle_secs, **kw):
        ondemand._last_seen = time.time() - idle_secs
        try:
            ondemand.watchdog(self.srv, idle=900, **kw)
        except Stop:
            pass

    def test_exits_when_idle(self):
        self.run_once(901)
        self.srv.shutdown.assert_called_once()

    def test_recent_request_keeps_alive(self):
        self.run_once(100)
        self.srv.shutdown.assert_not_called()

    def test_running_job_keeps_alive(self):
        jobs.job["running"] = True
        self.run_once(5000)
        self.srv.shutdown.assert_not_called()

    def test_scheduled_update_keeps_alive(self):
        self.sched.return_value = ("nav", "ndx")
        self.run_once(5000)
        self.srv.shutdown.assert_not_called()

    def test_not_launchd_never_exits(self):
        self.run_once(5000, exit_when_idle=False)
        self.srv.shutdown.assert_not_called()

    def test_not_launched_by_launchd(self):
        self.assertIsNone(ondemand.activated_socket())


class PersistTries(unittest.TestCase):
    def test_backoff_survives_restart(self):
        with temp_data(), mock.patch.object(autorefresh, "PERSIST", True):
            autorefresh._last_try.clear()
            autorefresh._remember(("nav", "sp500"), 1234.0)
            self.assertTrue(paths.AUTOREFRESH_TRIES.exists())
            autorefresh._last_try.clear()
            autorefresh.load_tries()
            self.assertEqual(autorefresh._last_try, {("nav", "sp500"): 1234.0})
        autorefresh._last_try.clear()


if __name__ == "__main__":
    unittest.main()
