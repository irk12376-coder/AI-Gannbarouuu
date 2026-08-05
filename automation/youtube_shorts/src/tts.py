"""ナレーション音声合成。

既定プロバイダは edge-tts (Microsoft Edge の音声合成サービスを無償利用する
非公式ライブラリ)。GitHub Actions ランナーなど通常のインターネット接続が
ある環境で動作する。APIキーは不要だが、提供状況は Microsoft 側の都合で
変わりうるため、失敗時は `offline` モード(無音プレースホルダ)にフォール
バックできるようにしてある。

将来、品質を上げたい場合は `TTSProvider` プロトコルに従う実装
(OpenAI TTS, Google Cloud TTS など)を追加して差し替える。
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class TTSProvider(Protocol):
    def synthesize(self, text: str, voice: str, out_path: Path) -> None: ...


@dataclass
class NarrationClip:
    page: int
    path: Path
    duration_sec: float


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(out.stdout)
    return float(data["format"]["duration"])


class EdgeTTSProvider:
    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        import edge_tts

        async def _run():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(out_path))

        asyncio.run(_run())


class SilentProvider:
    """ネットワーク不通時/オフラインテスト用: 文字数から概算した長さの無音mp3を作る。"""

    chars_per_second: float = 6.5

    def synthesize(self, text: str, voice: str, out_path: Path) -> None:
        duration = max(len(text) / self.chars_per_second, 1.5)
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
                "-t", f"{duration:.2f}", "-q:a", "9", str(out_path),
            ],
            check=True, capture_output=True,
        )


def synthesize_pages(
    pages: list[dict],
    voice: str,
    out_dir: Path,
    offline: bool = False,
) -> list[NarrationClip]:
    out_dir.mkdir(parents=True, exist_ok=True)
    provider: TTSProvider = SilentProvider() if offline else EdgeTTSProvider()
    clips = []
    for page in pages:
        text = page.get("narration") or page.get("body", "")
        out_path = out_dir / f"narration_{page['page']}.mp3"
        try:
            provider.synthesize(text, voice, out_path)
        except Exception:
            if offline:
                raise
            SilentProvider().synthesize(text, voice, out_path)
        duration = _ffprobe_duration(out_path)
        clips.append(NarrationClip(page=page["page"], path=out_path, duration_sec=duration))
    return clips
