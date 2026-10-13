"""自動更新: 什麼時候該更新、什麼時候絕對不上網; 只更新技術面不動評級; 快照同日合併。"""
import unittest
from datetime import date, datetime
from unittest import mock

import pandas as pd

from investor.navellier import alpha_beta, estimates, rating
from investor.web import autorefresh, presenters, steps, store
from tests.helpers import temp_data

FRI, SAT, MON, WED = date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 5), date(2026, 10, 7)


class LastTradingDay(unittest.TestCase):
    def test_weekend_uses_friday(self):
        self.assertEqual(autorefresh.last_trading_day(SAT), FRI)
        self.assertEqual(autorefresh.last_trading_day(date(2026, 10, 4)), FRI)
        self.assertEqual(autorefresh.last_trading_day(MON), MON)


class Due(unittest.TestCase):
    def _state(self, today, asof=None, tech_date=None):
        asof = asof or str(alpha_beta.complete_week_cutoff(today))
        return {"nav": {"ndx": {"asof": asof, "tech_date": tech_date, "method": rating.METHOD_VERSION}}}

    def test_nothing_when_fresh(self):
        self.assertIsNone(autorefresh.due(self._state(WED, tech_date=str(WED)), "ndx", WED))

    def test_no_data_needs_rating(self):
        self.assertEqual(autorefresh.due({}, "ndx", WED), "nav")

    def test_new_week_needs_rating(self):
        old = str(alpha_beta.complete_week_cutoff(WED))
        later = date(2026, 10, 10)                 # 週六: 新的一週已收完
        self.assertEqual(autorefresh.due(self._state(WED, asof=old, tech_date=str(later)), "ndx", later), "nav")

    def test_same_week_never_re_rates(self):
        """同一週內再多天都不重算評級 (只可能更新技術面)。"""
        st = self._state(WED, tech_date=str(MON))
        self.assertEqual(autorefresh.due(st, "ndx", WED), "tech")
        st = self._state(WED, tech_date=str(WED))
        self.assertIsNone(autorefresh.due(st, "ndx", WED))

    def test_weekend_does_not_refetch_tech(self):
        st = self._state(SAT, tech_date=str(FRI))
        self.assertIsNone(autorefresh.due(st, "ndx", SAT) if alpha_beta.complete_week_cutoff(SAT) == FRI else None)

    def test_old_method_version_needs_rating(self):
        """評級方法改版後, 舊結果即使週數、清單都沒變也要重算 (曾因此一直顯示舊邏輯的結果)。"""
        st = self._state(WED, tech_date=str(WED))
        st["nav"]["ndx"]["method"] = rating.METHOD_VERSION - 1
        self.assertEqual(autorefresh.due(st, "ndx", WED), "nav")
        del st["nav"]["ndx"]["method"]
        self.assertEqual(autorefresh.due(st, "ndx", WED), "nav")

    def test_missing_tech_date_needs_tech(self):
        self.assertEqual(autorefresh.due(self._state(WED), "ndx", WED), "tech")


class MaybeStart(unittest.TestCase):
    def setUp(self):
        autorefresh._last_try.clear()
        self.p = [mock.patch.object(autorefresh.settings, "load_mode", return_value="ndx"),
                  mock.patch.object(autorefresh.jobs, "start_job", return_value=True)]
        self.mock_mode, self.start = [p.start() for p in self.p]
        self.addCleanup(lambda: [p.stop() for p in self.p])

    def test_starts_when_due(self):
        self.assertEqual(autorefresh.maybe_start({}, now=1000), "nav")
        self.start.assert_called_once_with("nav")

    def test_backoff_after_attempt(self):
        autorefresh.maybe_start({}, now=1000)
        self.assertIsNone(autorefresh.maybe_start({}, now=1000 + 60))           # 重新整理頁面不會狂打
        self.assertEqual(autorefresh.maybe_start({}, now=1000 + 7 * 3600), "nav")

    def test_busy_job_not_counted_as_attempt(self):
        self.start.return_value = False
        self.assertIsNone(autorefresh.maybe_start({}, now=1000))
        self.start.return_value = True
        self.assertEqual(autorefresh.maybe_start({}, now=1001), "nav")

    def test_empty_watchlist_skipped(self):
        self.mock_mode.return_value = "watchlist"
        with mock.patch.object(autorefresh.settings, "load_watchlist", return_value=[]):
            self.assertIsNone(autorefresh.maybe_start({}, now=1000))
        self.start.assert_not_called()


