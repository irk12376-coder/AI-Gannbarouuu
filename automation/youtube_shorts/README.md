# YouTube Shorts 自動投稿パイプライン

TikTok/Douyinカルーセル運用の仕様(裏側ノートシリーズ)を踏まえ、
日本語・英語・韓国語・簡体字中国語の4市場向けにYouTube Shortsを
「台本生成 → 縦型画像7枚生成 → ナレーション音声合成 → 動画合成 →
YouTubeへのスケジュール投稿」まで自動化する仕組みです。

TikTok版は静止画カルーセル(MP4不要)でしたが、YouTube Shortsは動画が
必須のため、7枚の画像とナレーションからKen Burns風の縦型MP4を自動生成
する構成にしています。

## 構成

```
automation/youtube_shorts/
  config/
    channels.yaml   # 市場別チャンネル設定・投稿枠・テーマ配分・ハッシュタグ
    style.yaml       # 画像/動画デザイン設定(禁止事項を含む)
  src/
    config.py        # 設定読み込み
    dedupe.py         # 市場別テーマ履歴(重複防止台帳) data/history/*.json
    content_brain.py  # Claude APIで台本・メタデータ・出典を生成
    background.py     # 背景画像(既定は外部APIなしの手続き型ジェネレータ)
    slides.py          # 7枚の縦型JPGにテキストを合成
    tts.py              # ナレーション音声合成(既定: edge-tts)
    video.py            # ffmpegでKen Burns風の縦型MP4を組み立て
    youtube_upload.py   # YouTube Data API v3 アップロード
    authorize_channel.py # チャンネルごとのOAuthリフレッシュトークン取得(ローカル専用)
    pipeline.py          # 上記を1本のパイプラインとして実行するオーケストレータ
  data/history/*.json   # 市場別の既出テーマ台帳(パイプラインが自動更新・要コミット)
  output/                 # 実行結果(画像/音声/動画/納品物/ZIP) ※.gitignore対象
.github/workflows/
  youtube-shorts-daily.yml  # 市場・投稿枠ごとのcronスケジューラ
  _run-slot.yml              # 実処理を行う再利用ワークフロー
```

## 1回の実行で生成される納品物

`output/<market>/<seq>_<market>_<slot>_<date>/` 配下に:

- `images/page_1.jpg` 〜 `page_7.jpg` (1080x1920)
- `video.mp4` (縦型Shorts動画)
- `narration/narration_1.mp3` 〜 `_7.mp3`
- `title.txt` / `description.txt` / `tags.txt` / `hashtags.txt`
- `music.txt` (BGM選定メモ、音源そのものは含まない)
- `sources_notes.txt` (出典・公開日・確認日)
- `upload_guide.txt` (公開設定・AI生成コンテンツ申告の確認事項)
- `contact_sheet.jpg` (7枚のサムネイル一覧)
- `metadata.json` (台本の生データ)
- `zip_verification.txt` (ZIPのCRC検証結果)

同階層に `<同名>.zip` として1本にまとめたZIPも作成されます。

## セットアップ

### 1. Anthropic API Key(台本生成用)

`ANTHROPIC_API_KEY` を GitHub Secrets に登録してください。
未設定の場合、台本生成はプレースホルダ(`[PLACEHOLDER]`)にフォールバック
し、そのままでは投稿に使えない内容になります(パイプラインの動作確認は
可能)。

台本生成はClaudeのWeb検索ツールを使って一次情報を調べたうえで書く
指示になっていますが、完全に無人で走らせる性質上、事実誤認のリスクは
ゼロではありません。特に金融・歴史・医療に関わる主張は、`sources_notes.txt`
を人間が定期的に抜き打ちで確認することを推奨します。

### 2. YouTubeチャンネルごとのOAuth設定(4チャンネル分)

市場ごとに別チャンネル(別Googleアカウント)を想定しています。

