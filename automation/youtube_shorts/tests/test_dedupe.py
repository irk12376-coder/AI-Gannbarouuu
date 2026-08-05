import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class DedupeTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        import src.dedupe as dedupe_module

        self.dedupe = dedupe_module
        self.dedupe.HISTORY_DIR = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_next_seq_number_starts_at_1(self):
        self.assertEqual(self.dedupe.next_seq_number("ja"), 1)

    def test_record_run_increments_seq(self):
        self.dedupe.record_run(
            market="ja", slot_id="kokoro", theme="返信が遅い理由", conclusion="不安の仕組み",
            title="t1", output_dir="/tmp/x", theme_category="psychology_relationship",
        )
        self.assertEqual(self.dedupe.next_seq_number("ja"), 2)

    def test_is_duplicate_theme_detects_similar(self):
        self.dedupe.record_run(
            market="ja", slot_id="kokoro", theme="返信が3時間遅れると不安になる理由",
            conclusion="脳が警戒モードに入るから", title="t1", output_dir="/tmp/x",
            theme_category="psychology_relationship",
        )
        dup = self.dedupe.is_duplicate_theme(
            "ja", "返信が3時間くらい遅れると不安になる理由", "脳が警戒モードに入るから、というだけの話"
        )
        self.assertIsNotNone(dup)

    def test_is_duplicate_theme_allows_distinct_theme(self):
        self.dedupe.record_run(
            market="ja", slot_id="kokoro", theme="返信が遅い理由", conclusion="不安の仕組み",
            title="t1", output_dir="/tmp/x", theme_category="psychology_relationship",
        )
        dup = self.dedupe.is_duplicate_theme("ja", "600年読めない本の話", "暗号解読の限界")
        self.assertIsNone(dup)


if __name__ == "__main__":
    unittest.main()
