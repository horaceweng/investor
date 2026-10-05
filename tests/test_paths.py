"""路徑與舊資料搬遷。"""
import unittest
from pathlib import Path

from investor import paths
from tests.helpers import temp_data


class Ensure(unittest.TestCase):
    def test_suffixless_file_is_not_turned_into_a_directory(self):
        """回歸: 曾用副檔名猜測檔案/資料夾, 沒有副檔名的檔案 (session_secret、SEC 快取檔名) 會被建成同名資料夾。"""
        with temp_data() as root:
            f = paths.ensure_parent(root / "x" / "y" / "session_secret")
            self.assertTrue(f.parent.is_dir())
            self.assertFalse(f.exists())                      # 檔案本身不被建立成資料夾
            f.write_text("ok")
            self.assertEqual(f.read_text(), "ok")

    def test_ensure_dir(self):
        with temp_data() as root:
            self.assertTrue(paths.ensure_dir(root / "a" / "b").is_dir())


class MigrateLegacy(unittest.TestCase):
    def _make_legacy(self, root: Path):
        (root / "data").mkdir()
        (root / "data" / "sec_user_agent.txt").write_text("ua")
        (root / "data_navellier").mkdir()
        (root / "data_navellier" / "watchlist.txt").write_text("NVDA\n")
        (root / "data_navellier" / "latest_report_sp500.json").write_text("{}")
        (root / "13f_cache").mkdir()
        (root / "13f_cache" / "https_x").write_text("cached")
        (root / "sp500_fundamentals.csv").write_text("a,b")

    def test_moves_everything_and_is_idempotent(self):
        with temp_data() as root:
            self._make_legacy(root)
            moved = paths.migrate_legacy()
            self.assertEqual(len(moved), 5)
            self.assertEqual(paths.WATCHLIST.read_text(), "NVDA\n")
            self.assertEqual((paths.CONFIG / "sec_user_agent.txt").read_text(), "ua")
            self.assertEqual((paths.SEC_CACHE / "https_x").read_text(), "cached")
            self.assertTrue((paths.SCREEN_CACHE / "sp500_fundamentals.csv").exists())
            self.assertTrue((paths.NAVELLIER / "latest_report_sp500.json").exists())
            self.assertFalse((root / "data_navellier").exists())              # 搬空後移除舊資料夾
            self.assertEqual(paths.migrate_legacy(), [])                      # 再跑一次什麼都不做

    def test_never_overwrites_existing_new_files(self):
        with temp_data() as root:
            self._make_legacy(root)
            paths.ensure_parent(paths.WATCHLIST).write_text("MINE\n")
            paths.migrate_legacy()
            self.assertEqual(paths.WATCHLIST.read_text(), "MINE\n")           # 新位置已有的不覆蓋


class MergeDirectories(unittest.TestCase):
    def test_existing_destination_directory_is_merged_not_nested(self):
        """回歸: 目的資料夾已存在時, shutil.move 會把整個來源資料夾搬進去變成巢狀 (data/cache/13f/13f_cache/), 程式讀不到。"""
        with temp_data() as root:
            (root / "13f_cache").mkdir()
            (root / "13f_cache" / "old_file").write_text("old")
            (root / "13f_cache" / "shared").write_text("legacy version")
            paths.SEC_CACHE.mkdir(parents=True)
            (paths.SEC_CACHE / "shared").write_text("newer version")
            paths.migrate_legacy()
            self.assertEqual((paths.SEC_CACHE / "old_file").read_text(), "old")             # 搬到正確位置
            self.assertEqual((paths.SEC_CACHE / "shared").read_text(), "newer version")      # 新位置已有的不覆蓋
            self.assertFalse((paths.SEC_CACHE / "13f_cache").exists())                       # 沒有巢狀資料夾
            # 因同名而沒搬的舊檔案: 不覆蓋、也不自動刪除, 留在原處供人工確認 (搬走的部分則已不在舊資料夾)
            self.assertEqual([f.name for f in (root / "13f_cache").iterdir()], ["shared"])


class MigrationIsolation(unittest.TestCase):
    def test_destinations_follow_patched_paths(self):
        """回歸: 對照表曾在 import 時固定成真實路徑, 測試(或任何改動路徑常數的情況)會把檔案搬進真實資料夾。"""
        with temp_data():
            for dest in paths._legacy_map().values():
                self.assertTrue(str(dest).startswith(str(paths.DATA)), dest)
                self.assertNotEqual(str(dest).split("/data/")[0], str(paths.PACKAGE.parent), dest)   # 不是真實專案路徑


class Layout(unittest.TestCase):
    def test_runtime_data_is_all_under_data(self):
        for p in (paths.CONFIG, paths.USER, paths.NAVELLIER, paths.CACHE, paths.EXPORTS, paths.STATE_FILE, paths.SERVER_LOG):
            self.assertTrue(str(p).startswith(str(paths.DATA)), p)

    def test_managers_csv_ships_with_the_package(self):
        self.assertTrue(paths.MANAGERS_CSV.exists())
        self.assertGreater(len(paths.MANAGERS_CSV.read_text().splitlines()), 50)


if __name__ == "__main__":
    unittest.main()
