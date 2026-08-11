"""ターミナル向けのレポート整形。

全角文字が混ざるので、幅計算は東アジア文字幅 (unicodedata.east_asian_width) で行う。
半角換算の桁数を数えないと表が崩れる。
"""

from __future__ import annotations

import unicodedata

from ..analysis.portfolio import PortfolioReview
from ..models import Action, ScreenHit, Verdict

DISCLAIMER = (
    "※ 本ツールの出力は公開情報に機械的なルールを当てはめた結果であり、"
    "投資助言ではありません。売買の判断と結果はご自身の責任でお願いします。"
)

_ACTION_MARK = {
    Action.BUY_MORE: "▲ 買い増し検討",
    Action.HOLD: "－ 保有継続",
    Action.TRIM: "▽ 一部利確",
    Action.SELL: "▼ 売却検討",
    Action.STOP_LOSS: "✕ 損切り検討",
    Action.NO_SIGNAL: "? 判定不可",
}


def width(text: str) -> int:
    """半角換算の表示幅。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text: str, target: int, align: str = "left") -> str:
    """表示幅を揃えてパディングする。"""
    gap = max(target - width(text), 0)
    if align == "right":
        return " " * gap + text
    return text + " " * gap


def table(headers: list[str], rows: list[list[str]], aligns: list[str] | None = None) -> str:
    """罫線付きのテキストテーブルを作る。"""
    aligns = aligns or ["left"] * len(headers)
    widths = [width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], width(cell))

    # 区切り線は本文と同じ幅の詰め物 ("─┼─" と " │ ") でつなぐ。
    # 幅の違う文字でつなぐと、罫線だけ長さがずれる。
    sep = "─┼─".join("─" * w for w in widths)
    lines = [" │ ".join(pad(h, widths[i]) for i, h in enumerate(headers)), sep]
    lines += [
        " │ ".join(pad(cell, widths[i], aligns[i]) for i, cell in enumerate(row))
        for row in rows
    ]
    return "\n".join(lines)


def _fmt(value: float | None, suffix: str = "", digits: int = 1) -> str:
    if value is None:
        return "－"
    return f"{value:,.{digits}f}{suffix}"


def _signed(value: float | None, suffix: str = "%") -> str:
    if value is None:
        return "－"
    return f"{value:+,.1f}{suffix}"


def render_holdings(rev: PortfolioReview) -> str:
    rows = []
    for v in rev.verdicts:
        h = v.holding
        value = v.live_market_value or h.market_value
        weight = value / rev.total_value * 100.0 if rev.total_value else 0.0
        rows.append(
            [
                h.name,
                h.symbol or "－",
                f"{weight:,.1f}%",
                _fmt(value, " 円", 0),
                _signed(v.live_pnl_pct),
                _fmt(v.quote.price if v.quote else None, "", 1),
                _fmt(v.indicators.rsi14 if v.indicators else None),
                f"{v.sell_score:+.0f}",
                _ACTION_MARK[v.action],
            ]
        )

    return table(
        ["銘柄", "コード", "比率", "評価額", "損益率", "現在値", "RSI", "売りスコア", "判定"],
        rows,
        ["left", "left", "right", "right", "right", "right", "right", "right", "left"],
    )


def render_verdict_detail(v: Verdict) -> str:
    lines = [f"■ {v.holding.name} ({v.holding.symbol or '－'}) — {_ACTION_MARK[v.action]}"]

    if v.quote:
        avg = v.holding.avg_cost
        lines.append(
            f"   現在値 {v.quote.price:,.1f} / 取得単価 {_fmt(avg)} / "
            f"損益 {_signed(v.live_pnl_pct)}"
        )
    if v.stop_price is not None:
        if v.quote and v.quote.price < v.stop_price:
            # ストップより下にいる = 既に抜けている。利確目標を出すと誤解を招く。
            lines.append(
                f"   トレーリングストップ {v.stop_price:,.1f} — 既に下回っており、"
                "上昇トレンドは崩れています"
            )
        else:
            lines.append(
                f"   推奨損切りライン {v.stop_price:,.1f} / 目安の利確ライン "
                f"{_fmt(v.target_price)}"
            )
    for r in v.reasons:
        sign = "売" if r.score > 0 else "買"
        lines.append(f"   [{sign}{abs(r.score):>4.0f}] {r.label}: {r.detail}")
    for w in v.warnings:
        lines.append(f"   ⚠ {w}")
    return "\n".join(lines)


def render_portfolio_risks(rev: PortfolioReview) -> str:
    lines = ["■ ポートフォリオ全体"]
    lines.append(f"   評価総額 {rev.total_value:,.0f} 円 / 現金比率 {rev.cash_pct:.1f}%")

    if rev.sector_weights:
        top = list(rev.sector_weights.items())[:6]
        lines.append(
            "   セクター構成: " + " / ".join(f"{k} {v:.1f}%" for k, v in top)
        )

    if rev.avg_correlation is not None:
        judge = (
            "非常に高い(分散が効いていない)"
            if rev.avg_correlation >= 0.7
            else "高め"
            if rev.avg_correlation >= 0.5
            else "許容範囲"
        )
        lines.append(
            f"   保有銘柄の平均相関 {rev.avg_correlation:.2f} — {judge}"
        )

    if rev.risks:
        lines.append("   検出されたリスク:")
        for r in rev.risks:
            lines.append(f"     [{r.severity}] {r.label} — {r.detail}")
    else:
        lines.append("   目立った集中リスクは検出されませんでした。")

    return "\n".join(lines)


def render_review(rev: PortfolioReview, detail: bool = True) -> str:
    blocks: list[str] = []

    if rev.offline:
        blocks.append(
            "!!! オフラインモード: 以下の価格はすべてダミーデータです。"
            "実際の投資判断には絶対に使用しないでください。 !!!"
        )

    blocks.append("=== 保有資産の売り時診断 ===")
    blocks.append(render_holdings(rev))
    blocks.append(render_portfolio_risks(rev))

    urgent = [
        v
        for v in rev.verdicts
        if v.action in (Action.STOP_LOSS, Action.SELL, Action.TRIM, Action.BUY_MORE)
    ]
    if urgent:
        blocks.append("=== 要対応 ===")
        blocks.extend(render_verdict_detail(v) for v in urgent)

    if detail:
        others = [v for v in rev.verdicts if v not in urgent]
        if others:
            blocks.append("=== その他 ===")
            blocks.extend(render_verdict_detail(v) for v in others)

    if rev.errors:
        blocks.append("=== 取得エラー ===")
        blocks.extend(f"   ・{e}" for e in rev.errors)

    blocks.append(DISCLAIMER)
    return "\n\n".join(blocks)


def render_screen(hits: list[ScreenHit], errors: list[str], offline: bool = False) -> str:
    blocks: list[str] = []
    if offline:
        blocks.append(
            "!!! オフラインモード: 以下はダミーデータによる出力です。 !!!"
        )

    blocks.append("=== スクリーニング結果 (トレンド継続 + モメンタム上位) ===")

    if not hits:
        blocks.append("条件に合致する銘柄はありませんでした。")
    else:
        rows = [
            [
                str(i + 1),
                h.name,
                h.symbol,
                h.sector,
                f"{h.price:,.1f}",
                _signed(h.indicators.return_12m),
                _fmt(h.indicators.rsi14),
                f"{h.score:.0f}",
            ]
            for i, h in enumerate(hits)
        ]
        blocks.append(
            table(
                ["#", "銘柄", "コード", "業種", "株価", "12ヶ月", "RSI", "スコア"],
                rows,
                ["right", "left", "left", "left", "right", "right", "right", "right"],
            )
        )
        for h in hits:
            lines = [f"■ {h.name} ({h.symbol}) スコア {h.score:.0f}"]
            lines += [f"   ・{r.label}: {r.detail}" for r in h.reasons]
            blocks.append("\n".join(lines))

    if errors:
        blocks.append(f"=== 取得エラー ({len(errors)} 件) ===")
        blocks.extend(f"   ・{e}" for e in errors[:10])

    blocks.append(DISCLAIMER)
    return "\n\n".join(blocks)
