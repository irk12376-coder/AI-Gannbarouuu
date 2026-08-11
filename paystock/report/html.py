"""HTML レポートの生成。

スマホで見ることを前提に、単一ファイル・外部リソース無しで完結させる。
ライト/ダークどちらの端末設定でも読めるように配色を両方定義する。
"""

from __future__ import annotations

import html
from datetime import datetime

from ..analysis.portfolio import PortfolioReview
from ..models import Action, ScreenHit
from .text import DISCLAIMER

_ACTION_CLASS = {
    Action.BUY_MORE: "buy",
    Action.HOLD: "hold",
    Action.TRIM: "trim",
    Action.SELL: "sell",
    Action.STOP_LOSS: "stop",
    Action.NO_SIGNAL: "none",
}

_ACTION_LABEL = {
    Action.BUY_MORE: "買い増し検討",
    Action.HOLD: "保有継続",
    Action.TRIM: "一部利確",
    Action.SELL: "売却検討",
    Action.STOP_LOSS: "損切り検討",
    Action.NO_SIGNAL: "判定不可",
}

_CSS = """
:root {
  --bg: #ffffff; --fg: #1b1c1e; --muted: #6b7075; --line: #e3e6ea;
  --card: #f7f8fa; --up: #0a8f4a; --down: #cf2f45; --warn: #a8620a;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16181c; --fg: #e8eaed; --muted: #9aa0a6; --line: #2c3036;
    --card: #1e2126; --up: #3ecf7a; --down: #ff6b7d; --warn: #e0a33c;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; padding: 16px; background: var(--bg); color: var(--fg);
  font-family: -apple-system, BlinkMacSystemFont, "Hiragino Sans",
    "Noto Sans JP", "Yu Gothic", sans-serif;
  line-height: 1.6; font-size: 15px;
}
h1 { font-size: 20px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 28px 0 10px; padding-bottom: 6px;
     border-bottom: 1px solid var(--line); }
.meta { color: var(--muted); font-size: 13px; margin-bottom: 8px; }
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table { border-collapse: collapse; width: 100%; font-size: 13px; min-width: 560px; }
th, td { padding: 8px 10px; border-bottom: 1px solid var(--line); text-align: right;
         white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
th { color: var(--muted); font-weight: 600; }
.up { color: var(--up); } .down { color: var(--down); }
.badge { display: inline-block; padding: 2px 8px; border-radius: 999px;
         font-size: 12px; font-weight: 600; }
.badge.buy  { background: rgba(62,207,122,.16); color: var(--up); }
.badge.hold { background: rgba(128,128,128,.16); color: var(--muted); }
.badge.trim { background: rgba(224,163,60,.18); color: var(--warn); }
.badge.sell { background: rgba(255,107,125,.16); color: var(--down); }
.badge.stop { background: var(--down); color: #fff; }
.badge.none { background: rgba(128,128,128,.12); color: var(--muted); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px;
        padding: 12px 14px; margin: 10px 0; }
.card h3 { margin: 0 0 6px; font-size: 15px; }
ul.reasons { margin: 8px 0 0; padding-left: 18px; font-size: 13px; color: var(--muted); }
ul.reasons li { margin: 2px 0; }
.warn { color: var(--warn); font-size: 13px; margin-top: 6px; }
.banner { background: var(--down); color: #fff; padding: 10px 12px;
          border-radius: 8px; font-weight: 600; margin-bottom: 14px; }
footer { margin-top: 32px; color: var(--muted); font-size: 12px;
         border-top: 1px solid var(--line); padding-top: 12px; }
"""


def _e(text: object) -> str:
    return html.escape(str(text))


def _num(value: float | None, digits: int = 1, suffix: str = "") -> str:
    return "－" if value is None else f"{value:,.{digits}f}{suffix}"


def _signed_cell(value: float | None, suffix: str = "%") -> str:
    if value is None:
        return "<td>－</td>"
    cls = "up" if value >= 0 else "down"
    return f'<td class="{cls}">{value:+,.1f}{suffix}</td>'


def _page(title: str, body: str) -> str:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    return (
        "<!doctype html>\n"
        '<html lang="ja"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>"
        f"<h1>{_e(title)}</h1>"
        f'<div class="meta">生成日時: {stamp}</div>'
        f"{body}"
        f"<footer>{_e(DISCLAIMER)}</footer>"
        "</body></html>\n"
    )


def _holdings_table(rev: PortfolioReview) -> str:
    rows = []
    for v in rev.verdicts:
        h = v.holding
        value = v.live_market_value or h.market_value
        weight = value / rev.total_value * 100.0 if rev.total_value else 0.0
        badge = (
            f'<span class="badge {_ACTION_CLASS[v.action]}">'
            f"{_ACTION_LABEL[v.action]}</span>"
        )
        rows.append(
            f"<tr><td>{_e(h.name)}</td><td>{_e(h.symbol or '－')}</td>"
            f"<td>{weight:,.1f}%</td><td>{value:,.0f}</td>"
            f"{_signed_cell(v.live_pnl_pct)}"
            f"<td>{_num(v.quote.price if v.quote else None)}</td>"
            f"<td>{_num(v.indicators.rsi14 if v.indicators else None)}</td>"
            f"<td>{v.sell_score:+.0f}</td><td>{badge}</td></tr>"
        )
    head = (
        "<tr><th>銘柄</th><th>コード</th><th>比率</th><th>評価額</th><th>損益率</th>"
        "<th>現在値</th><th>RSI</th><th>売りスコア</th><th>判定</th></tr>"
    )
    return f'<div class="scroll"><table>{head}{"".join(rows)}</table></div>'


