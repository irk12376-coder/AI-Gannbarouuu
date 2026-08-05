"""YouTube Data API v3 を使った Shorts のアップロード。

認証は市場ごとの OAuth リフレッシュトークンを環境変数から読む
(市場ごとに別チャンネル = 別Googleアカウントを想定):

  YOUTUBE_{MARKET}_CLIENT_ID
  YOUTUBE_{MARKET}_CLIENT_SECRET
  YOUTUBE_{MARKET}_REFRESH_TOKEN

例: market="ja" -> YOUTUBE_JA_CLIENT_ID / YOUTUBE_JA_CLIENT_SECRET / YOUTUBE_JA_REFRESH_TOKEN

リフレッシュトークンの取得は `src/authorize_channel.py` を
ローカル環境で1回だけ対話的に実行して行う(CI上では実行できない)。

注意(重要・要運用確認):
  YouTubeは「改変または合成されたコンテンツ(AI生成物など)」の開示を
  クリエイターに求めている。この開示フラグをData API経由で確実に設定
  できるかは執筆時点で未確定のため、本モジュールでは自動設定していない。
  アップロード後、YouTube Studioで毎回「合成コンテンツを含む」の申告
  要否を確認すること(元運用のTikTok/Douyin向け仕様と同じ扱い)。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


class MissingCredentialsError(RuntimeError):
    pass


@dataclass
class UploadResult:
    video_id: str | None
    video_url: str | None
    dry_run: bool
    request_body: dict[str, Any]


def _env_creds(market: str) -> tuple[str, str, str]:
    prefix = f"YOUTUBE_{market.upper()}_"
    client_id = os.environ.get(prefix + "CLIENT_ID")
    client_secret = os.environ.get(prefix + "CLIENT_SECRET")
    refresh_token = os.environ.get(prefix + "REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        raise MissingCredentialsError(
            f"missing one of {prefix}CLIENT_ID / {prefix}CLIENT_SECRET / {prefix}REFRESH_TOKEN"
        )
    return client_id, client_secret, refresh_token


def _build_client(market: str):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    client_id, client_secret, refresh_token = _env_creds(market)
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds)


def build_request_body(
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
    privacy_status: str,
    publish_at_iso: str | None,
    made_for_kids: bool = False,
) -> dict[str, Any]:
    status: dict[str, Any] = {
        "privacyStatus": privacy_status,
        "selfDeclaredMadeForKids": made_for_kids,
    }
    if publish_at_iso:
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at_iso

    return {
        "snippet": {
            "title": title[:100],
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": status,
    }


def upload_short(
    market: str,
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
    publish_at_iso: str | None,
    privacy_status: str = "private",
    made_for_kids: bool = False,
    dry_run: bool = False,
) -> UploadResult:
    body = build_request_body(title, description, tags, category_id, privacy_status, publish_at_iso, made_for_kids)

    if dry_run:
        return UploadResult(video_id=None, video_url=None, dry_run=True, request_body=body)

    from googleapiclient.http import MediaFileUpload

    youtube = _build_client(market)
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()

    video_id = response["id"]
    return UploadResult(
        video_id=video_id,
        video_url=f"https://youtube.com/shorts/{video_id}",
        dry_run=False,
        request_body=body,
    )
