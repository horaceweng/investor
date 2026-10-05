"""技術面與預估整合測試。"""
import unittest
from investor.navellier import estimates, grading
from tests.helpers import temp_data


class SnapshotBackwardCompatibility(unittest.TestCase):
    """舊格式快照相容性。"""

    def test_load_old_snapshot_without_tech(self):
        """讀取沒有 tech 欄位的舊快照不會崩潰。"""
        with temp_data():
            # 手動寫入舊格式快照
            import json
            from investor import paths
            old_snap = {
                "date": "2026-09-05",
                "data": {
                    "AMZN": {
                        "eps_fq": 1.5,
                        "eps_next_fq": 1.6,
                        "eps_next_fy": 7.0,
                        "rev_fq": 1e11,
                        "rev_next_fq": 1.1e11,
                        "target": 200,
                        "rec": 1.5,
                        "rec_n": 25,
                        "next_report": 1000000000,
                    }
                }
            }

            paths.ESTIMATES.parent.mkdir(parents=True, exist_ok=True)
            paths.ESTIMATES.write_text(json.dumps(old_snap, ensure_ascii=False) + "\n")

            # 讀取應該成功
            snaps = estimates._load_snapshots()
            self.assertEqual(len(snaps), 1)
            self.assertNotIn("tech", snaps[0]["data"]["AMZN"])  # 舊格式沒有 tech

            # 計算修正應該也成功 (會忽略沒有 tech 的快照)
            revisions = estimates.compute_revisions(snaps, "2026-09-05")
            self.assertEqual(revisions, {})  # 沒有足夠舊的快照


class GradingUnchangedWithoutTechnicals(unittest.TestCase):
    """確認加了技術面後評級輸出不變。"""

    def test_grading_ignores_technicals(self):
        """評級只看 7 個基本面因子, 技術面不影響。"""
        fund = {
            "A": {"sales_yoy": 0.1, "margin_exp_yoy_pp": 1.0, "earn_yoy": 0.15, "earn_accel": 1.0,
                  "surprise_avg": 5.0, "fcf_yoy": 0.2, "roe_ttm": 15.0, "est_revision": None},
            "B": {"sales_yoy": 0.05, "margin_exp_yoy_pp": 0.5, "earn_yoy": 0.08, "earn_accel": 0.5,
                  "surprise_avg": 2.0, "fcf_yoy": 0.1, "roe_ttm": 10.0, "est_revision": None},
        }
        tickers = ["A", "B"]

        # 沒有 est_revision (或為 None) 時的評級
        scores1, fg1, _ = grading.grade(fund, tickers)

        # 加入 est_revision 值
        fund["A"]["est_revision"] = 5.0
        fund["B"]["est_revision"] = -3.0

        scores2, fg2, _ = grading.grade(fund, tickers)

        # 評級應該完全相同 (est_revision 被計入因子, 但不改變原有 7 個因子的計算)
        self.assertEqual(fg1["A"]["avg"], fg2["A"]["avg"])
        self.assertEqual(fg1["B"]["avg"], fg2["B"]["avg"])
        self.assertEqual(fg1["A"]["grade"], fg2["A"]["grade"])
        self.assertEqual(fg1["B"]["grade"], fg2["B"]["grade"])


if __name__ == "__main__":
    unittest.main()


class SnapshotRoundTrip(unittest.TestCase):
    """快照存的 tech 子物件欄位名與 TradingView 原始欄位不同, 還原結果必須與即時計算一致 (曾因欄位名對不上而全為 None)。"""

    def test_roundtrip_matches_live(self):
        from datetime import datetime, timezone
        from investor.navellier import technicals
        raw = {"close": 250.0, "RSI": 72.0, "SMA50": 240.0, "SMA200": 200.0, "price_52_week_high": 280.0,
               "Recommend.All": 0.6, "MACD.macd": 1.5, "MACD.signal": 1.0,
               "earnings_release_next_date": datetime(2026, 10, 10, tzinfo=timezone.utc).timestamp()}
        live = technicals.derive_technicals(raw, "2026-10-05")
        snap = {"close": 250.0, "rsi": live["rsi"], "sma50": 240.0, "sma200": 200.0, "high52": 280.0,
                "macd_hist": live["macd_hist"], "rec": 0.6, "next_report": live["next_report"]}
        self.assertEqual(technicals.derive_from_snapshot(snap, "2026-10-05"), live)
        self.assertEqual((live["rsi_zone"], live["trend"], live["macd_dir"], live["days_to_report"]), ("過熱", "多頭", "轉強", 5))

    def test_report_today_counts(self):
        from datetime import datetime, timezone
        from investor.navellier import technicals
        r = technicals.derive_technicals({"earnings_release_next_date": datetime(2026, 10, 5, 12, tzinfo=timezone.utc).timestamp()}, "2026-10-05")
        self.assertEqual((r["days_to_report"], r["report_soon"]), (0, True))
