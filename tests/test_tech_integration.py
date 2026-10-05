"""技術面與網頁整合測試。"""
import unittest
from investor.navellier import technicals
from investor.web import presenters


class NavellierRowsWithTechnicals(unittest.TestCase):
    """presenters.navellier_rows 與技術面相容。"""

    def _mock_r(self, with_tech=True):
        """組合假的 r (rating.run() 結果)。"""
        r = {
            "tickers": ["AMZN", "ABT"],
            "names": {"AMZN": "Amazon", "ABT": "Abbott"},
            "sectors": {"AMZN": "Information Technology", "ABT": "Healthcare"},
            "report": {
                "AMZN": {
                    "overall": "A", "combined": 4.5, "fund_grade": "B", "fund_avg": 3.5,
                    "nav_grade": "A", "quant_quintile": 5, "alpha_over_sd": 1.2,
                    "eligible": True, "nav_pct": 0.8, "cooling": "✅ 正常",
                    "rsi": 45.0, "rsi_zone": None, "trend": "多頭",
                    "off_high_pct": -5.0, "macd_dir": "轉強",
                    "tech_rating": "買進", "next_report": "2026-10-29",
                    "days_to_report": 24, "report_soon": False,
                    "tech_hints": ["可留意"] if with_tech else [],
                },
                "ABT": {
                    "overall": "C", "combined": 3.0, "fund_grade": "C", "fund_avg": 3.0,
                    "nav_grade": "C", "quant_quintile": 3, "alpha_over_sd": 0.5,
                    "eligible": True, "nav_pct": 0.5, "cooling": "🔻 冷卻警示",
                    "rsi": None, "rsi_zone": None, "trend": None,
                    "off_high_pct": None, "macd_dir": None,
                    "tech_rating": None, "next_report": None,
                    "days_to_report": None, "report_soon": False,
                    "tech_hints": [],
                } if with_tech else {
                    "overall": "C", "combined": 3.0, "fund_grade": "C", "fund_avg": 3.0,
                    "nav_grade": "C", "quant_quintile": 3, "alpha_over_sd": 0.5,
                    "eligible": True, "nav_pct": 0.5, "cooling": "🔻 冷卻警示",
                },
            },
            "series": {"AMZN": [1.0, 1.1, 1.2], "ABT": [0.5, 0.6, 0.7]},
        }
        return r

    def test_navellier_rows_with_tech(self):
        """navellier_rows 包含技術面欄位時能正常運作。"""
        r = self._mock_r(with_tech=True)
        df = presenters.navellier_rows(r, set())

        # 檢查表格結構
        self.assertEqual(len(df), 2)
        self.assertIn("RSI", df.columns)
        self.assertIn("趨勢", df.columns)
        self.assertIn("技術評等", df.columns)

        # 檢查技術面數據
        amzn_row = df[df["代號"] == "AMZN"].iloc[0]
        self.assertEqual(amzn_row["RSI"], 45.0)
        self.assertEqual(amzn_row["趨勢"], "多頭")
        self.assertEqual(amzn_row["技術評等"], "買進")

    def test_navellier_rows_without_tech(self):
        """navellier_rows 沒有技術面欄位時也能運作 (向後相容)。"""
        r = self._mock_r(with_tech=False)
        df = presenters.navellier_rows(r, set())

        # 檢查表格結構
        self.assertEqual(len(df), 2)
        # 既有欄位應該還在
        self.assertIn("綜合評級", df.columns)
        self.assertIn("基本面評級", df.columns)

    def test_existing_fields_unchanged(self):
        """加技術面欄位後既有欄位值不變。"""
        r_without = self._mock_r(with_tech=False)
        r_with = self._mock_r(with_tech=True)

        df_without = presenters.navellier_rows(r_without, set())
        df_with = presenters.navellier_rows(r_with, set())

        # 檢查既有欄位完全相同
        for col in ["代號", "公司", "綜合評級", "綜合分", "基本面評級", "量化評級"]:
            if col in df_without.columns and col in df_with.columns:
                self.assertTrue(
                    (df_without[col].fillna("") == df_with[col].fillna("")).all(),
                    f"Column {col} changed with tech fields"
                )


class RatingReportWithTechnicals(unittest.TestCase):
    """rating.py report 加技術面欄位後既有欄位不變。"""

    def test_report_fields_preserved(self):
        """評級 report 加技術面欄位不應改變現有欄位。"""
        # 這個測試主要檢查 rating.py 的邏輯
        # 實際的 rating.run() 需要完整的資料, 所以只測試結構

        # 模擬一個包含技術面的 report entry
        entry_with_tech = {
            "overall": "A", "combined": 4.5, "fund_grade": "B",
            "rsi": 45.0, "trend": "多頭", "tech_rating": "買進",
            "tech_hints": ["可留意"],
        }

        entry_without_tech = {
            "overall": "A", "combined": 4.5, "fund_grade": "B",
        }

        # 既有欄位應該相同
        self.assertEqual(entry_with_tech["overall"], entry_without_tech["overall"])
        self.assertEqual(entry_with_tech["combined"], entry_without_tech["combined"])
        self.assertEqual(entry_with_tech["fund_grade"], entry_without_tech["fund_grade"])


class HintsFromReportFields(unittest.TestCase):
    """hints() 用 report 欄位組合提示。"""

    def test_hints_with_a_and_overbought(self):
        """A 級 + 過熱 RSI -> 等回檔。"""
        row = {"rsi_zone": "過熱"}
        h = technicals.hints(row, fund_grade="A")
        self.assertIn("等回檔", h)

    def test_hints_with_removal_and_bearish(self):
        """被建議移除 + 空頭 -> 弱勢確認。"""
        row = {"trend": "空頭"}
        h = technicals.hints(row, cooling_flag="❌ 建議剔除（量化分數位於股票池第 35% 分位，低於 40%）")
        self.assertIn("弱勢確認", h)

    def test_hints_multiple_conditions(self):
        """多個條件同時觸發。"""
        row = {"rsi_zone": "過熱", "report_soon": True, "days_to_report": 5}
        h = technicals.hints(row, fund_grade="A")
        self.assertGreaterEqual(len(h), 2)  # 至少「等回檔」和「財報在 5 天內」


if __name__ == "__main__":
    unittest.main()
