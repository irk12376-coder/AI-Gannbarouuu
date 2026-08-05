#!/usr/bin/env python3
"""市場(YouTubeチャンネル)ごとのOAuthリフレッシュトークンを1回だけ取得するための
ローカル専用スクリプト。GitHub Actions上では実行できない(ブラウザ認証が必要)。

事前準備:
  1. Google Cloud Console でプロジェクトを作成し、YouTube Data API v3 を有効化する。
  2. OAuth同意画面を設定し、OAuthクライアントID(種類: デスクトップアプリ)を作成する。
  3. ダウンロードした client_secret.json をこのスクリプトに渡す。
  4. 投稿したいYouTubeチャンネルのGoogleアカウントでブラウザ認証を行う。

使い方:
  python src/authorize_channel.py --market ja --client-secret /path/to/client_secret.json

成功すると client_id / client_secret / refresh_token が標準出力に表示されるので、
GitHub Actions の Secrets に以下の名前で登録する:
  YOUTUBE_JA_CLIENT_ID / YOUTUBE_JA_CLIENT_SECRET / YOUTUBE_JA_REFRESH_TOKEN
(marketを EN/KO/ZH に読み替えて、4チャンネル分繰り返す)
"""
from __future__ import annotations

import argparse
import json

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", required=True, choices=["ja", "en", "ko", "zh"])
    parser.add_argument("--client-secret", required=True, help="Google Cloud ConsoleでダウンロードしたOAuthクライアントのJSON")
    args = parser.parse_args()

    flow = InstalledAppFlow.from_client_secrets_file(args.client_secret, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(args.client_secret, encoding="utf-8") as f:
        client_info = json.load(f)["installed"]

    market_upper = args.market.upper()
    print("\n=== GitHub Actions Secrets に登録する値 ===")
    print(f"YOUTUBE_{market_upper}_CLIENT_ID={client_info['client_id']}")
    print(f"YOUTUBE_{market_upper}_CLIENT_SECRET={client_info['client_secret']}")
    print(f"YOUTUBE_{market_upper}_REFRESH_TOKEN={creds.refresh_token}")
    print("============================================\n")
    print("上記3つの値を該当チャンネルのSecretsとして登録してください。")


if __name__ == "__main__":
    main()