def _risk_section(rev: PortfolioReview) -> str:
    parts = [
        f"<p>評価総額 <b>{rev.total_value:,.0f} 円</b> / 現金比率 "
        f"<b>{rev.cash_pct:.1f}%</b></p>"
    ]
    if rev.sector_weights:
        items = "".join(
            f"<li>{_e(k)}: {v:.1f}%</li>" for k, v in list(rev.sector_weights.items())[:8]
        )
        parts.append(f'<ul class="reasons">{items}</ul>')
    if rev.avg_correlation is not None:
        parts.append(
            f"<p>保有銘柄の平均相関: <b>{rev.avg_correlation:.2f}</b>"
            "(1.0 に近いほど同じ方向に動く = 分散が効いていない)</p>"
        )
    for r in rev.risks:
        parts.append(
            f'<div class="card"><h3>[{_e(r.severity)}] {_e(r.label)}</h3>'
            f"<div>{_e(r.detail)}</div></div>"
        )
    if not rev.risks:
        parts.append("<p>目立った集中リスクは検出されませんでした。</p>")
    return "".join(parts)


def _verdict_cards(rev: PortfolioReview) -> str:
    cards = []
    for v in rev.verdicts:
        reasons = "".join(
            f"<li>{'売' if r.score > 0 else '買'} {abs(r.score):.0f}: "
            f"{_e(r.label)} — {_e(r.detail)}</li>"
            for r in v.reasons
        )
        warns = "".join(f'<div class="warn">⚠ {_e(w)}</div>' for w in v.warnings)
        levels = ""
        if v.stop_price is not None:
            if v.quote and v.quote.price < v.stop_price:
                levels = (
                    f"<p>トレーリングストップ <b>{v.stop_price:,.1f}</b> — "
                    "既に下回っており、上昇トレンドは崩れています</p>"
                )
            else:
                levels = (
                    f"<p>推奨損切りライン <b>{v.stop_price:,.1f}</b> / "
                    f"目安の利確ライン <b>{_num(v.target_price)}</b></p>"
                )
        cards.append(
            f'<div class="card"><h3>{_e(v.holding.name)} '
            f'<span class="badge {_ACTION_CLASS[v.action]}">'
            f"{_ACTION_LABEL[v.action]}</span></h3>"
            f"{levels}"
            f'<ul class="reasons">{reasons}</ul>{warns}</div>'
        )
    return "".join(cards)


def render_review(rev: PortfolioReview) -> str:
    banner = (
        '<div class="banner">オフラインモード: 表示中の価格はダミーデータです。'
        "投資判断には使用しないでください。</div>"
        if rev.offline
        else ""
    )
    errors = ""
    if rev.errors:
        items = "".join(f"<li>{_e(e)}</li>" for e in rev.errors)
        errors = f'<h2>取得エラー</h2><ul class="reasons">{items}</ul>'

    body = (
        banner
        + "<h2>保有資産</h2>"
        + _holdings_table(rev)
        + "<h2>ポートフォリオ診断</h2>"
        + _risk_section(rev)
        + "<h2>銘柄ごとの判定根拠</h2>"
        + _verdict_cards(rev)
        + errors
    )
    return _page("保有資産レポート", body)


def render_screen(hits: list[ScreenHit], errors: list[str], offline: bool = False) -> str:
    banner = (
        '<div class="banner">オフラインモード: ダミーデータによる出力です。</div>'
        if offline
        else ""
    )

    rows = "".join(
        f"<tr><td>{i + 1}. {_e(h.name)}</td><td>{_e(h.symbol)}</td>"
        f"<td>{_e(h.sector)}</td><td>{h.price:,.1f}</td>"
        f"{_signed_cell(h.indicators.return_12m)}"
        f"<td>{_num(h.indicators.rsi14)}</td><td>{h.score:.0f}</td></tr>"
        for i, h in enumerate(hits)
    )
    head = (
        "<tr><th>銘柄</th><th>コード</th><th>業種</th><th>株価</th>"
        "<th>12ヶ月</th><th>RSI</th><th>スコア</th></tr>"
    )
    table = f'<div class="scroll"><table>{head}{rows}</table></div>'

    cards = "".join(
        f'<div class="card"><h3>{_e(h.name)} ({_e(h.symbol)}) — スコア {h.score:.0f}</h3>'
        + '<ul class="reasons">'
        + "".join(f"<li>{_e(r.label)}: {_e(r.detail)}</li>" for r in h.reasons)
        + "</ul></div>"
        for h in hits
    )

    err_block = ""
    if errors:
        items = "".join(f"<li>{_e(e)}</li>" for e in errors[:20])
        err_block = f'<h2>取得エラー ({len(errors)} 件)</h2><ul class="reasons">{items}</ul>'

    body = banner + "<h2>候補一覧</h2>" + table + "<h2>選定理由</h2>" + cards + err_block
    return _page("スクリーニング結果", body)
