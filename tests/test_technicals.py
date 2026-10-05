"""技術面指標計算測試 (全部 mock, 不連網)。"""
import unittest
from investor.navellier import technicals


class TechnicalsRSI(unittest.TestCase):
    """RSI 計算與分區。"""

    def test_rsi_normal(self):
        raw = {"RSI": 45.0}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["rsi"], 45.0)
        self.assertIsNone(result["rsi_zone"])

    def test_rsi_overbought(self):
        raw = {"RSI": 70.0}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["rsi_zone"], "過熱")

    def test_rsi_overbought_above_70(self):
        raw = {"RSI": 75.0}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["rsi_zone"], "過熱")

    def test_rsi_oversold(self):
        raw = {"RSI": 30.0}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["rsi_zone"], "超賣")

    def test_rsi_oversold_below_30(self):
        raw = {"RSI": 25.0}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["rsi_zone"], "超賣")

    def test_rsi_boundary_71(self):
        raw = {"RSI": 71.0}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["rsi_zone"], "過熱")  # 71 >= 70


class TechnicalsTrend(unittest.TestCase):
    """趨勢判斷。"""

    def test_trend_bullish(self):
        raw = {"close": 100, "SMA50": 95, "SMA200": 90}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["trend"], "多頭")

    def test_trend_pullback(self):
        raw = {"close": 92, "SMA50": 95, "SMA200": 90}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["trend"], "回檔")

    def test_trend_bearish(self):
        raw = {"close": 88, "SMA50": 95, "SMA200": 90}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["trend"], "空頭")

    def test_trend_at_sma50(self):
        raw = {"close": 95, "SMA50": 95, "SMA200": 90}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["trend"], "回檔")  # close <= SMA50

    def test_trend_missing_data(self):
        raw = {"close": 100, "SMA50": None, "SMA200": 90}
        result = technicals.derive_technicals(raw)
        self.assertIsNone(result["trend"])


class TechnicalsOffHigh(unittest.TestCase):
    """距 52 週高價。"""

    def test_off_high_pct(self):
        raw = {"close": 90, "price_52_week_high": 100}
        result = technicals.derive_technicals(raw)
        self.assertAlmostEqual(result["off_high_pct"], -10.0, delta=0.01)

    def test_off_high_at_peak(self):
        raw = {"close": 100, "price_52_week_high": 100}
        result = technicals.derive_technicals(raw)
        self.assertAlmostEqual(result["off_high_pct"], 0.0, delta=0.01)

    def test_off_high_missing(self):
        raw = {"close": 90, "price_52_week_high": None}
        result = technicals.derive_technicals(raw)
        self.assertIsNone(result["off_high_pct"])


class TechnicalsMACD(unittest.TestCase):
    """MACD 計算。"""

    def test_macd_positive(self):
        raw = {"MACD.macd": 0.5, "MACD.signal": 0.3}
        result = technicals.derive_technicals(raw)
        self.assertAlmostEqual(result["macd_hist"], 0.2, delta=0.01)
        self.assertEqual(result["macd_dir"], "轉強")

    def test_macd_negative(self):
        raw = {"MACD.macd": 0.2, "MACD.signal": 0.5}
        result = technicals.derive_technicals(raw)
        self.assertAlmostEqual(result["macd_hist"], -0.3, delta=0.01)
        self.assertEqual(result["macd_dir"], "轉弱")

    def test_macd_zero(self):
        raw = {"MACD.macd": 0.3, "MACD.signal": 0.3}
        result = technicals.derive_technicals(raw)
        self.assertAlmostEqual(result["macd_hist"], 0.0, delta=0.01)
        self.assertIsNone(result["macd_dir"])


class TechnicalsRating(unittest.TestCase):
    """技術評等 (Recommend.All)。"""

    def test_rating_strong_buy(self):
        raw = {"Recommend.All": 0.5}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["tech_rating"], "強力買進")

    def test_rating_buy(self):
        raw = {"Recommend.All": 0.1}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["tech_rating"], "買進")

    def test_rating_neutral(self):
        raw = {"Recommend.All": -0.05}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["tech_rating"], "中立")

    def test_rating_sell(self):
        raw = {"Recommend.All": -0.3}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["tech_rating"], "賣出")

    def test_rating_strong_sell(self):
        raw = {"Recommend.All": -0.8}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["tech_rating"], "強力賣出")

    def test_rating_boundary_0_5(self):
        raw = {"Recommend.All": 0.49}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["tech_rating"], "買進")  # < 0.5

    def test_rating_boundary_0_1(self):
        raw = {"Recommend.All": 0.09}
        result = technicals.derive_technicals(raw)
        self.assertEqual(result["tech_rating"], "中立")  # < 0.1


class TechnicalsReport(unittest.TestCase):
    """財報日計算。"""

    def test_report_in_7_days(self):
        # 2026-10-10 (5 days after today 2026-10-05)
        raw = {"earnings_release_next_date": 1791590400}
        result = technicals.derive_technicals(raw, "2026-10-05")
        self.assertEqual(result["days_to_report"], 5)
        self.assertTrue(result["report_soon"])

    def test_report_exactly_7_days(self):
        # 2026-10-12 (7 days after today 2026-10-05)
        raw = {"earnings_release_next_date": 1791763200}
        result = technicals.derive_technicals(raw, "2026-10-05")
        self.assertEqual(result["days_to_report"], 7)
        self.assertTrue(result["report_soon"])

    def test_report_8_days_away(self):
        # 2026-10-13 (8 days after today 2026-10-05)
        raw = {"earnings_release_next_date": 1791849600}
        result = technicals.derive_technicals(raw, "2026-10-05")
        self.assertEqual(result["days_to_report"], 8)
        self.assertFalse(result["report_soon"])

    def test_report_past(self):
        # 2026-10-04 (before today 2026-10-05)
        raw = {"earnings_release_next_date": 1791072000}
        result = technicals.derive_technicals(raw, "2026-10-05")
        self.assertIsNone(result["days_to_report"])
        self.assertFalse(result["report_soon"])


class HintsLogic(unittest.TestCase):
    """提示字串組合邏輯。"""

    def test_hints_a_grade_overbought(self):
        row = {"rsi_zone": "過熱"}
        h = technicals.hints(row, fund_grade="A")
        self.assertIn("等回檔", h)

    def test_hints_b_grade_pullback(self):
        row = {"trend": "回檔"}
        h = technicals.hints(row, fund_grade="B")
        self.assertIn("可留意", h)

    def test_hints_removal_bearish(self):
        row = {"trend": "空頭"}
        h = technicals.hints(row, cooling_flag="❌ 建議剔除（量化分數位於股票池第 35% 分位，低於 40%）")
        self.assertIn("弱勢確認", h)

    def test_hints_report_soon(self):
        row = {"report_soon": True, "days_to_report": 3}
        h = technicals.hints(row)
        self.assertIn("財報在 3 天內", h)

    def test_hints_multiple(self):
        row = {"rsi_zone": "過熱", "trend": "回檔", "report_soon": True, "days_to_report": 2}
        h = technicals.hints(row, fund_grade="A")
        self.assertGreaterEqual(len(h), 2)

    def test_hints_empty(self):
        row = {"rsi_zone": None, "trend": "多頭", "report_soon": False}
        h = technicals.hints(row, fund_grade="C")
        self.assertEqual(h, [])


if __name__ == "__main__":
    unittest.main()
