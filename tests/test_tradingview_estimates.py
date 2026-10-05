"""TradingView 資料來源與預估快照/修正因子測試 (全部 mock, 不連網)。"""
import unittest
from datetime import date, timedelta
from unittest import mock

from investor.data_sources import tradingview
from investor.navellier import estimates, factors, grading, fundamentals
from tests.helpers import temp_data


class TradingViewNormalization(unittest.TestCase):
    """TradingView 代號格式轉換。"""

    def test_normalize_brk_b(self):
        self.assertEqual(tradingview._normalize_ticker("BRK-B"), "BRK.B")

    def test_denormalize_brk_b(self):
        self.assertEqual(tradingview._denormalize_ticker("BRK.B"), "BRK-B")

    def test_normalize_regular(self):
        self.assertEqual(tradingview._normalize_ticker("AAPL"), "AAPL")


class TradingViewFetch(unittest.TestCase):
    """TradingView 資料抓取 (mocked)。"""

    def test_fetch_fundamentals_with_valid_data(self):
        """Mock Query 返回有效資料。"""
        import pandas as pd
        mock_df = pd.DataFrame({
            "name": ["BRK.B", "AAPL"],
            "type": ["stock", "stock"],
            "market_cap_basic": [1e12, 2e12],
            "total_revenue_fq_h": [[1e9, 2e9, 3e9, 4e9, 5e9, 6e9], [1e10, 2e10, 3e10, 4e10, 5e10, 6e10]],
            "net_income_fq_h": [[1e8, 2e8, 3e8, 4e8, 5e8, 6e8], [1e9, 2e9, 3e9, 4e9, 5e9, 6e9]],
            "earnings_per_share_diluted_fq_h": [[1.0, 1.1, 1.2, 1.3, 1.4, 1.5], [5.0, 5.5, 6.0, 6.5, 7.0, 7.5]],
            "free_cash_flow_fq_h": [[1e8, 2e8, 3e8, 4e8, 5e8, 6e8], [1e9, 2e9, 3e9, 4e9, 5e9, 6e9]],
        })

        with mock.patch("tradingview_screener.Query") as mock_query:
            mock_instance = mock.MagicMock()
            mock_instance.get_scanner_data.return_value = (2, mock_df)
            mock_query.return_value.set_markets.return_value.select.return_value.where.return_value.limit.return_value = mock_instance

            result = tradingview.fetch_fundamentals(["BRK-B", "AAPL"])

            self.assertIn("BRK-B", result)
            self.assertIn("AAPL", result)
            self.assertIsNotNone(result["BRK-B"]["total_revenue_fq_h"])
            self.assertIsNotNone(result["AAPL"]["total_revenue_fq_h"])


