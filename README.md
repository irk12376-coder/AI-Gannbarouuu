# AI-Gannbarouuu

社内AI活用を推進するためのプロジェクトです。

## 概要

このリポジトリでは、社内の業務効率化・生産性向上を目的とした
AI活用の取り組み(ツール、ノウハウ、ドキュメントなど)を管理します。

## 目的

- 社内業務におけるAI活用事例の共有
- AIツール導入・運用ノウハウの蓄積
- 社内向けAI活用の推進

---

# paystock — 保有株の売り時判定・銘柄リサーチツール

PayPay 証券で保有している銘柄について、「今売るべきか / 持ち続けるべきか」を
テクニカル指標にもとづいて機械的に判定し、あわせて新規の買い候補を
スクリーニングするコマンドラインツールです。

**発注は利用者がアプリで手動で行う前提**です。本ツールは
「どの銘柄を、いくら分、なぜ」までを出すところまでを担当します。

> **免責**: 本ツールの出力は公開されている価格データに機械的なルールを
> 当てはめた結果であり、投資助言ではありません。売買の判断と結果は
> すべて利用者ご自身の責任となります。

## できること

| コマンド | 内容 |
| --- | --- |
| `paystock alert` | **今日やることだけ**を短く表示。無ければ 1 行で終わる |
| `paystock report` | 保有銘柄ごとの売り時判定 + ポートフォリオ全体の偏り診断 |
| `paystock screen` | 上昇トレンド継続かつモメンタム上位の銘柄を抽出 |
| `paystock quote 5401` | 1 銘柄の指標と判定根拠を表示 |
| `paystock backtest 7011` | 判定ルールが過去にどう機能したかを検証 |
| `paystock init` | `portfolio.yaml` の雛形を作成 |

自動発注用の `paystock trade` / `paystock paper` も残っていますが、
手動で売買するなら使う必要はありません
(既定で無効なので、放置しても何も起きません)。

## セットアップ

```bash
pip install -r requirements.txt
python -m paystock report          # または pip install -e . して paystock report
```

Python 3.11 以上が必要です。

## 使い方

### 1. 保有資産を書き写す

`portfolio.yaml` に PayPay 証券アプリの「保有資産」画面の数字を入れます。
**評価額と評価損益と株数** の 3 つがあれば取得単価は自動で逆算されるので、
アプリに表示されていない取得単価を自分で計算する必要はありません。

```yaml
holdings:
  - name: 日本製鉄
    symbol: "5401"
    kind: jp_stock
    sector: 鉄鋼
    shares: 84.2091080572
    market_value: 57981     # 評価額
    pnl: 7960               # 評価損益
```

`kind` は `jp_stock` / `us_stock` / `fund` / `private` / `cash` のいずれか。
投資信託は基準価額を直接取得できないため、`proxy_symbol` に連動指数
(例: NASDAQ100 なら `^NDX`) を書いて代理評価します。

`next_earnings: 2026-11-04` を書いておくと、決算発表の 7 日前から警告が出ます。
手動で売買するなら決算跨ぎの判断が一番重要なので、埋めておくことを勧めます。

### 2. 今日やることを確認する

```bash
python -m paystock alert          # 対応が必要な銘柄だけ。無ければ 1 行
python -m paystock alert -q --notify  # cron 用。何も無い日は無言、あれば通知
```

PayPay 証券は金額指定売買なので、**「◯◯円のうち ◯◯円分を利確」**のように
そのままアプリに入力できる金額で出ます。金額は常に切り捨てで丸めるため、
保有額を超える注文にはなりません。

通知先は環境変数で設定します (Slack / Discord の Webhook、または SMTP メール)。
詳細は [docs/運用ガイド.md](docs/運用ガイド.md)。

### 3. 詳しく見る

```bash
python -m paystock report                       # ターミナルに表示
python -m paystock report -f html -o report.html # スマホで見る用の HTML
```

各銘柄について次を出力します。

- **判定**: 買い増し検討 / 保有継続 / 一部利確 / 売却検討 / 損切り検討
- **売りスコアとその内訳**: どのルールが何点を出したかを全部表示します
- **推奨損切りライン**: 52週高値 − ATR×2.5 (シャンデリア・エグジット)
- **ポートフォリオ診断**: セクター集中・銘柄集中・平均相関・現金比率

判定の閾値は `portfolio.yaml` の `rules:` で変更できます。

### 4. 買い候補を探す

```bash
python -m paystock screen --exclude-held -n 10
```

