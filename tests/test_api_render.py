"""JSON API 的各項操作與頁面渲染 (不開 socket, 不連網)。"""
import unittest
from unittest import mock

import pandas as pd

from investor.navellier import settings
from investor.web import api, render, steps
from investor.web.render import cells, page, tabs
from tests.helpers import temp_data


class Api(unittest.TestCase):
    def test_watchlist_set_validates(self):
        with temp_data():
            self.assertEqual(api.watchlist_set({"tickers": ["nvda", "tsm"]}), (200, {"tickers": ["NVDA", "TSM"]}))
            with self.assertRaises(ValueError) as cm:
                api.watchlist_set({"tickers": ["NVDA", "bad!", "<script>"]})
            self.assertIn("bad!", str(cm.exception))
            with self.assertRaises(ValueError):
                api.watchlist_set({"tickers": []})
            self.assertEqual(settings.load_watchlist(), ["NVDA", "TSM"])        # 失敗的請求不改動檔案

    def test_cooling_and_universe(self):
        with temp_data():
            self.assertEqual(api.cooling_set({"warn": 70, "remove": 30})[1], {"warn": 0.7, "remove": 0.3})
            for bad in ({"warn": 30, "remove": 70}, {"warn": "abc", "remove": 1}, {}):
                with self.assertRaises(ValueError):
                    api.cooling_set(bad)
            self.assertEqual(api.universe_set({"mode": "ndx"}), (200, {"mode": "ndx"}))
            with self.assertRaises(ValueError):
                api.universe_set({"mode": "dow30"})

    def test_update_rules(self):
        with temp_data(), mock.patch.object(api.jobs, "start_job", return_value=True) as start:
            self.assertEqual(api.update("nope")[0], 400)
            self.assertEqual(api.update("init")[0], 400)                          # 內部用, 不開放
            settings.save_mode("watchlist")
            self.assertEqual(api.update("losers")[0], 400)                        # 選股功能不適用自訂觀察清單
            self.assertEqual(api.update("nav")[0], 202)                           # Navellier 適用
            self.assertEqual(api.update("buys")[0], 202)                          # 大師買進與股票池無關
            settings.save_mode("sp500")
            self.assertEqual(api.update("losers")[0], 202)
            self.assertEqual(start.call_count, 3)

    def test_update_while_busy_is_409(self):
        with temp_data(), mock.patch.object(api.jobs, "start_job", return_value=False):
            self.assertEqual(api.update("nav"), (409, {"started": False}))

    def test_all_routes_have_size_limits(self):
        for path, (fn, limit) in api.JSON_POST.items():
            self.assertTrue(callable(fn) and 0 < limit <= 500_000, path)


class Cells(unittest.TestCase):
    def test_missing_and_zero(self):
        self.assertIn("—", cells.cell("num", None))
        self.assertIn("—", cells.cell("num", float("nan")))
        self.assertNotIn("-0.00", cells.cell("num", -0.0))                         # -0.0 要顯示成 0.00
        self.assertIn("0.00", cells.cell("num", 0))

    def test_html_is_escaped(self):
        out = cells.cell("text", "<script>alert(1)</script>")
        self.assertNotIn("<script>", out)

    def test_pct_colour_class(self):
        self.assertIn("up", cells.cell("pct", 5)); self.assertIn("dn", cells.cell("pct", -5))

    def test_table_adds_performance_columns_and_stars(self):
        df = pd.DataFrame({"代號": ["AAA"], "清單": ["★"]})
        html = cells.table(df, [("代號", "代號", "ticker"), ("清單", "清單", "star")], {"AAA": {"1M": 1.5, "3M": 2, "6M": 3, "1Y": 4}})
        for h in ("1M", "3M", "6M", "1Y"):
            self.assertIn(f"<th data-k=\"n\">{h}</th>", html)
        self.assertIn('class="star on"', html)
        self.assertIn("+1.5%", html)


class Page(unittest.TestCase):
    def _page(self, mode, state=None):
        with temp_data(), mock.patch.object(page, "load_mode", lambda: mode), mock.patch.object(tabs, "load_mode", lambda: mode):
            return render.build_page(state or {})

    def test_empty_state_renders_in_every_mode(self):
        for mode in ("watchlist", "sp500", "ndx"):
            html = self._page(mode)
            self.assertIn("<!doctype html>", html)
            self.assertEqual(html.count('<section id="s'), 7)
            self.assertIn("尚無資料", html)

    def test_tab_order_puts_superinvestors_last(self):
        html = self._page("sp500")
        order = [html.index(f'data-t="{i}"') for i in ("s1", "s2", "s3", "s4", "s6", "s7", "s5")]
        self.assertEqual(order, sorted(order))

    def test_screens_are_not_applicable_for_the_custom_watchlist(self):
        html = self._page("watchlist")
        self.assertEqual(html.count("此功能不適用"), 5)                            # 跌幅/本益比/淨值比/殖利率/神奇公式
        self.assertEqual(html.count('class="dim"'), 5)
        self.assertNotIn("此功能不適用", self._page("sp500"))

    def test_header_pool_buttons_order_and_selection(self):
        html = self._page("ndx")
        i1, i2, i3 = (html.index(f'data-mode="{m}"') for m in ("watchlist", "sp500", "ndx"))
        self.assertTrue(i1 < i2 < i3)
        self.assertRegex(html, r'data-mode="ndx" class="on"')

    def test_logout_link_only_when_login_enabled(self):
        with temp_data(), mock.patch.object(page, "load_mode", lambda: "sp500"), mock.patch.object(tabs, "load_mode", lambda: "sp500"):
            self.assertNotIn("/logout", render.build_page({}))
            self.assertIn("/logout", render.build_page({}, logout=True))

    def test_static_assets_are_real_files_and_embedded(self):
        self.assertIn("function fit()", page.JS)
        self.assertIn(":root{--bg", page.CSS)
        html = self._page("sp500")
        self.assertIn(page.JS, html); self.assertIn(page.CSS, html)

    def test_navellier_notes_are_after_the_tables(self):
        html = self._page("sp500")
        sec = html[html.index('<section id="s7"'):]
        self.assertGreater(sec.index("說明與警示"), sec.index('<div class="empty">'))


class Steps(unittest.TestCase):
    def test_every_task_references_known_steps(self):
        for task, (label, mods) in steps.TASKS.items():
            self.assertTrue(label)
            for m in mods:
                self.assertIn(m, steps.STEPS, task)

    def test_applicability(self):
        self.assertFalse(steps.applicable("losers", "watchlist"))
        self.assertTrue(steps.applicable("all", "watchlist"))                      # 「全部更新」仍有適用的部分
        self.assertTrue(steps.applicable("nav", "watchlist"))


if __name__ == "__main__":
    unittest.main()
