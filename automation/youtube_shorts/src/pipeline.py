"""1回分(market x slot)のYouTube Shorts自動生成パイプライン。

流れ:
  1. content_brain で台本・メタデータを生成(履歴と重複しないテーマを選ぶ)
  2. slides で7枚の縦型画像を生成
  3. tts でページごとのナレーションを合成
  4. video でKen Burns風の縦型MP4を組み立て
  5. 各種納品物(title/description/tags/music/sources_notes/upload_guide)を書き出す
  6. コンタクトシートを作る
  7. ZIPを作成し、CRC検証・内部ファイル読み出しテストを行う
  8. (upload=True かつ 認証情報がある場合)YouTube にアップロードする
  9. dedupe履歴に記録する
  10. 実行レポートを返す
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import content_brain, dedupe, slides, tts, video, youtube_upload
from .config import OUTPUT_DIR, get_slot


@dataclass
class RunReport:
    market: str
    slot_id: str
    seq: int
    output_dir: str
    title: str
    video_path: str
    zip_path: str
    zip_verified: bool
    duration_sec: float
    is_placeholder: bool
    warnings: list[str] = field(default_factory=list)
    upload: dict | None = None
    sources: list[dict] = field(default_factory=list)


def _run_dir(market: str, slot_id: str, seq: int) -> Path:
    date_str = datetime.now().strftime("%Y%m%d")
    name = f"{seq:04d}_{market}_{slot_id}_{date_str}"
    d = OUTPUT_DIR / market / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _build_music_note(slot) -> str:
    return (
        "# 音楽メモ\n"
        "著作権上の理由により、本パイプラインでは特定楽曲の音源を自動で埋め込んでいません。\n"
        "以下を参考に、YouTube Studioのオーディオライブラリ等で選定してください。\n\n"
        f"- 雰囲気: 静かで不穏、テーマ({slot.label})に合わせて選曲する\n"
        "- 推奨音量目安: 6〜8%(ナレーションを阻害しない音量)\n"
        "- 検索キーワード例: dark ambient piano / suspense soft drone / quiet tension\n"
        "- 代替: 見つからない場合はYouTubeオーディオライブラリの『Mood: Dark』『Mood: Sad』系から選ぶ\n"
        "- 楽曲の提供状況・利用条件は地域・時期で変わるため、投稿前に必ず確認すること\n"
        "- 毎回同じ曲に固定しないこと\n"
    )


def _build_upload_guide(slot, publish_at_iso: str | None, finance_disclaimer_needed: bool) -> str:
    lines = [
        "# アップロードガイド",
        f"チャンネル: {slot.channel_label}",
        f"投稿枠: {slot.label} ({slot.slot_id})",
        f"カテゴリID: {slot.default_category_id} (Education)",
        f"公開予定時刻(ISO8601): {publish_at_iso or '未設定(手動で設定してください)'}",
        "縦型Shorts(#shorts)として投稿すること。",
        "",
        "# 確認事項(投稿ごとに必須)",
        "- AI生成の背景画像を使用しています。YouTube Studioで「改変または合成コンテンツを含む」",
        "  の開示が必要かどうかを毎回確認し、必要であれば申告してください",
        "  (Data API経由での自動設定は未対応のため手動確認が必要です)。",
        "- 子ども向けではない(selfDeclaredMadeForKids=false)想定です。内容を確認してください。",
    ]
    if finance_disclaimer_needed:
        lines.append("- 金融関連の内容です。概要欄に「一般的な情報であり、特定の商品を推奨するものではない」旨の記載を確認してください。")
    return "\n".join(lines) + "\n"


def _make_contact_sheet(image_paths: list[Path], out_path: Path) -> None:
    from PIL import Image

    thumb_w, thumb_h = 270, 480
    cols = 4
    rows = (len(image_paths) + cols - 1) // cols
    sheet = Image.new("RGB", (thumb_w * cols, thumb_h * rows), (20, 20, 20))
    for i, p in enumerate(image_paths):
        img = Image.open(p).resize((thumb_w, thumb_h))
        x = (i % cols) * thumb_w
        y = (i // cols) * thumb_h
        sheet.paste(img, (x, y))
    sheet.save(out_path, quality=85)


def _zip_and_verify(run_dir: Path, zip_path: Path) -> bool:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(run_dir.rglob("*")):
            if "_work" in p.relative_to(run_dir).parts:
                continue
            if p.is_file() and p.suffix != ".zip":
                zf.write(p, p.relative_to(run_dir))

    with zipfile.ZipFile(zip_path) as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            return False
        names = zf.namelist()
        if not names:
            return False
        with zf.open(names[0]) as f:
            f.read(1)
    return True


def run(
    market: str,
    slot_id: str,
    upload: bool = False,
    dry_run_upload: bool = True,
    offline_tts: bool = False,
    privacy_status: str = "private",
    script_file: str | None = None,
) -> RunReport:
    slot = get_slot(market, slot_id)
    seq = dedupe.next_seq_number(market)
    run_dir = _run_dir(market, slot_id, seq)
    warnings: list[str] = []

    if script_file:
        script = content_brain.load_script_file(script_file)
        dup = dedupe.is_duplicate_theme(market, script.raw.get("theme", ""), script.raw.get("conclusion", ""))
        if dup is not None:
            warnings.append(f"theme resembles existing entry #{dup['seq']:04d} ({dup['theme']}); review before publishing")
    else:
        script = content_brain.generate(slot)
    data = script.raw
    warnings.extend(script.warnings)

    pages = [
        slides.PageContent(page=p["page"], heading=p["heading"], body=p["body"], image_motif=p["image_motif"])
        for p in data["pages"]
    ]
    seed_key = f"{market}:{slot_id}:{seq}"
    image_paths = slides.render_all_pages(pages, slot.language, seed_key, run_dir / "images")

    narration_clips = tts.synthesize_pages(
        data["pages"], slot.tts_voice, run_dir / "narration", offline=offline_tts
    )

    video_path = run_dir / "video.mp4"
    work_dir = run_dir / "_work"
    duration = video.build_short(image_paths, narration_clips, work_dir, video_path)
    shutil.rmtree(work_dir, ignore_errors=True)

    _write_text(run_dir / "title.txt", data["video_title"] + "\n")
    _write_text(run_dir / "description.txt", data["description"] + "\n")
    _write_text(run_dir / "tags.txt", "\n".join(data.get("tags", [])) + "\n")
    _write_text(run_dir / "hashtags.txt", " ".join(data.get("hashtags", [])) + "\n")
    _write_text(run_dir / "music.txt", _build_music_note(slot))

    sources = data.get("sources", [])
    sources_text = "\n".join(
        f"- {s.get('title','')} / {s.get('publisher','')} / {s.get('url','')} "
        f"/ 公開日:{s.get('published_date','不明')} / 確認日:{s.get('accessed_date','不明')} / {s.get('note','')}"
        for s in sources
    ) or "(出典なし。プレースホルダ台本、またはWeb検索無効時の生成のため要確認)"
    _write_text(run_dir / "sources_notes.txt", sources_text + "\n")

    publish_at_iso = compute_publish_at(slot)
    _write_text(
        run_dir / "upload_guide.txt",
        _build_upload_guide(slot, publish_at_iso, data.get("finance_disclaimer_needed", False)),
    )
    _write_text(run_dir / "metadata.json", json.dumps(data, ensure_ascii=False, indent=2))

    _make_contact_sheet(image_paths, run_dir / "contact_sheet.jpg")

    zip_path = run_dir.parent / f"{run_dir.name}.zip"
    zip_ok = _zip_and_verify(run_dir, zip_path)
    _write_text(
        run_dir / "zip_verification.txt",
        f"zip_path={zip_path}\ncrc_test_passed={zip_ok}\nchecked_at={datetime.utcnow().isoformat()}Z\n",
    )

    upload_info = None
    if upload:
        try:
            result = youtube_upload.upload_short(
                market=market,
                video_path=video_path,
                title=data["video_title"],
                description=data["description"],
                tags=data.get("tags", []),
                category_id=slot.default_category_id,
                publish_at_iso=publish_at_iso,
                privacy_status=privacy_status,
                dry_run=dry_run_upload,
            )
            upload_info = asdict(result)
        except youtube_upload.MissingCredentialsError as e:
            warnings.append(f"upload skipped: {e}")

    dedupe.record_run(
        market=market,
        slot_id=slot_id,
        theme=data.get("theme", ""),
        conclusion=data.get("conclusion", ""),
        title=data["video_title"],
        output_dir=str(run_dir),
        theme_category=data.get("theme_category", ""),
    )

    return RunReport(
        market=market,
        slot_id=slot_id,
        seq=seq,
        output_dir=str(run_dir),
        title=data["video_title"],
        video_path=str(video_path),
        zip_path=str(zip_path),
        zip_verified=zip_ok,
        duration_sec=duration,
        is_placeholder=script.is_placeholder,
        warnings=warnings,
        upload=upload_info,
        sources=sources,
    )


def compute_publish_at(slot) -> str:
    tz = ZoneInfo(slot.timezone)
    now_local = datetime.now(tz)
    hh, mm = map(int, slot.publish_time_local.split(":"))
    target = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now_local:
        target = target + timedelta(days=1)
    return target.astimezone(ZoneInfo("UTC")).isoformat().replace("+00:00", "Z")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, choices=["ja", "en", "ko", "zh"])
    parser.add_argument("--slot", required=True)
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--publish-now", action="store_true", help="dry-run uploadでも即時公開扱いのstatusにする(publishAtを付けない)")
    parser.add_argument("--no-dry-run-upload", dest="dry_run_upload", action="store_false")
    parser.add_argument("--offline-tts", action="store_true", help="TTSをネットワーク不要の無音プレースホルダにする(テスト用)")
    parser.add_argument(
        "--script-file",
        help="台本JSONを外部ファイルから読み込む(APIを使わず、人がレビュー・執筆した台本を通す場合)",
    )
    parser.set_defaults(dry_run_upload=True)
    args = parser.parse_args()

    report = run(
        market=args.market,
        slot_id=args.slot,
        upload=args.upload,
        dry_run_upload=args.dry_run_upload,
        offline_tts=args.offline_tts,
        script_file=args.script_file,
    )
    print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    if not report.zip_verified:
        sys.exit(1)


if __name__ == "__main__":
    main()
