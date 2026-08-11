"""コマンドラインインタフェース。

  paystock alert           対応が必要な銘柄だけを表示 (毎日の定期実行向け)
  paystock report          保有資産の売り時診断
  paystock screen          これから上がりそうな銘柄のスクリーニング
  paystock quote 5401      1 銘柄の指標を確認
  paystock backtest 7011   ルールの過去成績を検証
  paystock trade           自動売買 (既定はドライラン)
  paystock paper           ペーパートレード口座の状況表示・リセット
  paystock init            portfolio.yaml の雛形を作成
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from . import __version__, config as config_mod, notify as notify_mod
from .analysis import indicators as ind_mod
from .analysis import portfolio as portfolio_mod
from .analysis.rules import evaluate
from .backtest import engine as backtest_mod
from .data import DEFAULT_HISTORY_DAYS, PriceSourceError, build_source
from .models import AssetKind
from .report import actions as actions_mod
from .report import alert as alert_mod
from .report import html as html_report
from .report import text as text_report
from .screener import screen as screen_mod
from .screener import universe as universe_mod
from .trade.base import BrokerError
from .trade.engine import TradingEngine
from .trade.paper import PaperBroker

log = logging.getLogger("paystock")

TEMPLATE = """\
# PayPay 証券の保有資産。アプリの「保有資産」画面の数字をそのまま書き写す。
#   shares       : 保有株数 (小数可)
#   market_value : 評価額 (円)
#   pnl          : 評価損益 (円。マイナスは含み損)
# 取得単価はこの 3 つから自動計算されるので、別途入力する必要はない。

holdings:
  - name: 日本製鉄
    symbol: "5401"
    kind: jp_stock
    sector: 鉄鋼
    shares: 0
    market_value: 0
    pnl: 0

  - name: 現金
    kind: cash
    market_value: 0

# 判定に使う閾値 (省略時は既定値)
rules:
  stop_loss_pct: -12.0     # この率まで下がったら損切り検討
  atr_stop_multiple: 2.5   # トレーリングストップの ATR 倍率

# 自動売買の設定。既定では何も発注しない。
trade:
  enabled: false
  broker: paper
  dry_run: true
  max_order_amount: 30000
  max_position_pct: 20.0
  max_orders_per_day: 5
  max_daily_loss_pct: 5.0
