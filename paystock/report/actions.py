"""「今日やること」リストの生成。

売買は利用者が PayPay 証券アプリで手動で行う前提なので、判定を
**そのままアプリに入力できる形** に変換する。PayPay 証券は金額指定売買なので、
株数ではなく「いくら分売るか」を円で出す。

判定 (Verdict) との役割分担:
  - Verdict  … 何が起きているか (トレンド・指標・スコア)
  - ActionItem … だから何をするか (金額・価格・優先順位)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from ..analysis.portfolio import PortfolioReview
from ..config import ManualSettings
from ..models import Action, Verdict

# 優先度: 数字が小さいほど先に対応する。
# 損切りを最優先にするのは、対応が遅れたときの損失が一番大きいため。
_PRIORITY = {
    Action.STOP_LOSS: 1,
    Action.SELL: 2,
    Action.TRIM: 3,
    Action.BUY_MORE: 4,
    Action.HOLD: 5,
    Action.NO_SIGNAL: 6,
}

_VERB = {
    Action.STOP_LOSS: "損切り",
    Action.SELL: "売却",
    Action.TRIM: "一部利確",
    Action.BUY_MORE: "買い増し",
}


@dataclass
class ActionItem:
    """アプリで実行する 1 件の操作。"""

    priority: int
    name: str
    symbol: str | None
    action: Action
    headline: str                       # 「41,456円 すべてを売却」
    amount: float | None = None         # 発注金額 (円)。None なら金額提示なし
    price_note: str = ""                # 損切り / 利確ラインの目安
    reasons: list[str] = field(default_factory=list)
    cautions: list[str] = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        """実際に手を動かす必要がある項目かどうか。"""
        return self.action in _VERB


def _round_amount(amount: float, settings: ManualSettings) -> float:
    """提示金額を丸める。端数を出しても入力しづらいだけなので切り捨てる。"""
    if settings.round_to <= 0:
        return math.floor(amount)
    return math.floor(amount / settings.round_to) * settings.round_to


def _sell_amount(verdict: Verdict, fraction: float, settings: ManualSettings) -> float | None:
    """売却金額。下限に満たない場合は None (刻んで売る意味がないため)。"""
    value = verdict.live_market_value or verdict.holding.market_value
    amount = _round_amount(value * fraction, settings)
    return amount if amount >= settings.min_order_amount else None


def _cautions(verdict: Verdict, settings: ManualSettings, today: date | None) -> list[str]:
    """発注前に見ておくべき注意点。"""
    out: list[str] = []

    days = verdict.holding.days_to_earnings(today)
    if days is not None and days <= settings.earnings_warning_days:
        when = "本日" if days == 0 else f"{days}日後"
        out.append(
            f"決算発表が{when} ({verdict.holding.next_earnings})。"
            "決算跨ぎの値動きは指標では読めないので、持ち越すかどうかを先に決めること"
        )

    out.extend(verdict.warnings)
    return out


def build(
    review: PortfolioReview,
    settings: ManualSettings,
    cash: float = 0.0,
    max_buy_amount: float = 30000.0,
    today: date | None = None,
) -> list[ActionItem]:
    """診断結果を操作リストに変換する。優先度順に並べて返す。"""
    items: list[ActionItem] = []

    for verdict in review.verdicts:
        holding = verdict.holding
        value = verdict.live_market_value or holding.market_value
        reasons = [f"{r.label}: {r.detail}" for r in verdict.reasons[:3]]
        cautions = _cautions(verdict, settings, today)

        price_note = ""
        if verdict.stop_price is not None and verdict.quote:
            if verdict.quote.price < verdict.stop_price:
                price_note = (
                    f"トレーリングストップ {verdict.stop_price:,.0f}円 を既に下回っている"
                )
            else:
                target = (
                    f" / 利確目安 {verdict.target_price:,.0f}円"
                    if verdict.target_price
                    else ""
                )
                price_note = f"損切りライン {verdict.stop_price:,.0f}円{target}"

        amount: float | None = None
        if verdict.action in (Action.STOP_LOSS, Action.SELL):
            amount = _sell_amount(verdict, 1.0, settings)
            headline = (
                f"{value:,.0f}円 すべてを{_VERB[verdict.action]}"
                if amount
                else f"{value:,.0f}円 を{_VERB[verdict.action]} (金額が小さく端数に注意)"
            )
        elif verdict.action is Action.TRIM:
            amount = _sell_amount(verdict, settings.trim_fraction, settings)
            if amount is None:
                # 刻んで売れないので、判定は残しつつ操作は求めない
                headline = (
                    f"利確したいが、{settings.trim_fraction:.0%} が最低発注額 "
                    f"{settings.min_order_amount:,.0f}円 に届かない。全部売るか見送るか"
                )
            else:
                headline = f"{value:,.0f}円 のうち {amount:,.0f}円分 を利確"
        elif verdict.action is Action.BUY_MORE:
            budget = min(max_buy_amount, cash)
            amount = _round_amount(budget, settings)
            if amount < settings.min_order_amount:
                amount = None
                headline = (
                    f"買い増し候補だが、現金 {cash:,.0f}円 では最低発注額に届かない"
                )
            else:
                headline = f"{amount:,.0f}円分 を買い増し"
        elif verdict.action is Action.NO_SIGNAL:
            headline = "判定不可 — 自分で状況を確認する"
        else:
            headline = "対応不要"

        items.append(
            ActionItem(
                priority=_PRIORITY[verdict.action],
                name=holding.name,
                symbol=holding.symbol,
                action=verdict.action,
                headline=headline,
                amount=amount,
                price_note=price_note,
                reasons=reasons,
                cautions=cautions,
            )
        )

    # 同じ優先度なら金額の大きいものを先に出す (影響が大きい順)
    items.sort(key=lambda i: (i.priority, -(i.amount or 0.0)))
    return items