class Scheduled(unittest.TestCase):
    """週六早上的排程更新: 到點才做、三個股票池依序、最後動能榜、失敗的先跳過。"""
    SAT_AM = datetime(2026, 10, 10, 8)                 # 10/9 那週收完的隔天早上

    def setUp(self):
        autorefresh._last_try.clear()
        self.p = [mock.patch.object(autorefresh.settings, "load_watchlist", return_value=["AAA"]),
                  mock.patch.object(autorefresh.jobs, "start_job", return_value=True)]
        _, self.start = [p.start() for p in self.p]
        self.addCleanup(lambda: [p.stop() for p in self.p])

    def _fresh(self, *modes):
        asof = str(alpha_beta.complete_week_cutoff(self.SAT_AM.date()))
        nav = {m: {"asof": asof, "method": rating.METHOD_VERSION, "tickers": ["AAA"] if m == "watchlist" else [],
                   "groups_sig": None, "tech_date": "x"} for m in modes}
        return {"nav": nav}

    def run_at(self, state, now_dt, now=1000):
        with mock.patch.object(autorefresh.groups, "signature", return_value=None):
            return autorefresh.scheduled_start(state, now=now, now_dt=now_dt)

    def test_waits_until_saturday_morning(self):
        self.assertFalse(autorefresh.weekly_ready(datetime(2026, 10, 10, 6, 59)))
        self.assertTrue(autorefresh.weekly_ready(datetime(2026, 10, 10, 7)))
        self.assertTrue(autorefresh.weekly_ready(datetime(2026, 10, 12, 13)))     # 週六在睡眠: 週一補做
        self.assertTrue(autorefresh.weekly_ready(datetime(2026, 10, 9, 23)))      # 上一週的已到點 (是否過期由 due 判斷)
        self.assertIsNone(self.run_at({}, datetime(2026, 10, 10, 6)))
        self.start.assert_not_called()

    def test_pools_in_order_then_momentum(self):
        st = self._fresh()
        self.assertEqual(self.run_at(st, self.SAT_AM), ("nav", "sp500"))
        self.start.assert_called_with("nav", "sp500")
        st = self._fresh("sp500")
        self.assertEqual(self.run_at(st, self.SAT_AM), ("nav", "ndx"))
        st = self._fresh("sp500", "ndx")
        self.assertEqual(self.run_at(st, self.SAT_AM), ("nav", "watchlist"))
        st = self._fresh("sp500", "ndx", "watchlist")
        self.assertEqual(self.run_at(st, self.SAT_AM), ("momentum", "all"))
        self.start.assert_called_with("momentum", None)
        st["momentum"] = {"asof": str(alpha_beta.complete_week_cutoff(self.SAT_AM.date()))}
        self.assertIsNone(self.run_at(st, self.SAT_AM))                          # 全部最新: 不上網

    def test_failed_pool_skipped_until_backoff(self):
        st = self._fresh()
        self.run_at(st, self.SAT_AM, now=1000)                                    # sp500 失敗了 (仍過期)
        self.assertEqual(self.run_at(st, self.SAT_AM, now=1600), ("nav", "ndx"))  # 先做下一個
        self.assertEqual(self.run_at(st, self.SAT_AM, now=1000 + 7 * 3600), ("nav", "sp500"))

    def test_busy_waits(self):
        self.start.return_value = False
        self.assertIsNone(self.run_at({}, self.SAT_AM))
        self.assertEqual(autorefresh._last_try, {})                               # 沒有真的嘗試, 不算退避

    def test_empty_watchlist_skipped(self):
        st = self._fresh("sp500", "ndx")
        with mock.patch.object(autorefresh.settings, "load_watchlist", return_value=[]):
            self.assertEqual(self.run_at(st, self.SAT_AM), ("momentum", "all"))


