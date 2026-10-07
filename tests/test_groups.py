"""自訂清單分類: 解析、組內評級、組太小不評、分組顯示、分類改了就要重算。"""
import unittest
from datetime import date

import pandas as pd

from investor.navellier import alpha_beta, groups, rating, settings
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
        self.assertIn("無評級", presenters.tech_summary(df[df["分類"] == "航運"]))
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
            settings.save_watchlist(["AAA", "BBB"])
            groups.save([("G1", ["AAA", "BBB"])])
            sig = groups.signature("watchlist", ["AAA", "BBB"])
            st = {"nav": {"watchlist": {"asof": cutoff, "tech_date": str(today), "tickers": ["AAA", "BBB"], "groups_sig": sig, "method": rating.METHOD_VERSION}}}
            self.assertIsNone(autorefresh.due(st, "watchlist", today))
            groups.save([("G1", ["AAA"]), ("G2", ["BBB"])])
            self.assertEqual(autorefresh.due(st, "watchlist", today), "nav")


class DueOnWatchlistChange(unittest.TestCase):
    """觀察清單增減 (編輯器、☆ 點選、直接改檔) 都要觸發重算, 否則新股票要等下一週才出現。"""

    def _state(self, today, tickers):
        return {"nav": {"watchlist": {"asof": str(alpha_beta.complete_week_cutoff(today)), "tech_date": str(today),
                                      "tickers": tickers, "groups_sig": None, "method": rating.METHOD_VERSION}}}

    def test_added_or_removed_ticker_triggers(self):
        today = date(2026, 10, 7)
        with temp_data():
            settings.save_watchlist(["AAA", "BBB"])
            self.assertIsNone(autorefresh.due(self._state(today, ["BBB", "AAA"]), "watchlist", today))   # 順序不同不算變
            self.assertEqual(autorefresh.due(self._state(today, ["AAA"]), "watchlist", today), "nav")    # 新增
            self.assertEqual(autorefresh.due(self._state(today, ["AAA", "BBB", "CCC"]), "watchlist", today), "nav")  # 移除

    def test_other_pools_ignore_watchlist(self):
        today = date(2026, 10, 7)
        with temp_data():
            settings.save_watchlist(["AAA"])
            st = {"nav": {"ndx": {"asof": str(alpha_beta.complete_week_cutoff(today)), "tech_date": str(today),
                                  "tickers": ["X", "Y"], "groups_sig": None, "method": rating.METHOD_VERSION}}}
            self.assertIsNone(autorefresh.due(st, "ndx", today))


class AddTickers(unittest.TestCase):
    BASE = [("晶片", ["NVDA", "AMD"]), ("航運", ["DHT"])]

    def test_into_existing_and_new_group(self):
        out = groups.add_tickers({"avgo": "晶片", "RKLB": "太空"}, self.BASE)
        self.assertEqual(out, [("晶片", ["NVDA", "AMD", "AVGO"]), ("航運", ["DHT"]), ("太空", ["RKLB"])])

    def test_moves_from_old_group_and_drops_empty(self):
        out = groups.add_tickers({"DHT": "晶片"}, self.BASE)
        self.assertEqual(out, [("晶片", ["NVDA", "AMD", "DHT"])])          # 航運空了就不留

    def test_does_not_mutate_input_and_validates(self):
        groups.add_tickers({"AVGO": "晶片"}, self.BASE)
        self.assertEqual(self.BASE[0][1], ["NVDA", "AMD"])
        for bad in ({"N$V!": "晶片"}, {"AVGO": ""}, {"AVGO": "a:b"}, {"AVGO": "x" * 41}):
            with self.assertRaises(ValueError):
                groups.add_tickers(bad, self.BASE)


class UnclassifiedPanel(unittest.TestCase):
    def test_panel_lists_only_unclassified_and_only_with_groups(self):
        with temp_data():
            settings.save_watchlist(["NVDA", "AMD", "NEWCO"])
            self.assertNotIn('id="grnew"', tabs._watchlist_editor("watchlist", None))      # 沒設分類: 不提示
            groups.save([("晶片", ["NVDA", "AMD"])])
            html = tabs._watchlist_editor("watchlist", None)
            self.assertIn('id="grnew"', html)
            self.assertIn('data-gt="NEWCO"', html)
            self.assertNotIn('data-gt="NVDA"', html)
            self.assertIn("<option value=\"晶片\">", html)
            groups.save([("晶片", ["NVDA", "AMD", "NEWCO"])])
            self.assertNotIn('id="grnew"', tabs._watchlist_editor("watchlist", None))      # 都分類了: 提示消失


class AssignApi(unittest.TestCase):
    def test_assign_saves_and_starts_rating(self):
        from unittest import mock
        from investor.web import api
        with temp_data():
            groups.save([("晶片", ["NVDA"])])
            with mock.patch.object(api.jobs, "start_job", return_value=True) as start:
                code, body = api.groups_assign({"assign": {"AMD": "晶片", "ARM": "新類"}})
            self.assertEqual((code, body["started"]), (200, True))
            start.assert_called_once_with("nav")
            self.assertEqual(groups.load(), [("晶片", ["NVDA", "AMD"]), ("新類", ["ARM"])])
            with self.assertRaises(ValueError):
                api.groups_assign({"assign": {}})


if __name__ == "__main__":
    unittest.main()
