"""自訂清單分類: 解析、組內評級、組太小不評、分組顯示、分類改了就要重算。"""
import unittest
from datetime import date

import pandas as pd

from investor.navellier import alpha_beta, grading, groups, rating
from investor.web import autorefresh, presenters
from investor.web.render import tabs
from tests.helpers import temp_data


class ParseAndAssign(unittest.TestCase):
    def test_parse_basic_and_fullwidth(self):
        g = groups.parse_text("晶片: NVDA amd\n# 註解\n航運：DHT, FRO  # 尾註\n\n")
        self.assertEqual(g, [("晶片", ["NVDA", "AMD"]), ("航運", ["DHT", "FRO"])])

    def test_dot_ticker_normalised(self):
        self.assertEqual(groups.parse_text("其他: BRK.B")[0][1], ["BRK-B"])

    def test_errors(self):
        with self.assertRaises(ValueError):
            groups.parse_text("沒有冒號 NVDA")
        with self.assertRaises(ValueError):
            groups.parse_text("A: NVDA\nB: NVDA")             # 一檔不能在兩類
        with self.assertRaises(ValueError):
            groups.parse_text("A: NV$DA!!")

    def test_assign_ungrouped_last(self):
        amap, order = groups.assign(["AAA", "BBB", "CCC"], [("G1", ["AAA"]), ("G2", ["ZZZ"])])
        self.assertEqual(amap, {"AAA": "G1", "BBB": groups.UNGROUPED, "CCC": groups.UNGROUPED})
        self.assertEqual(order, ["G1", groups.UNGROUPED])      # 清單裡沒有成員的分類不顯示

    def test_roundtrip_and_active(self):
        with temp_data():
            self.assertEqual(groups.active("watchlist", ["AAA"]), (None, []))     # 沒設分類 = 不分組
            g = [("G1", ["AAA", "BBB"])]
            groups.save(g)
            self.assertEqual(groups.load(), g)
            self.assertEqual(groups.parse_text(groups.dump_text(g)), g)
            self.assertIsNotNone(groups.active("watchlist", ["AAA", "BBB"])[0])
            self.assertEqual(groups.active("sp500", ["AAA"]), (None, []))         # 只作用於自訂清單
            sig = groups.signature("watchlist", ["AAA", "BBB"])
            groups.save([("G2", ["AAA", "BBB"])])
            self.assertNotEqual(groups.signature("watchlist", ["AAA", "BBB"]), sig)
            self.assertIsNone(groups.signature("ndx", ["AAA"]))


def _fund(v):
    return {"sales_yoy": v, "margin_exp_yoy_pp": v, "earn_yoy": v, "earn_accel": v, "surprise_avg": v,
            "fcf_yoy": v, "roe_ttm": v, "earn_state": "ok", "fcf_state": "ok"}


class GroupedGrading(unittest.TestCase):
    def setUp(self):
        # 大組 big (6 檔) 與小組 small (3 檔); 全部因子同值, 方便預期名次
        self.tk = [f"B{i}" for i in range(6)] + [f"S{i}" for i in range(3)]
        self.fund = {t: _fund(float(i)) for i, t in enumerate(self.tk)}
        self.ab = {t: {"nav_score": float(i), "eligible": True} for i, t in enumerate(self.tk)}
        self.amap = {t: ("big" if t.startswith("B") else "small") for t in self.tk}

    def test_no_groups_identical_to_before(self):
        a = grading.grade(self.fund, self.tk, self.ab)
        b = grading.grade(self.fund, self.tk, self.ab, None)
        self.assertEqual(a, b)
        self.assertTrue(all(v["grade"] != "N/A" for v in a[1].values()))

    def test_small_group_not_rated(self):
        _, fg, comb = grading.grade(self.fund, self.tk, self.ab, self.amap)
        for t in ("S0", "S1", "S2"):
            self.assertEqual(fg[t]["grade"], "N/A")
            self.assertTrue(fg[t].get("small_group"))
            self.assertEqual(comb[t]["overall"], "N/A")
        self.assertTrue(all(fg[f"B{i}"]["grade"] != "N/A" for i in range(6)))

    def test_ranking_is_within_group(self):
        """B 組最強者 (B5) 在整體排名裡不是最高 (S2 數值更大), 但組內仍應是 A、最弱者是 E。"""
        _, fg, comb = grading.grade(self.fund, self.tk, self.ab, self.amap)
        self.assertEqual(fg["B5"]["grade"], "A")
        self.assertEqual(fg["B0"]["grade"], "E")
        self.assertEqual(comb["B5"]["quant_quintile"], 5)
        self.assertEqual(comb["B0"]["quant_quintile"], 1)

    def test_group_with_few_quant_eligible_gets_no_overall(self):
        """組有 6 檔、但只有 4 檔有 52 週資料: 量化分位沒意義, 綜合評級一律 N/A (與量化評級 N/A 一致)。"""
        ab = {t: dict(v) for t, v in self.ab.items()}
        for t in ("B0", "B1"):
            ab[t]["eligible"] = False
        _, fg, comb = grading.grade(self.fund, self.tk, ab, self.amap)
        self.assertNotEqual(fg["B5"]["grade"], "N/A")                       # 基本面仍可評
        self.assertEqual(comb["B5"]["overall"], "N/A")
        _, _, comb2 = grading.grade(self.fund, self.tk, ab, None)           # 不分組: 照舊
        self.assertNotEqual(comb2["B5"]["overall"], "N/A")

    def test_regrade_quant_by_group(self):
        ab = {k: dict(v) for k, v in self.ab.items()}
        rating._regrade_quant(ab, self.amap)
        self.assertEqual(ab["B5"]["nav_grade"], "A")
        self.assertEqual(ab["B0"]["nav_grade"], "E")
        self.assertEqual(ab["S2"]["nav_grade"], "N/A")             # 小組不評
        ab2 = {k: dict(v) for k, v in self.ab.items()}
        rating._regrade_quant(ab2, None)                           # 不分組: 不動
        self.assertNotIn("nav_grade", ab2["B0"])