- 200日線より上、かつ 25日線 > 75日線 の銘柄だけを対象にします
  (下降トレンドの押し目買いが一番損をしやすいため)
- その中で「直近1ヶ月を除く12ヶ月騰落率」が強い順に並べます
- RSI が高すぎる銘柄や 25日線から離れすぎた銘柄は減点・除外します

対象銘柄は `data/universe_jp.csv` (東証の主要 150 銘柄程度)。
`--universe your.csv` で自分のリストに差し替えられます。

> PayPay 証券の日本株は取扱銘柄が限られています。抽出された銘柄が
> 実際に買えるかどうかはアプリ側で確認してください。

### 5. ルールを検証する

```bash
python -m paystock backtest 5401 7011 8035 --days 1200
```

終値で判定して翌営業日の始値で約定する前提で、勝率・最大ドローダウン・
単純保有した場合との比較を出します。

## 自動売買について

**PayPay 証券には一般利用者向けの公開注文 API がありません。**
アプリの自動操作は利用規約に抵触する可能性が高く、画面変更で簡単に壊れ、
誤発注時に取り返しがつかないため、本ツールでは実装していません。
PayPay 証券の口座については「分析とアラート」までを担当し、発注は手動で行う設計です。

自動売買を実際に動かす場合は次の 2 つを用意しています。

- `broker: paper` — ペーパートレード。資金は動かず、判断と損益だけを記録します
- `broker: kabus` — auカブコム証券 kabu ステーション API 経由の実発注

詳細と安全装置の一覧は [docs/自動売買について.md](docs/自動売買について.md) を参照してください。
既定は `enabled: false` + `dry_run: true` なので、設定を書き換えない限り
何も発注されません。

## ドキュメント

- [運用ガイド](docs/運用ガイド.md) — 毎日の使い方、判定の読み方、cron 設定
- [ポートフォリオ所見](docs/ポートフォリオ所見.md) — 現在の保有構成についての分析
- [銘柄メモ 2026-08](docs/銘柄メモ_2026-08.md) — 各保有銘柄の決算・材料
  (ツールが見ていない情報)
- [自動売買について](docs/自動売買について.md) — なぜ PayPay 証券では自動化しないのか

## 開発

```bash
pip install pytest
python -m pytest            # 139 件。ネットワークには一切アクセスしません
python -m paystock --offline report   # ダミー価格で動作確認 (投資判断には使用不可)
```

## ディレクトリ構成

```
paystock/
  models.py           共通データモデル
  config.py           portfolio.yaml の読み込み
  cli.py              コマンドラインインタフェース
  notify.py           通知の送信 (Slack/Discord Webhook, SMTP メール)
  data/               価格データ取得 (Yahoo → Stooq フォールバック + キャッシュ)
  analysis/           指標計算 (indicators) と判定ルール (rules) とポートフォリオ診断
  screener/           銘柄スクリーニング
  report/             操作リスト (actions) / アラート (alert) / テキスト / HTML
  trade/              ブローカー実装と自動売買エンジン・安全装置
  backtest/           日足バックテスト
data/universe_jp.csv  スクリーニング対象の銘柄リスト
portfolio.yaml        保有資産と設定
```

## ライセンス

社内利用を前提としています。

---

# claude_delegate — Codex から Claude Code へ実装を委託するローカルランナー

Codex が作った実装指示 (タスクファイル) を Claude Code CLI へ渡し、
このリポジトリの実装とテストを Claude Code にやらせて、結果を Codex が
確認できるようにするための、**ローカル環境限定の MVP** です。

外部サーバー・GitHub・API キーは一切使いません。
`claude auth login` 済みの、ローカルの Claude Code CLI をそのまま利用します。

## 前提

- Python 3.11 以上
- `claude` コマンドがインストール済みで、`claude auth login` でログイン済みであること
  (このツール自身は認証情報を保持しません)
- 対象プロジェクトが Git 管理されていること (変更確認に `git status` / `git diff` を使うため)

## 使い方

### 1. 環境確認

```bash
python -m claude_delegate.cli check
```

- Python のバージョン
- `claude` コマンドの有無、`claude --version`
- `claude auth status` でのログイン状態 (トークンなどの値は絶対に表示しません)
- 対象プロジェクトの絶対パス
- Git の有無と現在の未コミット変更

### 2. dry-run (Claude Code は起動しません)

```bash
python -m claude_delegate.cli run --task tasks/content_mvp.md --project-dir . --dry-run
```