class ApplyTechnicals(unittest.TestCase):
    def test_only_tech_columns_change(self):
        rows = pd.DataFrame([{"代號": "AAA", "綜合評級": "A", "動能_tip": "✅ 正常", "基本面評級": "B", "RSI": None, "趨勢": None, "提示": ""}])
        out = presenters.apply_technicals(rows, {"AAA": {"rsi": 75.0, "rsi_zone": "過熱", "trend": "多頭"}})
        self.assertEqual((out.at[0, "RSI"], out.at[0, "趨勢"], out.at[0, "RSI區間"]), (75.0, "多頭", "過熱"))
        self.assertIn("等回檔", out.at[0, "提示"])                          # 綜合評級 A + 過熱
        self.assertEqual((out.at[0, "綜合評級"], out.at[0, "基本面評級"]), ("A", "B"))
        self.assertIsNone(rows.at[0, "RSI"])                                # 不改動原表

    def test_missing_ticker_blanks_tech(self):
        rows = pd.DataFrame([{"代號": "AAA", "綜合評級": "A", "動能_tip": ""}])
        out = presenters.apply_technicals(rows, {})
        self.assertTrue(pd.isna(out.at[0, "RSI"]) or out.at[0, "RSI"] is None)


class SnapshotMerge(unittest.TestCase):
    def test_same_day_other_pool_kept(self):
        with temp_data():
            def fake(tickers):
                return {t: {"earnings_per_share_forecast_next_fq": 1.0, "close": 10.0, "RSI": 50.0} for t in tickers}
            with mock.patch.object(estimates.tradingview, "fetch_estimates", side_effect=fake):
                estimates.snapshot(["AAA", "BBB"], today="2026-10-05")        # 例如 S&P 500
                estimates.snapshot(["CCC", "AAA"], today="2026-10-05")        # 再更新 Nasdaq 100
            snaps = estimates.load_snapshots()
            self.assertEqual(len(snaps), 1)
            self.assertEqual(set(snaps[0]["data"]), {"AAA", "BBB", "CCC"})


class DoTech(unittest.TestCase):
    def setUp(self):
        self.saved = dict(store.state)
        store.state.clear()
        self.addCleanup(lambda: (store.state.clear(), store.state.update(self.saved)))
        self.p = mock.patch.object(steps.settings, "load_mode", return_value="ndx")
        self.p.start(); self.addCleanup(self.p.stop)

    def test_requires_rating_first(self):
        with self.assertRaises(RuntimeError):
            steps.do_tech(False, "ndx")

    def test_failed_fetch_keeps_old_data(self):
        rows = pd.DataFrame([{"代號": "AAA", "綜合評級": "A", "動能_tip": "", "RSI": 40.0}])
        store.state["nav"] = {"ndx": {"rows": rows, "tickers": ["AAA"], "tech_date": "2026-10-01"}}
        with mock.patch.object(steps.estimates, "snapshot", return_value=None):
            with self.assertRaises(RuntimeError):
                steps.do_tech(False, "ndx")
        N = store.state["nav"]["ndx"]
        self.assertEqual(N["tech_date"], "2026-10-01")          # 失敗不能把「已更新」記成今天
        self.assertEqual(N["rows"].at[0, "RSI"], 40.0)

    def test_success_updates_tech_only(self):
        rows = pd.DataFrame([{"代號": "AAA", "綜合評級": "B", "動能_tip": "", "RSI": 40.0, "基本面評級": "C"}])
        store.state["nav"] = {"ndx": {"rows": rows, "tickers": ["AAA"], "asof": "2026-10-02", "tech_date": "2026-10-01"}}
        with mock.patch.object(steps.estimates, "snapshot", return_value={"AAA": {}}), \
             mock.patch.object(steps.estimates, "get_technicals", return_value={"AAA": {"rsi": 61.0, "trend": "多頭"}}):
            steps.do_tech(False, "ndx")
        N = store.state["nav"]["ndx"]
        self.assertEqual(N["rows"].at[0, "RSI"], 61.0)
        self.assertEqual((N["rows"].at[0, "基本面評級"], N["asof"]), ("C", "2026-10-02"))
        self.assertEqual(N["tech_date"], str(date.today()))


if __name__ == "__main__":
    unittest.main()
