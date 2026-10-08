"""全市場動能榜: 指標計算、標記、產業動能、價格快取、頁面與自動更新。全部用合成資料, 不連網。"""
import unittest
from datetime import date
from unittest import mock

import numpy as np
import pandas as pd

from investor import paths
from investor.screens import momentum
from tests.helpers import temp_data

WEEKS = pd.date_range("2024-10-04", periods=110, freq="W-FRI")


def _panel():
    """SPY 每週 +0.2%; LEAD 每週穩定 +1.5% (持續強勢); FLAT 跟 SPY 一樣; DOWN 每週 -1%; NOISY 大漲大跌。"""
    rng = np.random.default_rng(0)
    n = len(WEEKS)
    spy = pd.Series(100 * 1.002 ** np.arange(n), index=WEEKS)
    df = pd.DataFrame({
        "LEAD": 50 * 1.015 ** np.arange(n) * (1 + rng.normal(0, .002, n)),
        "FLAT": 80 * 1.002 ** np.arange(n),
        "DOWN": 90 * 0.99 ** np.arange(n),
        "NOISY": 40 * np.exp(np.cumsum(rng.normal(0, .08, n))),
    }, index=WEEKS)
    return df, spy


class Indicators(unittest.TestCase):
    def test_strong_trend_scores_high_and_flags(self):
        px, spy = _panel()
        ind = momentum.indicators(px, spy)
        self.assertGreater(ind.loc["LEAD", "A26"], ind.loc["FLAT", "A26"])
        self.assertLess(ind.loc["DOWN", "A52"], 0)
        self.assertTrue(ind.loc["LEAD", "ABOVE40"] and ind.loc["LEAD", "MA40UP"])
        self.assertFalse(ind.loc["DOWN", "ABOVE40"])
        self.assertGreater(ind.loc["LEAD", "UP26"], .8)
        self.assertAlmostEqual(ind.loc["LEAD", "R26"], px["LEAD"].iloc[-1] / px["LEAD"].iloc[-27] - 1)
        self.assertGreater(ind.loc["LEAD", "HI52"], -0.01)                # 持續上漲: 就在 52 週高附近
        self.assertGreater(ind.loc["NOISY", "VOL"], ind.loc["LEAD", "VOL"])

    def test_short_history_has_no_a52(self):
        px, spy = _panel()
        px.loc[px.index[:-30], "LEAD"] = np.nan                             # 只有 30 週資料
        ind = momentum.indicators(px, spy)
        self.assertTrue(np.isnan(ind.loc["LEAD", "A52"]))


def _scored_frame(n_per_ind=10):
    """兩個產業 (Hot / Cold) 各 n 檔; Hot 全部強勢, Cold 全部弱勢。"""
    rows, meta = {}, {}
    for i in range(n_per_ind):
        for ind, sign in (("Hot", 1), ("Cold", -1)):
            t = f"{ind[0]}{i}"
            v = sign * (1 + i / 10)
            rows[t] = {"A13": v, "A26": v, "A52": v, "UP26": .7 if sign > 0 else .3, "R4": .02 * sign, "R13": .1 * sign,
                       "R26": .3 * sign + i / 100, "R52": .5 * sign, "ABOVE10": sign > 0, "ABOVE40": sign > 0, "MA40UP": sign > 0,
                       "HI52": -.02 if sign > 0 else -.4, "NEWHI": sign > 0, "VOL": .5, "PRICE": 20.0, "NWEEKS": 104}
            meta[t] = {"name": t + " Inc", "industry": ind, "sector": "S", "cap": 5e9}
    return pd.DataFrame.from_dict(rows, orient="index"), pd.DataFrame.from_dict(meta, orient="index")