"""


def _source(args: argparse.Namespace):
    return build_source(offline=args.offline, use_cache=not args.no_cache)


def _write_output(content: str, path: str | None) -> None:
    if not path:
        print(content)
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    print(f"レポートを書き出しました: {target}")


# --------------------------------------------------------------- サブコマンド


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.config)
    if path.exists() and not args.force:
        print(f"{path} は既に存在します。上書きするには --force を付けてください。")
        return 1
    path.write_text(TEMPLATE, encoding="utf-8")
    print(f"{path} を作成しました。保有資産の数字を書き込んでから `paystock report` を実行してください。")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    source = _source(args)
    review = portfolio_mod.review(cfg, source, offline=args.offline)

    if args.format == "html":
        _write_output(html_report.render_review(review), args.output)
    else:
        _write_output(text_report.render_review(review, detail=args.detail), args.output)
    return 0


def cmd_alert(args: argparse.Namespace) -> int:
    """対応が必要な銘柄だけを短く出す。cron + 通知で毎日回す用。"""
    cfg = config_mod.load(args.config)
    review = portfolio_mod.review(cfg, _source(args), offline=args.offline)
    items = actions_mod.build(
        review,
        cfg.manual,
        cash=cfg.cash,
        max_buy_amount=cfg.trade.max_order_amount,
    )

    if args.quiet and not alert_mod.has_todo(items):
        return 0  # 何も無い日は完全に無言 (cron がメールを飛ばさないように)

    body = alert_mod.render(items, include_hold=args.include_hold)
    if review.offline:
        body = "!!! オフラインモード: ダミー価格による出力です !!!\n\n" + body
    print(body)

    if args.notify:
        todo = sum(1 for i in items if i.actionable)
        subject = (
            f"[paystock] 要対応 {todo}件" if todo else "[paystock] 本日は対応なし"
        )
        sent = notify_mod.notify(subject, body)
        print(
            f"通知を送信しました: {', '.join(sent)}" if sent else "通知先が未設定です",
            file=sys.stderr,
        )
    return 0


def cmd_screen(args: argparse.Namespace) -> int:
    universe_path = args.universe
    exclude: set[str] = set()
    if not universe_path or args.exclude_held:
        try:
            cfg = config_mod.load(args.config)
            universe_path = universe_path or cfg.universe_path
            if args.exclude_held:
                exclude = {
                    h.symbol
                    for h in cfg.holdings
                    if h.symbol and h.kind is AssetKind.JP_STOCK
                }
        except config_mod.ConfigError:
            universe_path = universe_path or str(universe_mod.DEFAULT_UNIVERSE)

    entries = universe_mod.load(universe_path)
    if args.limit:
        entries = entries[: args.limit]

    cfg_screen = screen_mod.ScreenConfig(
        top_n=args.top,
        require_uptrend=not args.relaxed,
        workers=args.workers,
    )
    source = _source(args)
    print(f"{len(entries)} 銘柄を評価中… (データ元: {source.name})", file=sys.stderr)
    hits, errors = screen_mod.screen(entries, source, cfg_screen, exclude)

    if args.format == "html":
        _write_output(html_report.render_screen(hits, errors, args.offline), args.output)
    else:
        _write_output(text_report.render_screen(hits, errors, args.offline), args.output)
    return 0


def cmd_quote(args: argparse.Namespace) -> int:
    source = _source(args)
    for symbol in args.symbols:
        try:
            df = source.history(symbol, days=DEFAULT_HISTORY_DAYS)
            ind = ind_mod.compute(df)
        except (PriceSourceError, ValueError) as exc:
            print(f"{symbol}: 取得できませんでした — {exc}")
            continue

        action, score, reasons, stop, target = evaluate(ind, None, None)

        def num(value: float | None, digits: int = 1) -> str:
            return "－" if value is None else f"{value:,.{digits}f}"

        print(f"■ {symbol}  現在値 {ind.price:,.1f}  判定 {action.value} (スコア {score:+.0f})")
        print(
            f"   25日線 {num(ind.sma25)} / 75日線 {num(ind.sma75)} / "
            f"RSI {num(ind.rsi14)}"
        )
        if stop is not None:
            if ind.price < stop:
                print(f"   トレーリングストップ {num(stop)} — 既に下回っています")
            else:
                print(f"   損切り目安 {num(stop)} / 利確目安 {num(target)}")
        for r in sorted(reasons, key=lambda r: -abs(r.score)):
            print(f"   [{'売' if r.score > 0 else '買'}{abs(r.score):>4.0f}] {r.label}: {r.detail}")
        print()
    print(text_report.DISCLAIMER)
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    try:
        cfg = config_mod.load(args.config)
        rules = cfg.rules
    except config_mod.ConfigError:
        rules = None

    source = _source(args)
    for symbol in args.symbols:
        try:
            df = source.history(symbol, days=args.days)
            result = backtest_mod.run(
                symbol,
                df,
                rules=rules,
                initial_cash=args.cash,
                fee_pct=args.fee,
                slippage_pct=args.slippage,
            )
        except (PriceSourceError, ValueError) as exc:
            print(f"{symbol}: バックテストできませんでした — {exc}")
            continue
        print(result.summary())
        print()
    print(text_report.DISCLAIMER)
    return 0


def cmd_trade(args: argparse.Namespace) -> int:
    cfg = config_mod.load(args.config)
    settings = cfg.trade
    if args.dry_run:
        settings.dry_run = True
    if args.live:
        settings.dry_run = False

    source = _source(args)

    if settings.broker == "paper":
        broker = PaperBroker(source, initial_cash=args.cash)
    elif settings.broker == "kabus":
        import os

        from .trade.kabus import PROD_BASE, TEST_BASE, KabusBroker

        broker = KabusBroker(
            api_password=os.environ.get("KABU_API_PASSWORD", ""),
            order_password=os.environ.get("KABU_ORDER_PASSWORD", ""),
            base_url=PROD_BASE if args.prod else TEST_BASE,
        )
    else:
        print(f"未知のブローカーです: {settings.broker} (paper か kabus)")
        return 1

    symbols = args.symbols or [
        h.symbol for h in cfg.holdings if h.symbol and h.kind is AssetKind.JP_STOCK
    ]
    if not symbols:
        print("対象銘柄がありません。portfolio.yaml に保有銘柄を書くか、引数で指定してください。")
        return 1

    engine = TradingEngine(broker, source, settings, cfg.rules)
    try:
        result = engine.run(symbols)
    except BrokerError as exc:
        print(f"ブローカーエラー: {exc}")
        return 1

    mode = "ドライラン" if settings.dry_run else "実発注"
    print(f"=== 自動売買 ({settings.broker} / {mode}) ===")
    if result.halted:
        print(f"停止: {result.halt_reason}")
        return 0

    for d in result.decisions:
        mark = "✓" if d.executed else "・"
        print(f" {mark} {d.symbol}: {d.action.value} (スコア {d.score:+.0f}) — {d.detail}")
    if not result.decisions:
        print(" 判定対象がありませんでした。")
    print()
    print(text_report.DISCLAIMER)
    return 0


def cmd_paper(args: argparse.Namespace) -> int:
    source = _source(args)
    broker = PaperBroker(source, initial_cash=args.cash)

    if args.reset:
        broker.reset(args.cash)
        print(f"ペーパー口座を初期化しました (現金 {args.cash:,.0f} 円)")
        return 0

    print(f"現金        : {broker.cash():,.0f} 円")
    positions = broker.positions()
    if positions:
        print("ポジション  :")
        for p in positions:
            try:
                price = broker.last_price(p.symbol)
                pnl = (price - p.avg_price) / p.avg_price * 100.0 if p.avg_price else 0.0
                print(
                    f"   {p.symbol}: {p.qty:.4f} 株 / 取得単価 {p.avg_price:,.1f} / "
                    f"現在値 {price:,.1f} ({pnl:+.1f}%)"
                )
            except BrokerError as exc:
                print(f"   {p.symbol}: {p.qty:.4f} 株 (現在値取得不可: {exc})")
        print(f"評価総額    : {broker.equity():,.0f} 円")
    else:
        print("ポジション  : なし")

    recent = broker.history[-args.history :] if args.history else []
    if recent:
        print("取引履歴    :")
        for row in recent:
            print(
                f"   {row['at']} {row['symbol']} {row['side']} "
                f"{row['qty']:.4f} 株 @ {row['price']:,.1f} ({row['reason']})"
            )
    return 0


# ------------------------------------------------------------------ パーサ


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="paystock",
        description="PayPay 証券の保有資産を分析し、売り時と買い候補を機械的に判定します。",
    )
    parser.add_argument("--version", action="version", version=f"paystock {__version__}")
    parser.add_argument("-c", "--config", default="portfolio.yaml", help="設定ファイル")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="ネットワークを使わずダミー価格で動作 (動作確認専用)",
    )
    parser.add_argument("--no-cache", action="store_true", help="価格キャッシュを使わない")
    parser.add_argument("-v", "--verbose", action="store_true", help="詳細ログを出す")

    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="portfolio.yaml の雛形を作成")
    p.add_argument("--force", action="store_true", help="既存ファイルを上書きする")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("report", help="保有資産の売り時診断")
    p.add_argument("-f", "--format", choices=["text", "html"], default="text")
    p.add_argument("-o", "--output", help="出力先ファイル (省略時は標準出力)")
    p.add_argument(
        "--no-detail",
        dest="detail",
        action="store_false",
        help="判定根拠の詳細を省略する",
    )
    p.set_defaults(func=cmd_report, detail=True)

    p = sub.add_parser("alert", help="対応が必要な銘柄だけを短く表示 (cron 向け)")
    p.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="対応事項が無い日は何も出力しない (cron のメール抑制用)",
    )
    p.add_argument(
        "--notify",
        action="store_true",
        help="Webhook / メールで通知する (宛先は環境変数)",
    )
    p.add_argument(
        "--include-hold", action="store_true", help="保有継続の銘柄も一覧に出す"
    )
    p.set_defaults(func=cmd_alert)

    p = sub.add_parser("screen", help="上昇トレンド + モメンタム上位の銘柄を抽出")
    p.add_argument("-u", "--universe", help="銘柄リスト CSV (code,name,sector)")
    p.add_argument("-n", "--top", type=int, default=15, help="表示件数")
    p.add_argument("--limit", type=int, help="評価する銘柄数の上限 (お試し用)")
    p.add_argument("--relaxed", action="store_true", help="上昇トレンド条件を外す")
    p.add_argument(
        "--exclude-held", action="store_true", help="既に保有している銘柄を除外する"
    )
    p.add_argument("--workers", type=int, default=8, help="並列取得数")
    p.add_argument("-f", "--format", choices=["text", "html"], default="text")
    p.add_argument("-o", "--output", help="出力先ファイル")
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("quote", help="銘柄の指標と判定を表示")
    p.add_argument("symbols", nargs="+", help="銘柄コード (例: 5401 7011)")
    p.set_defaults(func=cmd_quote)

    p = sub.add_parser("backtest", help="判定ルールの過去成績を検証")
    p.add_argument("symbols", nargs="+", help="銘柄コード")
    p.add_argument("--days", type=int, default=1200, help="検証に使う日足の本数")
    p.add_argument("--cash", type=float, default=1_000_000.0, help="初期資金")
    p.add_argument("--fee", type=float, default=0.0, help="手数料率 (%%)")
    p.add_argument("--slippage", type=float, default=0.1, help="スリッページ率 (%%)")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("trade", help="自動売買を 1 巡実行する (既定はドライラン)")
    p.add_argument("symbols", nargs="*", help="対象銘柄 (省略時は保有銘柄)")
    p.add_argument("--dry-run", action="store_true", help="発注せず判断だけ表示")
    p.add_argument(
        "--live",
        action="store_true",
        help="実際に発注する (trade.enabled も true にする必要あり)",
    )
    p.add_argument("--cash", type=float, default=100_000.0, help="ペーパー口座の初期資金")
    p.add_argument(
        "--prod",
        action="store_true",
        help="kabus の本番ポート(18080)を使う。既定は検証ポート(18081)",
    )
    p.set_defaults(func=cmd_trade)

    p = sub.add_parser("paper", help="ペーパートレード口座の状況表示")
    p.add_argument("--reset", action="store_true", help="口座を初期化する")
    p.add_argument("--cash", type=float, default=100_000.0, help="初期資金")
    p.add_argument("--history", type=int, default=10, help="表示する取引履歴の件数")
    p.set_defaults(func=cmd_paper)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except config_mod.ConfigError as exc:
        print(f"設定エラー: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"ファイルが見つかりません: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("中断しました", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