- 作業フォルダ、読み込むタスクファイル、Claude Code へ渡す指示全文
- 許可予定のツール・コマンド、拒否するツール・コマンド
- 実際に叩く起動コマンド (プロンプト本文は文字数のみ表示)
- タスク文中に危険な指示 (`git push`、`rm -rf`、外部投稿、API キーの要求など) が
  見つかった場合はここで停止します

### 3. 実行 (`--approve` が無いと Claude Code は起動しません)

```bash
python -m claude_delegate.cli run --task tasks/content_mvp.md --project-dir . --approve
```

- `claude -p "<指示>"` を `subprocess` 経由 (`shell=True` は不使用) で起動します
- `cwd` は `--project-dir` を `resolve()` した絶対パスに固定されます
- タスク本文はコマンド文字列に連結せず、引数リストの 1 要素として渡します
- 実行時間の上限はデフォルト 900 秒 (`--timeout` で変更可)
- 終了後に `git status` / `git diff --stat` で変更ファイル一覧、
  Claude Code の報告からテスト結果らしい行を抽出して表示します
- 実行結果 (プロンプト・stdout・stderr・変更ファイル・テスト結果) は
  `logs/` に日時付き JSON で保存されます (環境変数や API キーはマスキングして記録)

## Claude Code に与える権限

- `Read` / `Glob` / `Grep` / `Edit` / `Write`
- `Bash` は、指定したテストコマンドと `git status` / `git diff` のみに限定
  (インストール済みの `claude` が `--allowedTools` / `--disallowedTools` に対応している場合)
- 対応していない古いバージョンでは、その分の絞り込みができない旨を `check` /
  `run --dry-run` の出力で警告します

## 禁止している操作

`git push` / `git commit` / `git reset --hard` / `git clean` /
`git checkout` や `git restore` による変更破棄 / ファイルの大量削除 /
プロジェクト外のファイル変更 / `curl` `wget` などの外部送信 /
YouTube・X・note への投稿 / OAuth・API キー・パスワードの要求・表示・保存 /
`--dangerously-skip-permissions` ・ `bypassPermissions` / `shell=True`。

これらは (1) タスクファイルの事前スキャン、(2) Claude Code の
`--disallowedTools`、(3) 委託タスクに自動で付ける共通指示、の三段構えで
防ぎます。許可されていない操作が必要になった場合、Claude Code は
自動承認せずに失敗として報告する運用を前提としています。

## 安全対策まとめ

- `--approve` を付けない限り Claude Code は絶対に起動しません
- 実行前に対象パス・タスクパスを `resolve()` し、プロジェクト外を拒否します
- タスクファイルにはサイズ上限があります (既定 64KB)
- 実行時間には上限があります (既定 900 秒、`--timeout` で変更可)
- ログに環境変数や認証情報の値は書き込みません。API キーらしい文字列は
  `mask_secrets()` で自動マスキングしてから記録します
- 同時実行防止のロックファイル (`.claude_delegate.lock`) をプロジェクト直下に作ります

## 実行中の注意 (Codex 側への周知)

**`--approve` での実行中は、Codex 自身 (このツールを呼び出している側) も
対象プロジェクト内のファイルを変更しないでください。**
Claude Code が同じ作業ツリーを編集しているため、同時編集は競合や
意図しない上書きの原因になります。ロックファイルはツール同士の多重起動は
防ぎますが、Codex が直接ファイルシステムを触るのは防げません。

## テスト

```bash
python -m pytest tests/test_delegate_runner.py
```

Claude Code 本体は一切起動せず、`subprocess.run` をすべてモックして
検証します (dry-run で呼ばれないこと、`--approve` 無しで起動しないこと、
危険なタスクを拒否すること、プロジェクト外パスを拒否すること、
タイムアウト処理、非 0 終了コードの扱い、ログでの認証情報マスキングなど)。

## 制限事項

- ローカル実行専用の MVP です。リモート実行、キュー、Web UI はありません
- Claude Code のバージョンによって使えるオプション (`--tools` /
  `--allowedTools` など) が異なります。無い場合はその分の制限が弱くなるため、
  `check` の警告を確認してください
- 危険な指示の検出は正規表現ベースの簡易的なものです。最終的な差分レビューは
  必ず人手 (または Codex) で行ってください
- 実際に `--approve` で Claude Code を起動する動作確認は、このツールの
  実装作業そのものでは行っていません (`--dry-run` までを確認済みです)
