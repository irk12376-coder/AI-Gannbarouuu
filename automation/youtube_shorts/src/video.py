"""7枚の画像とナレーション音声から、Ken Burns風の縦型Shorts動画(MP4)を組み立てる。

方針:
  1. ページごとに ffmpeg zoompan でゆっくり拡大するクリップを作る
     (長さはナレーション尺 + 余白を style.yaml の上下限でクランプ)
  2. ページごとのナレーションmp3を、そのクリップ長に無音パディングして結合
  3. 全ページを xfade/acrossfade でクロスフェード連結する

背景音楽(BGM)は既定では追加しない(著作権・提供状況の確認が必要なため。
music.txt に記載する推奨曲は投稿時にYouTube Studioのオーディオライブラリ等で
別途付与する運用を想定)。ローカルに royalty-free なBGMファイルがあれば
`bgm_path` で薄く(-18dB目安)ミックスできる。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import load_style
from .tts import NarrationClip


@dataclass
class PageTiming:
    page: int
    image_path: Path
    narration: NarrationClip
    duration_sec: float


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def compute_timings(image_paths: list[Path], narration_clips: list[NarrationClip]) -> list[PageTiming]:
    style = load_style()["video"]
    lo, hi = style["seconds_per_page_min"], style["seconds_per_page_max"]
    pad = style["narration_padding_seconds"]
    timings = []
    for img_path, clip in zip(image_paths, narration_clips):
        duration = _clamp(clip.duration_sec + pad, lo, hi)
        timings.append(PageTiming(page=clip.page, image_path=img_path, narration=clip, duration_sec=duration))
    return timings


def _build_page_clip(timing: PageTiming, work_dir: Path, width: int, height: int, fps: int, zoom_per_sec: float) -> Path:
    out_path = work_dir / f"clip_{timing.page}.mp4"
    frames = max(int(timing.duration_sec * fps), 1)
    zoom_end = 1.0 + zoom_per_sec * timing.duration_sec

    vf = (
        f"scale={width * 2}:{height * 2},"
        f"zoompan=z='min(zoom+{zoom_per_sec / fps},{zoom_end})':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={width}x{height}:fps={fps},"
        f"format=yuv420p"
    )

    padded_audio = work_dir / f"audio_padded_{timing.page}.m4a"
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(timing.narration.path),
            "-af", f"apad=whole_dur={timing.duration_sec:.3f}",
            "-t", f"{timing.duration_sec:.3f}",
            "-c:a", "aac", "-b:a", "128k", str(padded_audio),
        ],
        check=True, capture_output=True,
    )

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-loop", "1", "-i", str(timing.image_path),
            "-i", str(padded_audio),
            "-vf", vf,
            "-t", f"{timing.duration_sec:.3f}",
            "-r", str(fps),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "128k",
            "-shortest",
            str(out_path),
        ],
        check=True, capture_output=True,
    )
    return out_path


def _crossfade_chain(clip_paths: list[Path], durations: list[float], crossfade: float, work_dir: Path, out_path: Path, fps: int) -> None:
    if len(clip_paths) == 1:
        subprocess.run(["ffmpeg", "-y", "-i", str(clip_paths[0]), "-c", "copy", str(out_path)], check=True, capture_output=True)
        return

    inputs = []
    for p in clip_paths:
        inputs += ["-i", str(p)]

    filter_parts = []
    v_label = "0:v"
    a_label = "0:a"
    cumulative = durations[0]
    for i in range(1, len(clip_paths)):
        offset = max(cumulative - crossfade, 0)
        next_v = f"v{i}"
        next_a = f"a{i}"
        filter_parts.append(
            f"[{v_label}][{i}:v]xfade=transition=fade:duration={crossfade:.3f}:offset={offset:.3f}[{next_v}]"
        )
        filter_parts.append(
            f"[{a_label}][{i}:a]acrossfade=d={crossfade:.3f}[{next_a}]"
        )
        v_label, a_label = next_v, next_a
        cumulative = cumulative - crossfade + durations[i]

    filter_complex = ";".join(filter_parts)
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{v_label}]", "-map", f"[{a_label}]",
        "-r", str(fps),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "128k",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def build_short(
    image_paths: list[Path],
    narration_clips: list[NarrationClip],
    work_dir: Path,
    out_path: Path,
    bgm_path: Path | None = None,
) -> float:
    style = load_style()["video"]
    canvas = load_style()["canvas"]
    width, height, fps = canvas["width"], canvas["height"], style["fps"]
    crossfade = style["crossfade_seconds"]

    work_dir.mkdir(parents=True, exist_ok=True)
    timings = compute_timings(image_paths, narration_clips)

    clip_paths = []
    for t in timings:
        clip_paths.append(_build_page_clip(t, work_dir, width, height, fps, style["ken_burns_zoom_per_second"]))

    durations = [t.duration_sec for t in timings]
    joined_path = work_dir / "joined.mp4"
    _crossfade_chain(clip_paths, durations, crossfade, work_dir, joined_path, fps)

    if bgm_path and bgm_path.exists():
        final_cmd = [
            "ffmpeg", "-y", "-i", str(joined_path), "-i", str(bgm_path),
            "-filter_complex",
            "[1:a]volume=0.08,aloop=loop=-1:size=2e9[bgm];[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            str(out_path),
        ]
        subprocess.run(final_cmd, check=True, capture_output=True)
    else:
        subprocess.run(["ffmpeg", "-y", "-i", str(joined_path), "-c", "copy", str(out_path)], check=True, capture_output=True)

    total_duration = sum(durations) - crossfade * (len(durations) - 1)
    return total_duration