class Scoring(unittest.TestCase):
    def test_percentiles_flags_and_industry_momentum(self):
        ind, meta = _scored_frame()
        df = momentum.score(ind, meta)
        self.assertEqual(len(df), 20)
        self.assertTrue((df.loc[[f"H{i}" for i in range(10)], "IND_MOM"] == 1.0).all())
        self.assertTrue((df.loc[[f"C{i}" for i in range(10)], "IND_MOM"] == 0.5).all())
        self.assertTrue(df.loc["H9", "P"] and df.loc["H9", "TAIL"] and df.loc["H9", "BOTH"] and df.loc["H9", "CAND"])
        self.assertFalse(df.loc["C9", "CAND"])
        self.assertIn("持續強勢", momentum.tags(next(df.loc[["H9"]].itertuples())))

    def test_filters_small_cheap_and_short_history(self):
        ind, meta = _scored_frame()
        meta.loc["H0", "cap"] = 1e9
        ind.loc["H1", "PRICE"] = 3.0
        ind.loc["H2", "NWEEKS"] = 40
        df = momentum.score(ind, meta)
        for t in ("H0", "H1", "H2"):
            self.assertNotIn(t, df.index)

    def test_small_industry_has_no_momentum(self):
        ind, meta = _scored_frame(n_per_ind=5)                               # 每個產業只有 5 檔 (< 8)
        df = momentum.score(ind, meta)
        self.assertTrue(df["IND_MOM"].isna().all())
        self.assertFalse(df["TAIL"].any())
        self.assertTrue(momentum.industries(df).empty)

    def test_industry_table_and_rows(self):
        ind, meta = _scored_frame()
        df = momentum.score(ind, meta)
        t = momentum.industries(df)
        self.assertEqual(list(t["產業"]), ["Hot", "Cold"])
        self.assertEqual(int(t.set_index("產業").loc["Hot", "持續強勢檔數"]), int(df.loc[df.industry == "Hot", "P"].sum()))
        r = momentum.rows(df)
        self.assertTrue(set(r["產業"]) == {"Hot"})
        self.assertEqual(r.iloc[0]["代號"], "H9")                            # 同產業內 A26 最高者排前面


class Regime(unittest.TestCase):
    def test_above_and_below(self):
        up = pd.Series(np.linspace(100, 150, 60))
        down = pd.Series(np.linspace(150, 100, 60))
        self.assertTrue(momentum.regime(up)["above"])
        self.assertFalse(momentum.regime(down)["above"])


class PriceCache(unittest.TestCase):
    def _fake_download(self, calls):
        days = pd.bdate_range("2024-10-01", "2026-10-09")

        def dl(tickers, **kw):
            calls.append(list(tickers))
            data = {("Close", t): np.linspace(10, 20, len(days)) for t in tickers if t != "BAD"}
            return pd.DataFrame(data, index=days)
        return dl

    def test_cached_per_week_and_only_missing_fetched(self):
        calls = []
        with temp_data(), mock.patch.object(momentum.yf, "download", side_effect=self._fake_download(calls)), \
                mock.patch.object(momentum.time, "sleep"):
            px = momentum.weekly_prices(["AAA", "BBB", "BAD"], date(2026, 10, 2))
            self.assertLessEqual(px.index[-1], pd.Timestamp("2026-10-02"))   # 未收完的週不算
            self.assertIn("SPY", px.columns)
            self.assertTrue(px["BAD"].isna().all())                           # 抓不到的記成空欄, 不會每次重抓
            momentum.weekly_prices(["AAA", "BBB", "BAD"], date(2026, 10, 2))
            self.assertEqual(len(calls), 1)                                   # 同一週: 不重抓
            momentum.weekly_prices(["AAA", "CCC"], date(2026, 10, 2))
            self.assertEqual(calls[-1], ["CCC"])                              # 只抓新增的
            momentum.weekly_prices(["AAA"], date(2026, 10, 9))
            self.assertEqual(sorted(calls[-1]), ["AAA", "SPY"])               # 新的一週: 重抓

    def test_progress_kept_when_a_chunk_fails(self):
        calls = []
        good = self._fake_download(calls)

        def flaky(tickers, **kw):
            if len(calls) == 1:
                calls.append(list(tickers))
                return pd.DataFrame()
            return good(tickers, **kw)
        with temp_data(), mock.patch.object(momentum.yf, "download", side_effect=flaky), \
                mock.patch.object(momentum, "CHUNK", 2), mock.patch.object(momentum.time, "sleep"), \
                mock.patch.object(momentum, "retry", side_effect=lambda f: f()):
            with self.assertRaises(RuntimeError):
                momentum.weekly_prices(["A", "B", "C"], date(2026, 10, 2))   # 第二批失敗
            px = momentum.weekly_prices(["A", "B", "C"], date(2026, 10, 2))
            self.assertEqual(calls[-1], ["C", "SPY"])                          # 第一批 (A, B) 不重抓
            self.assertEqual(set(px.columns), {"A", "B", "C", "SPY"})


