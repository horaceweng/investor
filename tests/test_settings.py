"""使用者設定: 代號解析 (全形標點等)、觀察清單存取、股票池選擇、動能門檻。"""
import unittest

from investor.navellier import settings as S
from tests.helpers import temp_data


class ParseTickers(unittest.TestCase):
    def test_normalisation(self):
        self.assertEqual(S.split_tickers("nvda，tsm、brk.b; $aapl ＡＭＤ")[0], ["NVDA", "TSM", "BRK-B", "AAPL", "AMD"])

    def test_dedup_comments_and_bad_tokens(self):
        good, bad = S.split_tickers("nvda NVDA # 註解 tsm 這整行都是註解\ntsm\nbad! <script>")
        self.assertEqual(good, ["NVDA", "TSM"])
        self.assertEqual(bad, ["bad!", "<script>"])

    def test_parse_raises_listing_bad_tokens(self):
        with self.assertRaises(ValueError) as cm:
            S.parse_tickers("NVDA bad!")
        self.assertIn("bad!", str(cm.exception))

    def test_comment_only_is_empty(self):
        self.assertEqual(S.split_tickers("# NVDA"), ([], []))


class Watchlist(unittest.TestCase):
    def test_default_when_no_file(self):
        with temp_data():
            self.assertEqual(S.load_watchlist(), S.DEFAULT_WATCHLIST)

    def test_roundtrip_has_no_upper_limit(self):
        with temp_data():
            many = [f"Q{i}" for i in range(500)]
            S.save_watchlist(many)
            self.assertEqual(S.load_watchlist(), many)                    # 曾有 60 檔上限, 已取消

    def test_empty_rejected(self):
        with temp_data(), self.assertRaises(ValueError):
            S.save_watchlist([])

    def test_toggle(self):
        with temp_data():
            S.save_watchlist(["NVDA", "TSM"])
            self.assertEqual(S.toggle_watchlist("aapl"), (["NVDA", "TSM", "AAPL"], True))
            self.assertEqual(S.toggle_watchlist("TSM"), (["NVDA", "AAPL"], False))
            with self.assertRaises(ValueError):
                S.toggle_watchlist("!!")

    def test_cannot_remove_last(self):
        with temp_data():
            S.save_watchlist(["NVDA"])
            with self.assertRaises(ValueError):
                S.toggle_watchlist("NVDA")

    def test_corrupt_lines_do_not_break_loading(self):
        with temp_data():
            S.paths.USER.mkdir(parents=True)
            S.paths.WATCHLIST.write_text("NVDA\n!!bad!!\nTSM\n")
            self.assertEqual(S.load_watchlist(), ["NVDA", "TSM"])


class ModeAndCooling(unittest.TestCase):
    def test_mode(self):
        with temp_data():
            self.assertEqual(S.load_mode(), "sp500")                      # 預設
            S.save_mode("ndx")
            self.assertEqual(S.load_mode(), "ndx")
            with self.assertRaises(ValueError):
                S.save_mode("../etc")
            S.paths.POOL_MODE.write_text("garbage")
            self.assertEqual(S.load_mode(), "sp500")                      # 檔案壞掉時退回預設

    def test_mode_order_puts_watchlist_first(self):
        self.assertEqual(list(S.MODES), ["watchlist", "sp500", "ndx"])

    def test_cooling_validation(self):
        with temp_data():
            self.assertEqual(S.load_cooling(), S.DEFAULT_COOLING)
            S.save_cooling(0.7, 0.3)
            self.assertEqual(S.load_cooling(), {"warn": 0.7, "remove": 0.3})
            for bad in ((0.3, 0.7), (1.2, 0.5), (0.5, 0.0), (0.5, 0.5)):    # 剔除門檻必須小於警示門檻, 且都在 0~1 之間
                with self.assertRaises(ValueError):
                    S.save_cooling(*bad)


if __name__ == "__main__":
    unittest.main()