1. [Google Cloud Console](https://console.cloud.google.com/) でプロジェクトを作成
2. 「YouTube Data API v3」を有効化
3. OAuth同意画面を設定(テストユーザーに投稿先アカウントを追加)
4. OAuthクライアントID(種類: デスクトップアプリ)を作成し、`client_secret.json` をダウンロード
5. ローカル環境(このリポジトリをクローンした自分のPC)で、投稿したいチャンネルの
   Googleアカウントでログインした状態で以下を実行:

   ```bash
   cd automation/youtube_shorts
   pip install -r requirements.txt
   python src/authorize_channel.py --market ja --client-secret /path/to/client_secret.json
   ```

   ブラウザが開いて認証を求められます。完了すると
   `YOUTUBE_JA_CLIENT_ID` / `YOUTUBE_JA_CLIENT_SECRET` / `YOUTUBE_JA_REFRESH_TOKEN`
   が表示されるので、GitHub Secretsに登録してください。
6. `en` / `ko` / `zh` についても同様に繰り返す(該当チャンネルのGoogleアカウントで認証)。

### 3. GitHub リポジトリ設定

- Settings → Actions → General → Workflow permissions で
  **「Read and write permissions」** を有効にする
  (重複防止台帳 `data/history/*.json` をワークフローがコミットするため)。
- 上記のSecretsを登録する:
  - `ANTHROPIC_API_KEY`
  - `YOUTUBE_JA_CLIENT_ID` / `YOUTUBE_JA_CLIENT_SECRET` / `YOUTUBE_JA_REFRESH_TOKEN`
  - `YOUTUBE_EN_CLIENT_ID` / `YOUTUBE_EN_CLIENT_SECRET` / `YOUTUBE_EN_REFRESH_TOKEN`
  - `YOUTUBE_KO_CLIENT_ID` / `YOUTUBE_KO_CLIENT_SECRET` / `YOUTUBE_KO_REFRESH_TOKEN`
  - `YOUTUBE_ZH_CLIENT_ID` / `YOUTUBE_ZH_CLIENT_SECRET` / `YOUTUBE_ZH_REFRESH_TOKEN`

未設定の市場のジョブは、アップロード時に認証情報不足の警告を出して
アップロードだけスキップします(生成・ZIP作成までは行われます)。

## スケジュール

`.github/workflows/youtube-shorts-daily.yml` が市場・投稿枠ごとにcronで
起動します(実際の公開時刻の2時間前に生成ジョブを実行し、YouTube側の
`publishAt` スケジュール公開で正確な時刻に公開する設計)。

| 市場 | 投稿枠 | 公開目標(現地時間) | 生成ジョブ起動(UTC) |
|---|---|---|---|
| ja | kokoro (心の裏側) | 09:00 JST | 22:00 (前日) |
| ja | sekai (世界の裏側) | 12:00 JST | 01:00 |
| ja | okane (お金の裏側) | 20:00 JST | 09:00 |
| en | daily | 18:00 America/New_York目安 | 21:00 (DST非対応、最大1時間ずれる場合あり) |
| ko | daily | 19:00 KST | 08:00 |
| zh | daily | 19:30 CST | 09:30 |

検証期間(最初の14日間)はこの構成(JA3本/日、他3市場は各1本/日、合計6本/日)
を推奨します。データが揃ったら海外版も `config/channels.yaml` の
`slots` を増やして最大3本/日まで拡張してください。

手動実行は Actions タブから `workflow_dispatch` で市場・投稿枠を指定して
起動できます(`dry_run_upload=true` にすると実際のアップロードをせずに
リクエスト内容だけ確認できます)。

## ローカルでのテスト

```bash
cd automation/youtube_shorts
pip install -r requirements.txt
# システムパッケージ: ffmpeg, fonts-noto-cjk(Noto Serif CJK)が必要

# ネットワーク不要のオフラインドライラン(無音ナレーション、アップロードなし)
python -m src.pipeline --market ja --slot kokoro --offline-tts

# 実際にTTS(edge-tts)を使うがアップロードはしない
python -m src.pipeline --market ja --slot kokoro

# アップロードも試す(dry-runでリクエスト内容だけ確認)
python -m src.pipeline --market ja --slot kokoro --upload
```

## 既知の制約・今後の改善余地

- **背景画像の品質**: 既定の `src/background.py` は外部AI画像APIに
  依存しない手続き型(グラデーション+粒状ノイズ+簡易シルエット)の
  ジェネレータです。TikTok版のような油絵・木炭画調の高品質背景が必要な
  場合は、`BackgroundEngine` プロトコルに沿った実装(契約済みの画像生成
  APIを叩くエンジンなど)を追加して差し替えてください。
- **BGM**: 著作権上の理由から、特定楽曲の音源は自動で埋め込んでいません。
  `music.txt` に選曲の方向性を書き出すので、YouTube Studioのオーディオ
  ライブラリ等で別途付与するか、`video.build_short(..., bgm_path=...)`
  にroyalty-freeな音源を渡してください。
- **AI生成コンテンツの開示**: YouTube Data API経由で「改変・合成コンテンツ」
  の開示フラグを確実に設定できるかは未確認のため自動化していません。
  `upload_guide.txt` に毎回、YouTube Studioでの手動確認を促す注意書きを
  出力しています。
- **英語圏のDST**: cronはUTC固定のため、米国の夏時間切り替えで公開時刻が
  最大1時間ずれます。気になる場合は季節ごとにcron式を手動調整してください。
- **3投稿まとめZIP(JA)**: 個々の投稿ZIPは自動生成・検証されますが、
  1日3本をまとめた統合ZIPの自動作成はGitHub Actionsの実行単位をまたぐ
  ため今回は未実装です。必要であれば、各ジョブの成果物(Artifacts)を
  日次でダウンロードして束ねる追加ワークフローを組んでください。
- **投稿枠の重複防止**: `data/history/<market>.json` をワークフローが
  自動コミットすることで、日をまたいだテーマ重複を防いでいます。手動で
  このファイルを編集・削除すると重複防止が効かなくなるので注意してください。
