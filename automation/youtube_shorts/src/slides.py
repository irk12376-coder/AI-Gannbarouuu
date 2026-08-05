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


@dataclass
class PageContent:
    page: int
    heading: str
    body: str
    image_motif: str


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size)


def _wrap_cjk(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in text.replace("\n", " \n "):
        if ch == "\n":
            lines.append(current)
            current = ""
            continue
        trial = current + ch
        if draw.textlength(trial, font=font) > max_width and current:
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

    title_font = _font(fonts_cfg["primary"], fonts_cfg["size_title_px"])
    body_font = _font(fonts_cfg["primary_regular"], fonts_cfg["size_body_px"])
    outline_w = fonts_cfg["outline_width_px"]
    line_spacing = fonts_cfg["line_spacing"]

    heading_lines = _wrap(draw, page.heading, title_font, max_text_width, language)
    body_lines = _wrap(draw, page.body, body_font, max_text_width, language)

    heading_line_h = int(fonts_cfg["size_title_px"] * line_spacing)
    body_line_h = int(fonts_cfg["size_body_px"] * line_spacing)
    gap_between = int(fonts_cfg["size_body_px"] * 0.8)

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
