"""同業比較基準: 對「自己產業的全市場」評級、TradingView 因子、同業價格分數的快取。"""
import unittest
from unittest import mock

from investor.navellier import grading, peers
from tests.helpers import temp_data


def _f(v):
    return {"sales_yoy": v, "earn_yoy": v, "earn_accel": v, "fcf_yoy": v, "roe_ttm": v, "surprise_avg": v,
            "margin_exp_yoy_pp": v, "earn_state": "ok", "fcf_state": "ok"}


class Percentile(unittest.TestCase):
    def test_percentile_and_quintile(self):
        ref = list(range(1, 11))
        self.assertEqual(grading.percentile(10, ref), 0.95)
        self.assertEqual(grading.percentile(1, ref), 0.05)
        self.assertEqual(grading.quintile_of(0.95), 5)
        self.assertEqual(grading.quintile_of(0.05), 1)
        self.assertEqual(grading.quintile_of(0.2), 2)          # 邊界: 0.2 屬第 2 等
        self.assertEqual(grading.quintile_of(1.0), 5)
        self.assertIsNone(grading.percentile(5, []))

    def test_ties_get_half_credit(self):
        self.assertEqual(grading.percentile(5, [5, 5, 5, 5]), 0.5)


class GradeVsPeers(unittest.TestCase):
    def setUp(self):
        # 同業 20 檔: 因子值 1..20, 52 週 Alpha/SD 也是 1..20
        self.peer_fund = {"Semis": {f"P{i}": _f(float(i)) for i in range(1, 21)}}
        self.peer_nav = {"Semis": [float(i) for i in range(1, 21)]}
        self.tk = ["TOP", "MID", "LOW", "ETF", "TINY"]
        self.target_fund = {"TOP": _f(25.0), "MID": _f(10.5), "LOW": _f(-5.0), "TINY": _f(9.0)}
        self.industry_of = {"TOP": "Semis", "MID": "Semis", "LOW": "Semis", "TINY": "Tiny"}
        self.peer_fund["Tiny"] = {f"T{i}": _f(float(i)) for i in range(3)}      # 同業只有 3 檔: 太少
        self.ab = {"TOP": {"nav_score": 30.0, "eligible": True}, "MID": {"nav_score": 10.5, "eligible": True},
                   "LOW": {"nav_score": -3.0, "eligible": True}, "TINY": {"nav_score": 5.0, "eligible": True}}

    def run_grade(self, **kw):
        return grading.grade_vs_peers(self.target_fund, self.tk, self.industry_of, self.peer_fund,
                                      kw.get("ab", self.ab), kw.get("peer_nav", self.peer_nav))

    def test_ranked_against_all_peers_not_the_list(self):
        sc, fg, comb, nav = self.run_grade()
        self.assertEqual((fg["TOP"]["grade"], fg["LOW"]["grade"]), ("A", "E"))
        self.assertEqual(comb["TOP"]["overall"], "A")
        self.assertEqual(comb["LOW"]["overall"], "E")
        self.assertEqual(nav["TOP"]["grade"], "A")
        self.assertEqual(nav["LOW"]["grade"], "E")
        self.assertEqual(comb["TOP"]["n_peers"], 20)
        self.assertIn(comb["MID"]["overall"], "CD")                # 中間值落在中間

    def test_no_peer_basis_is_na(self):
        _, fg, comb, nav = self.run_grade()
        for t in ("ETF", "TINY"):                                   # ETF: 沒有產業; TINY: 同業太少
            self.assertEqual(fg[t]["grade"], "N/A")
            self.assertEqual(comb[t]["overall"], "N/A")
            self.assertNotIn(t, nav)

    def test_single_stock_list_still_rated(self):
        """清單只有一檔也能評 (舊算法在一檔的清單裡沒有意義)。"""
        _, fg, comb, _ = grading.grade_vs_peers({"TOP": _f(25.0)}, ["TOP"], {"TOP": "Semis"}, self.peer_fund,
                                                {"TOP": {"nav_score": 30.0, "eligible": True}}, self.peer_nav)
        self.assertEqual(comb["TOP"]["overall"], "A")

    def test_ineligible_quant_gives_no_overall(self):
        ab = {**self.ab, "TOP": {"nav_score": 30.0, "eligible": False}}
        _, fg, comb, nav = self.run_grade(ab=ab)
        self.assertEqual(fg["TOP"]["grade"], "A")                   # 基本面仍可評
        self.assertEqual(comb["TOP"]["overall"], "N/A")
        self.assertNotIn("TOP", nav)

    def test_too_few_factors_is_na(self):
        self.target_fund["TOP"] = {"sales_yoy": 25.0, "earn_yoy": 25.0}
        _, fg, _, _ = self.run_grade()
        self.assertEqual(fg["TOP"]["grade"], "N/A")


