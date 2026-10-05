"""Navellier 基本面因子: 把討論過的定義與案例固定下來 (純計算, 不連網)。"""
import unittest

import numpy as np
import pandas as pd

from investor.navellier import factors as F


def S(*v):
    return pd.Series(v, dtype=float)


class EarnAccel(unittest.TestCase):
    """盈餘動能 = 成長率的變化率 (二階導數) = (g最新 - g前一季) / g前一季; 連續季數 = 連續幾季成長率為正且逐季提高。"""

    def test_nvda_decelerating(self):                 # EPS 一路創高, 但成長率 +36% -> +3%
        a = F.earn_accel(S(1.08, 1.30, 1.76, 2.39, 2.46))
        self.assertEqual(a["earn_accel_streak"], 0)
        self.assertAlmostEqual(a["earn_accel_pct"], -91.8, delta=0.1)       # (2.9 - 35.8) / 35.8
        self.assertEqual(a["earn_growth_pct"], [20.4, 35.4, 35.8, 2.9])
        self.assertIsNone(a["earn_accel_case"])

    def test_sndk_is_74_percent_not_256_points(self):  # +347% -> +91%: (91-347)/347 = -73.8%, 不是「相減 -256」
        a = F.earn_accel(S(-0.16, 0.75, 5.15, 23.03, 43.97))
        self.assertAlmostEqual(a["earn_accel_pct"], -73.8, delta=0.1)
        self.assertEqual(a["earn_growth_pct"][0], None)                      # 基期為負, 成長率無法計算

    def test_steady_acceleration_scores_three(self):
        a = F.earn_accel(S(1.0, 1.1, 1.25, 1.5, 1.9))        # 成長率 10% -> 13.6% -> 20% -> 26.7%
        self.assertEqual(a["earn_accel_streak"], 3)
        self.assertGreater(a["earn_accel"], 3)

    def test_latest_quarter_loss_is_worst_and_labelled(self):
        a = F.earn_accel(S(-0.67, 0.90, -0.12, -0.73, -2.16))
        self.assertEqual(a["earn_accel_case"], "loss")
        self.assertTrue(np.isnan(a["earn_accel_pct"]))
        self.assertLess(a["earn_accel"], 0)

    def test_rebound_is_not_called_a_loss(self):
        """AMD: EPS 全為正, 前一季成長率 -8.7% (小幅下滑), 最新季 +64%。比值正負號會反, 所以不給百分比, 歸為「反彈」。"""
        a = F.earn_accel(S(0.54, 0.75, 0.92, 0.84, 1.38))
        self.assertEqual(a["earn_accel_case"], "rebound")
        self.assertTrue(np.isnan(a["earn_accel_pct"]))
        self.assertGreater(a["earn_accel"], 0)               # 排在所有「成長率減速者」之前
        self.assertGreater(a["earn_accel"], F.earn_accel(S(1.08, 1.30, 1.76, 2.39, 2.46))["earn_accel"])

    def test_turnaround_and_declining(self):
        self.assertEqual(F.earn_accel(S(-0.2, -0.1, -0.05, 0.1, 0.3))["earn_accel_case"], "turnaround")
        self.assertEqual(F.earn_accel(S(2.0, 1.8, 1.5, 1.4, 1.0))["earn_accel_case"], "declining")

    def test_needs_five_consecutive_quarters(self):
        self.assertTrue(np.isnan(F.earn_accel(S(1, 2, 3, 4))["earn_accel"]))                  # 只有 4 季
        self.assertTrue(np.isnan(F.earn_accel(S(1, np.nan, 3, 4, 5))["earn_accel"]))          # 中間缺一季 (如 BRK-B)


class YoyAndTrim(unittest.TestCase):
    def test_yoy_states(self):
        self.assertEqual(F.yoy_state(S(1, 1, 1, 1, 2)), (1.0, "ok"))
        self.assertEqual(F.yoy_state(S(1, 1, 1, 1, -1))[1], "loss")
        self.assertEqual(F.yoy_state(S(-1, 1, 1, 1, 2))[1], "turnaround")
        self.assertEqual(F.yoy_state(S(1, 2))[1], None)

    def test_trim_only_cuts_trailing_nans(self):
        t = F.trim([1, None, 3, None, None])
        self.assertEqual(len(t), 3)                                  # 尾端兩個空值被剪掉
        self.assertTrue(np.isnan(t.iloc[1]))                         # 中間的缺口保留 (位置關係不可被破壞)

    def test_growth_does_not_skip_an_interior_gap(self):
        # 位置運算在「保留空值」的序列上做: [-1] vs [-5] 一定是真正相隔 4 季
        self.assertAlmostEqual(F.growth(S(1, np.nan, 3, 4, 5), 4), 4.0)           # 5/1 - 1; 中間缺一季不影響
        self.assertTrue(np.isnan(F.growth(S(np.nan, 2, 3, 4, 5), 4)))              # 4 季前缺值 -> NaN, 不會默默往更早找


class Derive(unittest.TestCase):
    def test_trailing_empty_quarter_does_not_kill_factors(self):
        """Yahoo 對剛公布的最新一季常只有 EPS (營收/營業利益/淨利為空): 營收年增、利益率、ROE 仍須算得出來 (AMZN 實例)。"""
        r = {"_rev": [155667e6, 167702e6, 180169e6, 213386e6, 181519e6, None],
             "_opinc": [18405e6, 19171e6, 17422e6, 24977e6, 23852e6, None],
             "_eps": [None, 1.68, 1.95, 1.95, 2.78, 5.75],
             "_ni": [17127e6, 18164e6, 21187e6, 21192e6, 30255e6, None],
             "_fcf": [None] * 6, "_eq": [305867e6, 333775e6, 369631e6, 411065e6, 441914e6, None]}
        d = F.derive(r)
        self.assertAlmostEqual(d["sales_yoy"], 181519 / 155667 - 1, places=6)
        self.assertAlmostEqual(d["margin_exp_yoy_pp"], 1.3169, places=3)
        self.assertAlmostEqual(d["roe_ttm"], 21.29, delta=0.01)
        self.assertEqual(d["earn_state"], "ok")                      # EPS 用到最新一季 (5.75)

    def test_falls_back_to_net_income_when_eps_is_sparse(self):
        d = F.derive({"_eps": [None, 1, None, None, None, 2], "_ni": [10, 20, 30, 40, 50, 60]})
        self.assertEqual(d["_base"], [10.0, 20.0, 30.0, 40.0, 50.0, 60.0])


class Ranking(unittest.TestCase):
    def test_ties_get_equal_quintile(self):                           # 同值不可因排序先後被分到不同等級
        q = F.quintiles([("a", 0), ("b", 0), ("c", 0), ("d", 0), ("e", 0), ("f", 3)])
        self.assertEqual({q[t] for t in "abcde"}, {2})
        self.assertEqual(q["f"], 5)

    def test_factor_value_sentinels(self):
        self.assertEqual(F.factor_value({"earn_state": "loss"}, "earn_yoy"), -1e9)
        self.assertEqual(F.factor_value({"fcf_state": "turnaround"}, "fcf_yoy"), 1e9)
        self.assertIsNone(F.factor_value({}, "roe_ttm"))                # 真正缺資料 -> 略過


if __name__ == "__main__":
    unittest.main()
