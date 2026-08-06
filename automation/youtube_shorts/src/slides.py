"""背景画像の上にテキストを合成して、1080x1920の縦型JPGを作る。

絶対条件(デザイン仕様書より):
  - シリーズ名/エピソード番号/左上黒角丸ラベル/左上赤い線/右上ページ番号を描画しない
  - 画像上部に余白を残す(見出しは上部セーフマージンより下に配置)
  - 明朝体(Noto Serif CJK)、本文は白、見出し/強調語は暗い赤
  - 黒縁取り、スマホで瞬時に読めるサイズ、UI安全域に配置
  - 詰め込みすぎず、自然な位置で改行する
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import background
from .config import load_style

CJK_LANGS = {"ja", "ko", "zh"}

# 禁則処理: 行頭に置いてはいけない文字(句読点・閉じ括弧・長音・小書き仮名など)。
# 該当文字は前の行の末尾にぶら下げる。
LINE_START_PROHIBITED = (
    "。、．，・：；？！?!"
    "）］｝」』】〕〉》)]}"
    "’”"
    "ーゝゞヽヾ"
    "ぁぃぅぇぉっゃゅょゎ"
    "ァィゥェォッャュョヮ"
)
# 行末に置いてはいけない文字(開き括弧など)。該当文字は次の行へ送る。
LINE_END_PROHIBITED = "（［｛「『【〔〈《([{‘“"


@dataclass
class PageContent:
    page: int
    heading: str
    body: str
    image_motif: str


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _wrap_cjk(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """CJKテキストを禁則処理付きで折り返す。

    「。」「、」などが行頭に落ちたり、閉じ括弧だけが next line に取り残される
    のを防ぐ(日本語・中国語の組版として不自然に見えるため)。
    """
    lines: list[str] = []
    current = ""
    for ch in text.replace("\n", " \n "):
        if ch == "\n":
            lines.append(current)
            current = ""
            continue
        trial = current + ch
        if draw.textlength(trial, font=font) > max_width and current:
            if ch in LINE_START_PROHIBITED:
                # ぶら下げ: 行頭禁則文字は幅を多少超えても前の行に残す
                lines.append(trial)
                current = ""
                continue
            if current[-1] in LINE_END_PROHIBITED:
                # 追い出し: 行末禁則文字は次の行へ一緒に送る
                lines.append(current[:-1])
                current = current[-1] + ch
                continue
            lines.append(current)
            current = ch
        else:
            current = trial
    if current:
        lines.append(current)
    return [ln for ln in lines if ln.strip() != ""] or [""]


def _wrap_latin(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.split("\n"):
        words = paragraph.split(" ")
        current = ""
        for word in words:
            # 単語自体が幅を超える場合(スペースのないCJKテキストの混入などを含む)は
            # 文字単位でも分割し、キャンバス幅からはみ出さないようにする。
            if draw.textlength(word, font=font) > max_width:
                if current:
                    lines.append(current)
                    current = ""
                lines.extend(_wrap_cjk(draw, word, font, max_width))
                continue
            trial = (current + " " + word).strip()
            if draw.textlength(trial, font=font) > max_width and current:
                lines.append(current)
                current = word
            else:
                current = trial
        if current:
            lines.append(current)
    return lines or [""]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int, language: str) -> list[str]:
    if language in CJK_LANGS:
        return _wrap_cjk(draw, text, font, max_width)
    return _wrap_latin(draw, text, font, max_width)


def _layout_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: str,
    base_size: int,
    min_size: int,
    max_width: int,
    language: str,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    """台本が指定した改行位置(\\n)をできるだけ尊重してレイアウトする。

    指定行がそのままの文字サイズで収まらない場合、まず文字サイズを段階的に
    縮小して収めようとする。下限まで縮小しても収まらない場合にのみ、
    禁則処理付きの自動折り返しにフォールバックする。

    これは「一枚に情報を詰めすぎず、自然な位置で改行する」という要件のうち、
    改行位置を機械が勝手に壊さないための処理(過去に見出しの助詞1文字だけが
    次行へ孤立する問題があった)。
    """
    explicit_lines = [ln for ln in text.split("\n") if ln.strip()] or [""]
    for size in range(base_size, min_size - 1, -4):
        font = _font(font_path, size)
        if all(draw.textlength(ln, font=font) <= max_width for ln in explicit_lines):
            return font, explicit_lines
    font = _font(font_path, min_size)
    return font, _wrap(draw, text, font, max_width, language)


def _draw_outlined_text(draw: ImageDraw.ImageDraw, xy, text, font, fill, outline, outline_width):
    x, y = xy
    for dx in range(-outline_width, outline_width + 1):
        for dy in range(-outline_width, outline_width + 1):
            if dx * dx + dy * dy <= outline_width * outline_width:
                draw.text((x + dx, y + dy), text, font=font, fill=outline)
    draw.text((x, y), text, font=font, fill=fill)


def render_page(page: PageContent, language: str, seed_key: str) -> Image.Image:
    style = load_style()
    canvas = style["canvas"]
    palette = style["palette"]
    fonts_cfg = style["fonts"]

    width, height = canvas["width"], canvas["height"]
    top_margin = canvas["top_safe_margin_px"]
    bottom_margin = canvas["bottom_safe_margin_px"]
    side_margin = canvas["side_margin_px"]
    max_text_width = width - 2 * side_margin

    img = background.generate(width, height, page.image_motif, seed_key=f"{seed_key}:{page.page}")
    draw = ImageDraw.Draw(img)

    outline_w = fonts_cfg["outline_width_px"]
    line_spacing = fonts_cfg["line_spacing"]

    title_font, heading_lines = _layout_text(
        draw, page.heading, fonts_cfg["primary"],
        fonts_cfg["size_title_px"], fonts_cfg["size_title_min_px"], max_text_width, language,
    )
    body_font, body_lines = _layout_text(
        draw, page.body, fonts_cfg["primary_regular"],
        fonts_cfg["size_body_px"], fonts_cfg["size_body_min_px"], max_text_width, language,
    )

    heading_line_h = int(title_font.size * line_spacing)
    body_line_h = int(body_font.size * line_spacing)
    gap_between = int(body_font.size * 0.8)

    block_height = len(heading_lines) * heading_line_h + gap_between + len(body_lines) * body_line_h
    available_height = height - top_margin - bottom_margin
    start_y = top_margin + max(0, (available_height - block_height) // 2)

    y = start_y
    for line in heading_lines:
        w = draw.textlength(line, font=title_font)
        x = (width - w) / 2
        _draw_outlined_text(draw, (x, y), line, title_font, palette["text_emphasis"], palette["text_outline"], outline_w)
        y += heading_line_h

    y += gap_between
    for line in body_lines:
        w = draw.textlength(line, font=body_font)
        x = (width - w) / 2
        _draw_outlined_text(draw, (x, y), line, body_font, palette["text_primary"], palette["text_outline"], max(2, outline_w - 2))
        y += body_line_h

    return img


def render_all_pages(pages: list[PageContent], language: str, seed_key: str, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for page in pages:
        img = render_page(page, language, seed_key)
        path = out_dir / f"page_{page.page}.jpg"
        img.convert("RGB").save(path, quality=92)
        paths.append(path)
    return paths