class GroupedDisplay(unittest.TestCase):
    def _df(self):
        return pd.DataFrame([
            {"代號": "A1", "分類": "晶片", "綜合評級": "A", "RSI": 70.0, "趨勢": "多頭", "距52週高%": -5.0, "動能_tip": ""},
            {"代號": "A2", "分類": "晶片", "綜合評級": "N/A", "RSI": 50.0, "趨勢": "空頭", "距52週高%": -15.0, "動能_tip": ""},
            {"代號": "B1", "分類": "航運", "綜合評級": "N/A", "RSI": 40.0, "趨勢": None, "距52週高%": None, "動能_tip": ""}])

    def test_summaries(self):
        df = self._df()
        s = presenters.tech_summary(df[df["分類"] == "晶片"])
        self.assertIn("2 檔", s); self.assertIn("1 檔有評級", s); self.assertIn("RSI 中位 60", s); self.assertIn("多頭 1/2", s)
        self.assertIn("未評等級", presenters.tech_summary(df[df["分類"] == "航運"]))
        f = pd.DataFrame([{"代號": "A1", "sales_yoy": 10.0, "roe_ttm": 20.0, "財報結果": "雙 beat"},
                          {"代號": "A2", "sales_yoy": 30.0, "roe_ttm": 10.0, "財報結果": "雙 miss"}])
        fs = presenters.fund_summary(f)
        self.assertIn("營收年增中位 +20%", fs); self.assertIn("雙 beat 1/2", fs)

    def test_split_groups_order_and_fallback(self):
        parts = tabs._split_groups(self._df(), ["航運", "晶片"])
        self.assertEqual([g for g, _ in parts], ["航運", "晶片"])
        self.assertIsNone(tabs._split_groups(self._df().drop(columns="分類"), []))        # 舊資料: 照舊整張顯示
        self.assertIsNone(tabs._split_groups(self._df().assign(分類=None), []))

    def test_body_renders_one_table_per_group(self):
        df = self._df()
        fac = pd.DataFrame([{"代號": t, "分類": g} for t, g in zip(df["代號"], df["分類"])])
        N = {"rows": df, "factors": fac, "group_order": ["晶片", "航運"], "tickers": list(df["代號"]), "missing": []}
        with temp_data():
            html = tabs._navellier_body(N, "watchlist", {})
        self.assertEqual(html.count('<h3 class="grp">'), 4)        # 2 組 x (主表 + 因子明細)
        self.assertEqual(html.count("<table"), 4)
        self.assertIn("各自分類", html)


class DueOnGroupChange(unittest.TestCase):
    def test_group_change_triggers_rating(self):
        today = date(2026, 10, 7)
        cutoff = str(alpha_beta.complete_week_cutoff(today))
        with temp_data():
            groups.save([("G1", ["AAA", "BBB"])])
            sig = groups.signature("watchlist", ["AAA", "BBB"])
            st = {"nav": {"watchlist": {"asof": cutoff, "tech_date": str(today), "tickers": ["AAA", "BBB"], "groups_sig": sig}}}
            self.assertIsNone(autorefresh.due(st, "watchlist", today))
            groups.save([("G1", ["AAA"]), ("G2", ["BBB"])])
            self.assertEqual(autorefresh.due(st, "watchlist", today), "nav")


if __name__ == "__main__":
    unittest.main()