class TvFactors(unittest.TestCase):
    def entry(self, **kw):
        base = {"total_revenue_fq_h": [150.0, 140.0, 130.0, 120.0, 100.0, 90.0],        # 最新在前
                "net_income_fq_h": [30.0, 28.0, 25.0, 22.0, 20.0, 18.0],
                "earnings_per_share_diluted_fq_h": [3.0, 2.8, 2.5, 2.2, 2.0, 1.8],
                "free_cash_flow_fq_h": [15.0, 14.0, 12.0, 11.0, 10.0, 9.0],
                "return_on_equity": 22.5, "eps_surprise_percent_fq": 4.0}
        return {**base, **kw}

    def test_series_order_and_overrides(self):
        f = peers.tv_factors(self.entry())
        self.assertAlmostEqual(f["sales_yoy"], 150 / 100 - 1)               # 最新 / 4 季前
        self.assertAlmostEqual(f["earn_yoy"], 3.0 / 2.0 - 1)
        self.assertEqual((f["roe_ttm"], f["surprise_avg"]), (22.5, 4.0))
        self.assertNotIn("margin_exp_yoy_pp", f)                             # TradingView 沒有營業利益歷史

    def test_missing_pieces_do_not_crash(self):
        f = peers.tv_factors({"total_revenue_fq_h": None, "return_on_equity": float("nan")})
        self.assertNotIn("roe_ttm", f)
        self.assertNotIn("sales_yoy", f)


class FetchIndustry(unittest.TestCase):
    def test_preferred_shares_excluded_and_dupes_by_market_cap(self):
        import pandas as pd
        from investor.data_sources import tradingview
        df = pd.DataFrame({"name": ["AAA", "RNR/PF", "BBB", "BBB"], "type": ["stock"] * 4,
                           "market_cap_basic": [5e9, 3e9, 2e9, 9e9], "industry": ["Semis"] * 4,
                           "return_on_equity": [1.0, 2.0, 3.0, 4.0]})
        with mock.patch("investor.data_sources.tradingview.Query") as q:
            q.return_value.set_markets.return_value.select.return_value.where.return_value.limit.return_value.get_scanner_data.return_value = (4, df)
            out = tradingview.fetch_industry(["Semis"])
        self.assertEqual(set(out), {"AAA", "BBB"})
        self.assertEqual(out["BBB"]["return_on_equity"], 4.0)             # 同名取市值大的


class BuildAndScores(unittest.TestCase):
    def test_build_groups_peers_by_industry(self):
        targets = {"AAA": {**TvFactors().entry(), "industry": "Semis"}}
        rows = {"AAA": {**TvFactors().entry(), "industry": "Semis"}, "BBB": {**TvFactors().entry(), "industry": "Semis"},
                "CCC": {**TvFactors().entry(), "industry": "Other"}}
        out = peers.build(["AAA", "ETF1"], targets, rows)
        self.assertEqual(out["industry_of"], {"AAA": "Semis"})              # ETF 沒有產業
        self.assertEqual(set(out["peer_fund"]), {"Semis"})
        self.assertEqual(set(out["peer_fund"]["Semis"]), {"AAA", "BBB"})    # 只留目標用到的產業
        self.assertIn("AAA", out["target_fund"])

    def test_nav_scores_cached_per_week_and_resumable(self):
        calls = []

        def fake(part, verbose=False):
            calls.append(list(part))
            return {t: {"nav_score": 1.0, "eligible": t != "BAD"} for t in part}, [], {}
        with temp_data(), mock.patch.object(peers.alpha_beta, "compute", side_effect=fake):
            r1 = peers.nav_scores(["A", "B", "BAD"], "2026-10-02")
            self.assertEqual(r1, {"A": 1.0, "B": 1.0, "BAD": None})
            peers.nav_scores(["A", "B", "BAD"], "2026-10-02")
            self.assertEqual(len(calls), 1)                                  # 同一週: 不重抓
            peers.nav_scores(["A", "C"], "2026-10-02")
            self.assertEqual(calls[-1], ["C"])                               # 只抓新增的
            peers.nav_scores(["A"], "2026-10-09")
            self.assertEqual(calls[-1], ["A"])                               # 新的一週: 重算

    def test_nav_scores_progress_survives_failure(self):
        peers_chunk = peers.CHUNK
        n = {"i": 0}

        def flaky(part, verbose=False):
            n["i"] += 1
            if n["i"] == 2:
                raise RuntimeError("rate limited")
            return {t: {"nav_score": 2.0, "eligible": True} for t in part}, [], {}
        with temp_data(), mock.patch.object(peers.alpha_beta, "compute", side_effect=flaky), \
                mock.patch.object(peers, "CHUNK", 2):
            tk = ["A", "B", "C", "D"]
            with self.assertRaises(RuntimeError):
                peers.nav_scores(tk, "2026-10-02")                           # 第二批失敗
            n["i"] = 10
            out = peers.nav_scores(tk, "2026-10-02")                         # 接續: 第一批不重抓
            self.assertEqual(set(out.values()), {2.0})
            self.assertEqual(n["i"], 11)
        self.assertEqual(peers.CHUNK, peers_chunk)


if __name__ == "__main__":
    unittest.main()
