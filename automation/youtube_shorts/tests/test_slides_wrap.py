import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from src import slides  # noqa: E402
from src.config import load_style  # noqa: E402


class WrapTests(unittest.TestCase):
    def setUp(self):
        style = load_style()["fonts"]
        img = Image.new("RGB", (10, 10))
        self.draw = ImageDraw.Draw(img)
        self.font = ImageFont.truetype(style["primary_regular"], 40)
        self.max_width = 800

    def _assert_all_lines_fit(self, lines):
        for line in lines:
            self.assertLessEqual(self.draw.textlength(line, font=self.font), self.max_width)

    def test_wrap_latin_normal_sentence(self):
        lines = slides._wrap_latin(self.draw, "This is a normal English sentence to wrap.", self.font, self.max_width)
        self._assert_all_lines_fit(lines)

    def test_wrap_latin_handles_non_spaced_cjk_leak(self):
        # 英語圏(language="en")で、スペースの無いCJKテキストが紛れ込んだ場合でも
        # キャンバス幅からはみ出してはいけない(過去に発生した回帰バグ)。
        text = "これはAPIキー未設定時のプレースホルダ本文です。テーマ: news_explained_simply"
        lines = slides._wrap_latin(self.draw, text, self.font, self.max_width)
        self._assert_all_lines_fit(lines)

    def test_wrap_cjk_basic(self):
        lines = slides._wrap_cjk(self.draw, "返信が三時間遅れると何が起きるのか、という話です。", self.font, self.max_width)
        self._assert_all_lines_fit(lines)


class PlaceholderScriptTests(unittest.TestCase):
    def test_placeholder_heading_has_no_page_fraction_markers(self):
        # デザイン仕様: 右上の1/7のようなページ番号を画面に出してはいけない。
        from src import content_brain
        from src.config import get_slot

        slot = get_slot("ja", "kokoro")
        data = content_brain._placeholder_script(slot, "psychology_relationship", 1)
        for page in data["pages"]:
            self.assertNotIn("/7", page["heading"])

    def test_placeholder_uses_language_appropriate_text(self):
        from src import content_brain
        from src.config import get_slot

        for market in ["ja", "en", "ko", "zh"]:
            slot = get_slot(market, get_slotdata(market))
            data = content_brain._placeholder_script(slot, "x", 1)
            self.assertIn(content_brain._PLACEHOLDER_TEXT[market]["heading"], data["pages"][0]["heading"])


def get_slotdata(market: str) -> str:
    return {"ja": "kokoro", "en": "daily", "ko": "daily", "zh": "daily"}[market]


if __name__ == "__main__":
    unittest.main()
