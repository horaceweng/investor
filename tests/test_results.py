"""最新財報 beat/miss: 分類、提示、快照存取、顯示欄位。"""
import unittest
from unittest import mock

import pandas as pd

from investor.navellier import estimates, technicals
from investor.web import presenters
from tests.helpers import temp_data


class DeriveResult(unittest.TestCase):
    def d(self, eps, rev, date="2026-10-01", today="2026-10-05"):
        return technicals.derive_result({"eps_pct": eps, "rev_pct": rev, "date": date}, today)

    def test_labels(self):
        self.assertEqual(self.d(5.0, 2.0)["result_label"], "雙 beat")
        self.assertEqual(self.d(-5.0, -2.0)["result_label"], "雙 miss")
        self.assertEqual(self.d(5.0, -1.0)["result_label"], "EPS beat、營收 miss")
        self.assertEqual(self.d(-5.0, 1.0)["result_label"], "EPS miss、營收 beat")
        self.assertEqual(self.d(0.0, 0.0)["result_label"], "符合預期")

    def test_partial_and_missing(self):
        self.assertEqual(self.d(5.0, None)["result_label"], "EPS beat")
        self.assertIsNone(self.d(None, 3.0)["result_label"])
        self.assertIsNone(technicals.derive_result(None)["result_label"])
        self.assertIsNone(technicals.derive_result({"eps_pct": float("nan"), "rev_pct": None})["result_label"])

    def test_days_since(self):
        self.assertEqual(self.d(1.0, 1.0)["days_since_report"], 4)
        self.assertIsNone(self.d(1.0, 1.0, date=None)["days_since_report"])


class ResultHints(unittest.TestCase):
    def h(self, eps, rev, since):
        return technicals.hints({"eps_surprise_pct": eps, "rev_surprise_pct": rev, "days_since_report": since})

    def test_eps_beat_revenue_miss(self):
        self.assertIn("EPS beat 但營收 miss", self.h(3.0, -1.0, 5))

    def test_double_miss(self):
        self.assertIn("財報雙 miss", self.h(-3.0, -1.0, 5))

    def test_clean_beat_no_hint(self):
        self.assertEqual(self.h(3.0, 1.0, 5), [])

    def test_old_report_not_flagged(self):
        self.assertEqual(self.h(3.0, -1.0, 60), [])


class SnapshotRoundTrip(unittest.TestCase):
    def test_result_stored_and_returned(self):
        row = {"close": 10.0, "RSI": 50.0, "SMA50": 9.0, "SMA200": 8.0, "price_52_week_high": 12.0,
               "Recommend.All": 0.2, "MACD.macd": 0.1, "MACD.signal": 0.0, "earnings_release_next_date": 1793275200,
               "eps_surprise_percent_fq": 6.2, "revenue_surprise_percent_fq": -1.5, "earnings_release_date": 1790000000}
        with temp_data():
            with mock.patch.object(estimates.tradingview, "fetch_estimates", return_value={"AAA": row}):
                estimates.snapshot(["AAA"], today="2026-10-05")
            res = estimates.load_snapshots()[0]["data"]["AAA"]["result"]
            self.assertEqual((res["eps_pct"], res["rev_pct"], res["date"]), (6.2, -1.5, "2026-09-21"))
            t = estimates.get_technicals("2026-10-05")["AAA"]
            self.assertEqual((t["result_label"], t["days_since_report"]), ("EPS beat、營收 miss", 14))
            self.assertIn("EPS beat 但營收 miss", technicals.hints(t))

    def test_after_close_release_uses_us_eastern_date(self):
        """美東 2026-10-01 20:30 發布 = UTC 10-02 00:30, 日期要算 10-01。"""
        self.assertEqual(estimates._unix_date(1790901000), "2026-10-01")
        self.assertIsNone(estimates._unix_date(None))

    def test_old_snapshot_without_result(self):
        with temp_data():
            estimates._save_snapshots([{"date": "2026-10-01", "data": {"AAA": {"tech": {"close": 10.0, "rsi": 50.0}}}}])
            t = estimates.get_technicals("2026-10-05")["AAA"]
            self.assertIsNone(t["result_label"])


class Display(unittest.TestCase):
    def test_columns_in_rows(self):
        rows = pd.DataFrame([{"代號": "AAA", "綜合評級": "B", "動能_tip": ""}])
        out = presenters.apply_technicals(rows, {"AAA": technicals.derive_result(
            {"eps_pct": 4.0, "rev_pct": 1.0, "date": "2026-10-01"}, "2026-10-05")})
        self.assertEqual((out.at[0, "EPS驚喜%"], out.at[0, "營收驚喜%"], out.at[0, "財報結果"], out.at[0, "最新財報"]),
                         (4.0, 1.0, "雙 beat", "2026-10-01"))


if __name__ == "__main__":
    unittest.main()