class EstimateSnapshot(unittest.TestCase):
    """預估快照存取。"""

    def test_snapshot_saves_and_loads(self):
        """snapshot() 存檔, _load_snapshots() 可讀出。"""
        with temp_data():
            tickers = ["AMZN", "ABT"]
            mock_tv = {
                "AMZN": {
                    "earnings_per_share_forecast_fq": 1.5,
                    "earnings_per_share_forecast_next_fq": 1.6,
                    "earnings_per_share_forecast_next_fy": 7.0,
                    "revenue_forecast_fq": 1e11,
                    "revenue_forecast_next_fq": 1.1e11,
                    "price_target_average": 200,
                    "recommendation_mark": 1.5,
                    "recommendation_total": 25,
                    "earnings_release_next_date": 1000000000,
                },
                "ABT": {
                    "earnings_per_share_forecast_fq": 1.0,
                    "earnings_per_share_forecast_next_fq": 1.1,
                    "earnings_per_share_forecast_next_fy": 4.5,
                    "revenue_forecast_fq": 1e10,
                    "revenue_forecast_next_fq": 1.05e10,
                    "price_target_average": 120,
                    "recommendation_mark": 1.2,
                    "recommendation_total": 20,
                    "earnings_release_next_date": 1000000100,
                }
            }

            with mock.patch("investor.data_sources.tradingview.fetch_estimates", return_value=mock_tv):
                today = "2026-10-01"
                estimates.snapshot(tickers, today=today)

            snaps = estimates._load_snapshots()
            self.assertEqual(len(snaps), 1)
            self.assertEqual(snaps[0]["date"], today)
            self.assertIn("AMZN", snaps[0]["data"])
            self.assertEqual(snaps[0]["data"]["AMZN"]["eps_next_fq"], 1.6)

    def test_snapshot_overwrites_same_day(self):
        """同一天的快照應該覆蓋, 不重複。"""
        with temp_data():
            tickers = ["AMZN"]
            today = "2026-10-01"

            mock_tv1 = {
                "AMZN": {
                    "earnings_per_share_forecast_next_fq": 1.5,
                    "earnings_per_share_forecast_next_fy": 6.0,
                    **{k: None for k in ["earnings_per_share_forecast_fq", "revenue_forecast_fq",
                                        "revenue_forecast_next_fq", "price_target_average",
                                        "recommendation_mark", "recommendation_total",
                                        "earnings_release_next_date"]}
                }
            }

            with mock.patch("investor.data_sources.tradingview.fetch_estimates", return_value=mock_tv1):
                estimates.snapshot(tickers, today=today)

            mock_tv2 = {
                "AMZN": {
                    "earnings_per_share_forecast_next_fq": 1.6,
                    "earnings_per_share_forecast_next_fy": 6.5,
                    **{k: None for k in ["earnings_per_share_forecast_fq", "revenue_forecast_fq",
                                        "revenue_forecast_next_fq", "price_target_average",
                                        "recommendation_mark", "recommendation_total",
                                        "earnings_release_next_date"]}
                }
            }

            with mock.patch("investor.data_sources.tradingview.fetch_estimates", return_value=mock_tv2):
                estimates.snapshot(tickers, today=today)

            snaps = estimates._load_snapshots()
            self.assertEqual(len(snaps), 1)
            self.assertEqual(snaps[0]["data"]["AMZN"]["eps_next_fq"], 1.6)


class EstimateRevision(unittest.TestCase):
    """預估修正計算。"""

    def test_revision_same_report_date(self):
        """同一財報日的快照可以比較修正。"""
        prev_snap = {
            "eps_next_fq": 1.5,
            "eps_next_fy": 6.0,
            "next_report": 1000000000,
        }
        cur_snap = {
            "eps_next_fq": 1.6,
            "eps_next_fy": 6.5,
            "next_report": 1000000000,
        }

        result = estimates.revision(prev_snap, cur_snap)
        self.assertAlmostEqual(result["est_revision"], 6.67, delta=0.1)
        self.assertAlmostEqual(result["est_revision_fy"], 8.33, delta=0.1)

    def test_revision_different_report_date_returns_none(self):
        """不同財報日 -> None (季度滾動)。"""
        prev_snap = {"eps_next_fq": 1.5, "next_report": 1000000000}
        cur_snap = {"eps_next_fq": 1.6, "next_report": 1000000100}

        result = estimates.revision(prev_snap, cur_snap)
        self.assertIsNone(result["est_revision"])

    def test_revision_negative_change(self):
        """修正下調。"""
        prev_snap = {"eps_next_fq": 1.5, "next_report": 1000000000}
        cur_snap = {"eps_next_fq": 1.35, "next_report": 1000000000}

        result = estimates.revision(prev_snap, cur_snap)
        self.assertAlmostEqual(result["est_revision"], -10.0, delta=0.1)

    def test_revision_base_too_small(self):
        """基期小於 0.01 -> None。"""
        prev_snap = {"eps_next_fq": 0.005, "next_report": 1000000000}
        cur_snap = {"eps_next_fq": 0.01, "next_report": 1000000000}

        result = estimates.revision(prev_snap, cur_snap)
        self.assertIsNone(result["est_revision"])

    def test_revision_missing_data_returns_none(self):
        """缺資料 -> None。"""
        prev_snap = {"eps_next_fq": None, "next_report": 1000000000}
        cur_snap = {"eps_next_fq": 1.5, "next_report": 1000000000}

        result = estimates.revision(prev_snap, cur_snap)
        self.assertIsNone(result["est_revision"])

    def test_compute_revisions_with_28_day_window(self):
        """compute_revisions 找最接近 28 天前的快照。"""
        with temp_data():
            today = date(2026, 10, 5)
            target_prev = today - timedelta(days=28)

            snapshots = [
                {
                    "date": str(target_prev),
                    "data": {
                        "AMZN": {
                            "eps_next_fq": 1.5,
                            "eps_next_fy": 6.0,
                            "next_report": 1000000000,
                        }
                    }
                },
                {
                    "date": str(today),
                    "data": {
                        "AMZN": {
                            "eps_next_fq": 1.6,
                            "eps_next_fy": 6.5,
                            "next_report": 1000000000,
                        }
                    }
                },
            ]

            revisions = estimates.compute_revisions(snapshots, str(today))
            self.assertIn("AMZN", revisions)
            self.assertAlmostEqual(revisions["AMZN"]["est_revision"], 6.67, delta=0.1)
            self.assertEqual(revisions["AMZN"]["est_days"], 28)

    def test_compute_revisions_ignores_old_snapshots_less_than_7_days(self):
        """間隔不足 7 天的舊快照應該忽略。"""
        with temp_data():
            today = date(2026, 10, 5)

            snapshots = [
                {
                    "date": str(today - timedelta(days=3)),
                    "data": {"AMZN": {"eps_next_fq": 1.0, "next_report": 1000000000}}
                },
                {
                    "date": str(today),
                    "data": {"AMZN": {"eps_next_fq": 1.6, "next_report": 1000000000}}
                },
            ]

            revisions = estimates.compute_revisions(snapshots, str(today))
            self.assertEqual(revisions, {})

    def test_compute_revisions_returns_empty_for_no_snapshots(self):
        """無快照時回傳空字典。"""
        revisions = estimates.compute_revisions([])
        self.assertEqual(revisions, {})


