"""每週歷史的合併規則、評級計算。"""
import unittest

from investor.navellier import grading, history


class History(unittest.TestCase):
    def test_week_end_is_the_friday_on_or_after(self):
        self.assertEqual(history.week_end("2026-09-29"), "2026-10-02")      # 週二 -> 該週五
        self.assertEqual(history.week_end("2026-10-02"), "2026-10-02")      # 週五 -> 自己
        self.assertEqual(history.week_end("2026-10-03"), "2026-10-09")      # 週六 -> 下週五

    def test_live_records_are_never_overwritten_by_backfill(self):
        recs = {"2026-09-25": {"scores": {"A": 1.0}, "source": "live"}}
        out = history.merge_history(recs, {"2026-09-25": {"A": 9.9, "B": 2.0}, "2026-09-18": {"A": 3.0}}, "2026-10-02")
        self.assertEqual(out["2026-09-25"]["scores"], {"A": 1.0, "B": 2.0})   # live 的 A 保留, 只補上沒有的 B
        self.assertEqual(out["2026-09-25"]["source"], "live")
        self.assertEqual(out["2026-09-18"]["source"], "backfill")

    def test_current_week_is_written_as_live(self):
        out = history.merge_history({}, {"2026-10-02": {"A": 1.5}}, "2026-10-02")
        self.assertEqual(out["2026-10-02"], {"scores": {"A": 1.5}, "source": "live"})

    def test_save_load_roundtrip(self):
        from tests.helpers import temp_data
        with temp_data():
            recs = {"2026-10-02": {"scores": {"A": 1.0}, "source": "live"}}
            history.save_history(recs)
            self.assertEqual(history.load_history(), recs)


class Grading(unittest.TestCase):
    def test_letter(self):
        self.assertEqual([grading.letter(x) for x in (5, 4.0, 3, 2, 1)], ["A", "B", "C", "D", "E"])

    def _fund(self, **kw):
        base = {"sales_yoy": 0.1, "margin_exp_yoy_pp": 1.0, "earn_yoy": 0.1, "earn_accel": 0.0,
                "surprise_avg": 5.0, "fcf_yoy": 0.1, "roe_ttm": 10.0}
        return {**base, **kw}

    def test_too_few_factors_is_not_rated(self):
        fund = {"A": self._fund(), "B": {"sales_yoy": 0.1, "roe_ttm": 5.0}}        # B 只有 2 個因子
        _, fg, _ = grading.grade(fund, ["A", "B"])
        self.assertEqual(fg["B"]["grade"], "N/A")
        self.assertEqual(fg["B"]["n_factors"], 2)

    def test_loss_gets_the_worst_score_not_skipped(self):
        fund = {t: self._fund(earn_yoy=0.1 * (i + 1)) for i, t in enumerate("ABCDE")}
        fund["F"] = self._fund(earn_state="loss", earn_yoy=float("nan"))
        scores, _, _ = grading.grade(fund, list("ABCDEF"))
        self.assertEqual(scores["F"]["earn_yoy"], 1)

    def test_combined_is_30_percent_fundamental_70_percent_quant(self):
        fund = {t: self._fund(sales_yoy=0.1 * (i + 1)) for i, t in enumerate("ABCDE")}
        ab = {t: {"nav_score": float(i), "eligible": True} for i, t in enumerate("ABCDE")}
        _, fg, comb = grading.grade(fund, list("ABCDE"), ab)
        a = comb["E"]
        self.assertAlmostEqual(a["combined"], round(0.3 * fg["E"]["avg"] + 0.7 * a["quant_quintile"], 2))
        self.assertEqual(a["quant_quintile"], 5)

    def test_ineligible_quant_gets_no_overall(self):
        fund = {t: self._fund() for t in "ABCDE"}
        ab = {t: {"nav_score": 1.0, "eligible": t != "A"} for t in "ABCDE"}
        self.assertEqual(grading.grade(fund, list("ABCDE"), ab)[2]["A"]["overall"], "N/A")


if __name__ == "__main__":
    unittest.main()