class Compute(unittest.TestCase):
    def test_end_to_end_with_fakes(self):
        px, spy = _panel()
        tick = list(px.columns)
        allpx = px.assign(SPY=spy)
        meta = pd.DataFrame({"name": [t + " Corp" for t in tick], "industry": ["X"] * 4, "sector": ["S"] * 4, "cap": [5e9] * 4}, index=tick)
        with temp_data(), mock.patch.object(momentum, "universe", return_value=meta), \
                mock.patch.object(momentum, "weekly_prices", return_value=allpx), \
                mock.patch.object(momentum.alpha_beta, "complete_week_cutoff", return_value=WEEKS[-1].date()):
            out = momentum.compute()
            self.assertEqual(out["n_universe"], 4)
            self.assertIn("LEAD", set(out["rows"]["代號"]))
            self.assertTrue(paths.MOMENTUM_HISTORY.exists())
            momentum.compute()                                                # 同一週再跑: 歷史覆蓋, 不重複
            self.assertEqual(len(paths.MOMENTUM_HISTORY.read_text().strip().splitlines()), 1)

    def test_refuses_when_prices_mostly_missing(self):
        px, spy = _panel()
        allpx = px.assign(SPY=spy)
        allpx[["LEAD", "FLAT", "DOWN"]] = np.nan
        meta = pd.DataFrame({"name": list(px.columns), "industry": "X", "sector": "S", "cap": 5e9}, index=px.columns)
        with temp_data(), mock.patch.object(momentum, "universe", return_value=meta), \
                mock.patch.object(momentum, "weekly_prices", return_value=allpx), \
                mock.patch.object(momentum.alpha_beta, "complete_week_cutoff", return_value=WEEKS[-1].date()):
            with self.assertRaises(RuntimeError):
                momentum.compute()


class Page(unittest.TestCase):
    def test_tab_renders_by_industry_with_regime_and_star(self):
        from investor.navellier import settings
        from investor.web.render import tabs
        ind, meta = _scored_frame()
        df = momentum.score(ind, meta)
        M = {"asof": "2026-10-02", "regime": {"spy": 1, "ma40": 1, "above": False, "dist": -.03},
             "industries": momentum.industries(df), "rows": momentum.rows(df), "n_universe": 20, "n_cand": 10, "n_p": 10, "ts": "x"}
        with temp_data():
            settings.save_watchlist(["H9"])
            t = tabs.momentum_tab({"momentum": M}, tabs.Ctx({}))
        self.assertIn("40 週線之下", t["extra"])
        self.assertIn('<h3 class="grp">Hot', t["table"])
        self.assertIn('class="star on" data-tk="H9"', t["table"])
        self.assertIn('data-tk="H8"', t["table"])
        self.assertIn('title="近 26 週 (約半年) 每週相對 SPY', t["table"])        # 表頭滑鼠提示
        self.assertIn('title="產業半年報酬中位數', t["extra"])

    def test_tab_empty_state(self):
        from investor.web.render import tabs
        with temp_data():
            t = tabs.momentum_tab({}, tabs.Ctx({}))
        self.assertEqual(t["table"], "")
        self.assertEqual(t["task"], "momentum")


class AutoRefresh(unittest.TestCase):
    def test_weekly_due(self):
        from investor.navellier import alpha_beta
        from investor.web import autorefresh
        today = date(2026, 10, 8)
        cut = str(alpha_beta.complete_week_cutoff(today))
        self.assertTrue(autorefresh.momentum_due({}, today))
        self.assertFalse(autorefresh.momentum_due({"momentum": {"asof": cut}}, today))
        self.assertTrue(autorefresh.momentum_due({"momentum": {"asof": "2026-09-25"}}, today))

    def test_started_only_when_ratings_are_fresh(self):
        from investor.web import autorefresh
        autorefresh._last_try.clear()
        with mock.patch.object(autorefresh.settings, "load_mode", return_value="ndx"), \
                mock.patch.object(autorefresh, "due", return_value=None), \
                mock.patch.object(autorefresh.jobs, "start_job", return_value=True) as start:
            self.assertEqual(autorefresh.maybe_start({}, now=1000), "momentum")
            start.assert_called_once_with("momentum")
        autorefresh._last_try.clear()
        with mock.patch.object(autorefresh.settings, "load_mode", return_value="ndx"), \
                mock.patch.object(autorefresh, "due", return_value="nav"), \
                mock.patch.object(autorefresh.jobs, "start_job", return_value=True) as start:
            self.assertEqual(autorefresh.maybe_start({}, now=1000), "nav")    # 評級優先


if __name__ == "__main__":
    unittest.main()