class TradingViewGapFilling(unittest.TestCase):
    """TV 補 Yahoo 空欄位。"""

    def test_fill_tv_gaps_replaces_none_latest(self):
        """Yahoo 最新值為 None 時用 TV 取代。"""
        with temp_data():
            fund = {
                "AMZN": {
                    "_rev": [1e9, 2e9, 3e9, 4e9, 5e9, None],
                    "_ni": [1e8, 2e8, 3e8, 4e8, 5e8, 6e8],
                    "_eps": [1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
                    "_fcf": [1e8, 2e8, 3e8, 4e8, 5e8, None],
                    "_opinc": [1e8, 2e8, 3e8, 4e8, 5e8, 6e8],
                    "_eq": [1e9, 2e9, 3e9, 4e9, 5e9, None],
                }
            }

            tv_data = {
                "AMZN": {
                    "total_revenue_fq_h": [6e9, 5.5e9, 5e9, 4.5e9, 4e9, 3.5e9],
                    "net_income_fq_h": None,
                    "earnings_per_share_diluted_fq_h": None,
                    "free_cash_flow_fq_h": [1.5e8, 1.4e8, 1.3e8, 1.2e8, 1.1e8, 1e8],
                }
            }

            with mock.patch("investor.data_sources.tradingview.fetch_fundamentals", return_value=tv_data):
                fundamentals._fill_tv_gaps(fund, ["AMZN"])

            # TV 前 6 個: [6e9, 5.5e9, 5e9, 4.5e9, 4e9, 3.5e9], 反轉: [3.5e9, 4e9, 4.5e9, 5e9, 5.5e9, 6e9]
            self.assertEqual(fund["AMZN"]["_rev"], [3.5e9, 4e9, 4.5e9, 5e9, 5.5e9, 6e9])
            # TV 前 6 個: [1.5e8, 1.4e8, 1.3e8, 1.2e8, 1.1e8, 1e8], 反轉: [1e8, 1.1e8, 1.2e8, 1.3e8, 1.4e8, 1.5e8]
            self.assertEqual(fund["AMZN"]["_fcf"], [1e8, 1.1e8, 1.2e8, 1.3e8, 1.4e8, 1.5e8])
            self.assertEqual(fund["AMZN"]["_opinc"], [1e8, 2e8, 3e8, 4e8, 5e8, 6e8])
            self.assertEqual(fund["AMZN"]["_eq"], [1e9, 2e9, 3e9, 4e9, 5e9, None])
            self.assertEqual(fund["AMZN"]["_src"]["_rev"], "tv")
            self.assertIn("_fcf", fund["AMZN"]["_src"])

    def test_fill_tv_gaps_preserves_complete_yahoo_data(self):
        """Yahoo 資料完整時不用 TV 取代。"""
        with temp_data():
            fund = {
                "AMZN": {
                    "_rev": [1e9, 2e9, 3e9, 4e9, 5e9, 6e9],
                    "_ni": [1e8, 2e8, 3e8, 4e8, 5e8, 6e8],
                    "_eps": [1.0, 1.1, 1.2, 1.3, 1.4, 1.5],
                    "_fcf": None,
                }
            }

            tv_data = {
                "AMZN": {
                    "total_revenue_fq_h": [6e9, 5.5e9, 5e9, 4.5e9, 4e9, 3.5e9],
                    "free_cash_flow_fq_h": [1.5e8, 1.4e8, 1.3e8, 1.2e8, 1.1e8, 1e8],
                }
            }

            with mock.patch("investor.data_sources.tradingview.fetch_fundamentals", return_value=tv_data):
                fundamentals._fill_tv_gaps(fund, ["AMZN"])

            self.assertEqual(fund["AMZN"]["_rev"], [1e9, 2e9, 3e9, 4e9, 5e9, 6e9])
            self.assertEqual(fund["AMZN"]["_fcf"], [1e8, 1.1e8, 1.2e8, 1.3e8, 1.4e8, 1.5e8])

    def test_fill_tv_gaps_saves_surprise_percentages(self):
        """TV 最新季驚喜應存進 raw 資料。"""
        with temp_data():
            fund = {
                "AMZN": {"_rev": [1e9, 2e9, 3e9, 4e9, 5e9, None]}
            }

            tv_data = {
                "AMZN": {
                    "total_revenue_fq_h": [6e9, 5.5e9, 5e9, 4.5e9, 4e9, 3.5e9],
                    "eps_surprise_percent_fq": 5.5,
                    "revenue_surprise_percent_fq": 2.3,
                }
            }

            with mock.patch("investor.data_sources.tradingview.fetch_fundamentals", return_value=tv_data):
                fundamentals._fill_tv_gaps(fund, ["AMZN"])

            self.assertEqual(fund["AMZN"]["eps_surprise_last_pct"], 5.5)
            self.assertEqual(fund["AMZN"]["rev_surprise_last_pct"], 2.3)


class EstimateRevisionFactor(unittest.TestCase):
    """est_revision 因子在 FACTORS 中且正常作用。"""

    def test_est_revision_in_factors(self):
        """est_revision 是有效因子。"""
        self.assertIn("est_revision", factors.FACTORS)

    def test_factor_value_handles_est_revision(self):
        """factor_value 處理 est_revision。"""
        r = {"est_revision": 5.5}
        self.assertAlmostEqual(factors.factor_value(r, "est_revision"), 5.5)

        r = {"est_revision": None}
        self.assertIsNone(factors.factor_value(r, "est_revision"))

    def test_grading_without_snapshots_gives_same_result(self):
        """沒有預估修正時, 評級應與之前相同 (N/A 與無修正值)。"""
        fund = {
            "A": {"sales_yoy": 0.1, "margin_exp_yoy_pp": 1.0, "earn_yoy": 0.15, "earn_accel": 1.0,
                  "surprise_avg": 5.0, "fcf_yoy": 0.2, "roe_ttm": 15.0},
            "B": {"sales_yoy": 0.05, "margin_exp_yoy_pp": 0.5, "earn_yoy": 0.08, "earn_accel": 0.5,
                  "surprise_avg": 2.0, "fcf_yoy": 0.1, "roe_ttm": 10.0},
            "C": {"sales_yoy": 0.02, "margin_exp_yoy_pp": 0.2, "earn_yoy": 0.03, "earn_accel": 0.0,
                  "surprise_avg": 1.0, "fcf_yoy": 0.05, "roe_ttm": 5.0},
        }
        tickers = ["A", "B", "C"]

        scores, fg, _ = grading.grade(fund, tickers)

        # 檢查都有資料的因子被評級
        for t in tickers:
            if fg[t]["grade"] != "N/A":
                self.assertIn("sales_yoy", scores.get(t, {}))
                self.assertNotIn("est_revision", scores.get(t, {}))


if __name__ == "__main__":
    unittest.main()
