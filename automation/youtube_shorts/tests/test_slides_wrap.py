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
        for line in lines[:-1]:
            self.assertLessEqual(self.draw.textlength(line, font=self.font), self.max_width * 1.15)

    def test_kinsoku_no_prohibited_char_starts_a_line(self):
        # 禁則処理: 「。」「、」などが行頭に落ちてはいけない。
        text = (
            "夜、布団の中で急に戻ってくる。あの作業、開いたままだった。"
            "頭が求めているのは完了ではなく、前に進む合図なのかもしれない。"
        )
        for width in [300, 420, 560, 800]:
            lines = slides._wrap_cjk(self.draw, text, self.font, width)
            for line in lines:
                self.assertNotIn(
                    line[0], slides.LINE_START_PROHIBITED,
                    f"line starts with prohibited char {line[0]!r} at width={width}: {lines}",
                )

    def test_kinsoku_no_prohibited_char_ends_a_line(self):
        # 行末禁則: 開き括弧が行末に取り残されてはいけない。
        text = "研究では「いつ・どこで・どうやるか」を決めるだけで割り込む思考が減った"
        for width in [300, 420, 560]:
            lines = slides._wrap_cjk(self.draw, text, self.font, width)
            for line in lines:
                self.assertNotIn(
                    line[-1], slides.LINE_END_PROHIBITED,
                    f"line ends with prohibited char {line[-1]!r} at width={width}: {lines}",
                )

    def test_layout_text_preserves_author_line_breaks_by_shrinking(self):
        # 台本が指定した改行位置を、自動再折り返しで壊さないこと
        # (助詞1文字が次行に孤立する問題の回帰テスト)。
        style = load_style()["fonts"]
        text = "ただし、有名な部分は\n残らなかった"
        font, lines = slides._layout_text(
            self.draw, text, style["primary"],
            style["size_title_px"], style["size_title_min_px"], 888, "ja",
        )
        self.assertEqual(lines, ["ただし、有名な部分は", "残らなかった"])


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
