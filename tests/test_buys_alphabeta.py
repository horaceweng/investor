"""13F 買進判定與 Alpha/SD 週資料的純計算部分。"""
import unittest
from datetime import date

import numpy as np
import pandas as pd

from investor.navellier import alpha_beta as AB
from investor.superinvestors import buys as B


class Quarters(unittest.TestCase):
    def test_prev_quarters_oldest_first_with_correct_month_ends(self):
        self.assertEqual(B.prev_quarters("2026-06-30", 4),
                         ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"])

    def test_year_wraparound(self):
        self.assertEqual(B.prev_quarters("2026-03-31", 2), ["2025-09-30", "2025-12-31", "2026-03-31"])


def H(**kw):
    """{cusip: [股數, 名稱]}"""
    return {c: [s, c + " INC"] for c, s in kw.items()}


class QuarterStats(unittest.TestCase):
    def test_new_and_added_positions_counted_others_not(self):
        data = {"m1": {"Q2": H(AAA=100, BBB=50, CCC=10), "Q1": H(AAA=100, BBB=10)},
                "m2": {"Q2": H(AAA=200), "Q1": H(AAA=100)}}
        stat, n, _ = B.quarter_stats(data, ("Q2", "Q1"))
        self.assertEqual(n, 2)
        self.assertEqual(stat["BBB"]["add"], ["m1"])                    # 50 > 10 * 1.05
        self.assertEqual(stat["CCC"]["new"], ["m1"])
        self.assertEqual(stat["AAA"]["add"], ["m2"])                    # m1 的 AAA 沒變 -> 不算買進

    def test_small_changes_under_5_percent_are_not_buys(self):
        stat, _, _ = B.quarter_stats({"m": {"Q2": H(AAA=104), "Q1": H(AAA=100)}}, ("Q2", "Q1"))
        self.assertEqual(stat["AAA"]["add"], [])

    def test_spinoff_where_everyone_is_new_is_excluded(self):
        """分拆新股: 所有持有人都是「新建倉」且 >= 5 家, 不是主動買進。"""
        data = {f"m{i}": {"Q2": H(SPIN=10, KEEP=5), "Q1": H(KEEP=5)} for i in range(6)}
        stat, _, _ = B.quarter_stats(data, ("Q2", "Q1"))
        self.assertNotIn("SPIN", stat)
        self.assertIn("KEEP", stat)

    def test_stock_split_is_not_counted_as_buying(self):
        """多數持有人的股數同比例變動 (如 4:1 分割) 要還原, 否則全部被當成加碼。"""
        data = {f"m{i}": {"Q2": H(SPLT=400, OTHR=100), "Q1": H(SPLT=100, OTHR=100)} for i in range(6)}
        stat, _, split = B.quarter_stats(data, ("Q2", "Q1"))
        self.assertEqual(split["SPLT"], 4.0)
        self.assertEqual(stat["SPLT"]["add"], [])

    def test_etfs_are_excluded(self):
        data = {"m": {"Q2": {"X": [10.0, "VANGUARD INDEX FUND"], "Y": [10.0, "REAL CO"]}, "Q1": {}}}
        stat, _, _ = B.quarter_stats(data, ("Q2", "Q1"))
        self.assertNotIn("X", stat)
        self.assertIn("Y", stat)


class WeekCutoff(unittest.TestCase):
    """只計已收完的週 (週六起才算該週結束), 避免同一週內不同天執行得到不同分數。"""

    def test_cutoff_by_weekday(self):
        f = AB.complete_week_cutoff
        self.assertEqual(f(date(2026, 10, 5)), date(2026, 10, 2))      # 週一 -> 上週五
        self.assertEqual(f(date(2026, 10, 8)), date(2026, 10, 2))      # 週四
        self.assertEqual(f(date(2026, 10, 9)), date(2026, 10, 2))      # 週五(尚未收盤確認) -> 仍是上週五
        self.assertEqual(f(date(2026, 10, 10)), date(2026, 10, 9))     # 週六 -> 當週五
        self.assertEqual(f(date(2026, 10, 11)), date(2026, 10, 9))     # 週日


class WeeklyScores(unittest.TestCase):
    def _prices(self, n_weeks=80, seed=1):
        rng = np.random.default_rng(seed)
        idx = pd.bdate_range("2024-01-01", periods=n_weeks * 5)
        mk = 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, len(idx)))
        st = 100 * np.cumprod(1 + rng.normal(0.0008, 0.015, len(idx)))
        return pd.DataFrame({"SPY": mk, "AAA": st}, index=idx)

    def test_score_is_information_ratio_of_weekly_excess_returns(self):
        px = self._prices()
        cutoff = max(d for d in px.resample("W-FRI").last().index if d <= px.index[-1])
        out = AB.weekly_scores(px, ["AAA"], "SPY", cutoff, 0)
        w = px.resample("W-FRI").last().pct_change().dropna()
        a = (w["AAA"] - w["SPY"]).iloc[-52:]
        expected = a.mean() * 52 / (a.std(ddof=1) * np.sqrt(52))
        self.assertAlmostEqual(out[str(cutoff.date())]["AAA"], round(expected, 3), places=3)

    def test_needs_52_weeks(self):
        px = self._prices(n_weeks=30)
        cutoff = max(d for d in px.resample("W-FRI").last().index if d <= px.index[-1])
        self.assertEqual(AB.weekly_scores(px, ["AAA"], "SPY", cutoff, 0), {})


if __name__ == "__main__":
    unittest.main()
