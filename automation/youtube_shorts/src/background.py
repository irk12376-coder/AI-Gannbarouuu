"""ページ背景の生成。

既定の実装(`engine="procedural"`)は外部AI画像APIに依存しない、PIL製の
暗色・低彩度・粒状テクスチャ背景ジェネレータです。TikTok版で使っている
ような高品質なAI生成背景(油絵・木炭画調)と比べると簡易ですが、
- 追加の画像生成APIキーが無くても動く
- 「AI画像内に文字/ロゴ/数字を生成させない」制約を構造的に満たす
  (そもそも生成AIに文字を描かせていない)
という利点があります。

より高品質な背景が必要になった場合は、`Background Engine`の
プロトコルに従う実装を追加し、`ENGINE` を差し替えてください
(例: 社内で契約済みの画像生成APIを叩く `AIImageEngine` を追加する)。
"""
from __future__ import annotations

import hashlib
import math
import random
from typing import Protocol

from PIL import Image, ImageDraw, ImageFilter

from .config import load_style


class BackgroundEngine(Protocol):
    def generate(self, width: int, height: int, motif: str, seed: int) -> Image.Image: ...


def _seed_from(*parts: str) -> int:
    h = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def _base_gradient(width: int, height: int, rng: random.Random) -> Image.Image:
    style = load_style()["palette"]
    dark = tuple(style["background_dark"])
    mid = tuple(style["background_mid"])
    angle = rng.uniform(0, math.pi)
    img = Image.new("RGB", (width, height))
    px = img.load()
    dx, dy = math.cos(angle), math.sin(angle)
    for y in range(height):
        for x in range(0, width, 4):
            t = ((x * dx + y * dy) / (width * abs(dx) + height * abs(dy) + 1e-6) + 1) / 2
            t = min(max(t + rng.uniform(-0.03, 0.03), 0), 1)
            color = tuple(_lerp(dark[i], mid[i], t) for i in range(3))
            for xx in range(x, min(x + 4, width)):
                px[xx, y] = color
    return img


def _add_grain(img: Image.Image, rng: random.Random, intensity: int = 10) -> Image.Image:
    noise = Image.effect_noise(img.size, 24).convert("L")
    noise = noise.point(lambda p: int((p - 128) * (intensity / 24)))
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    return Image.blend(img, Image.blend(img, noise_rgb, 0.15), 0.5)


def _vignette(img: Image.Image, strength: float = 0.55) -> Image.Image:
    w, h = img.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    cx, cy = w / 2, h * 0.45
    max_r = math.hypot(w, h) * 0.75
    steps = 40
    for i in range(steps, 0, -1):
        r = max_r * i / steps
        v = int(255 * (1 - strength * (i / steps)))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=v)
    mask = mask.filter(ImageFilter.GaussianBlur(120))
    black = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(img, black, mask)


def _draw_motif_shapes(img: Image.Image, motif: str, rng: random.Random) -> Image.Image:
    w, h = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    motif_l = motif.lower()

    if "silhouette" in motif_l or "figure" in motif_l:
        fx = rng.uniform(0.3, 0.7) * w
        fy = h * rng.uniform(0.72, 0.85)
        scale = h * 0.22
        draw.ellipse([fx - scale * 0.22, fy - scale, fx + scale * 0.22, fy - scale * 0.55], fill=(0, 0, 0, 235))
        draw.polygon(
            [
                (fx - scale * 0.32, fy),
                (fx + scale * 0.32, fy),
                (fx + scale * 0.22, fy - scale * 0.55),
                (fx - scale * 0.22, fy - scale * 0.55),
            ],
            fill=(0, 0, 0, 235),
        )

    if "shelves" in motif_l or "library" in motif_l or "book" in motif_l:
        for i in range(6):
            y = h * (0.55 + i * 0.045)
            draw.line([(0, y), (w, y)], fill=(0, 0, 0, 90), width=3)

    if "streetlight" in motif_l or "fog" in motif_l or "candlelight" in motif_l:
        gx, gy = w * rng.uniform(0.25, 0.75), h * rng.uniform(0.2, 0.4)
        glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gdraw = ImageDraw.Draw(glow)
        for r, a in [(260, 40), (180, 60), (90, 90)]:
            gdraw.ellipse([gx - r, gy - r, gx + r, gy + r], fill=(255, 235, 200, a))
        glow = glow.filter(ImageFilter.GaussianBlur(80))
        img.paste(Image.alpha_composite(img.convert("RGBA"), glow).convert("RGB"), (0, 0))

    if "torn" in motif_l or "paper" in motif_l or "ink" in motif_l:
        for _ in range(rng.randint(3, 6)):
            x0, y0 = rng.uniform(0, w), rng.uniform(0, h)
            draw.ellipse([x0, y0, x0 + rng.uniform(20, 60), y0 + rng.uniform(20, 60)], fill=(0, 0, 0, 30))

    return img


def generate(width: int, height: int, motif: str, seed_key: str) -> Image.Image:
    seed = _seed_from(motif, seed_key)
    rng = random.Random(seed)
    img = _base_gradient(width, height, rng)
    img = _draw_motif_shapes(img, motif, rng)
    img = _vignette(img, strength=rng.uniform(0.45, 0.62))
    img = _add_grain(img, rng, intensity=rng.randint(10, 20))
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    return img
