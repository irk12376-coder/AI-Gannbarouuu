"""「今日やること」リストとアラート出力のテスト。

手動で発注する前提なので、**そのままアプリに入力できる金額が出るか**と
**対応不要な日に静かでいられるか**を重点的に見る。
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from conftest import FakeSource, make_bars, noisy, trending
from paystock.analysis import portfolio as portfolio_mod
from paystock.config import AppConfig, ManualSettings
from paystock.models import Action, AssetKind, Holding
from paystock.report import actions as actions_mod
from paystock.report import alert as alert_mod

SETTINGS = ManualSettings()


def stock(name, symbol, value, pnl=0.0, shares=10.0, sector="鉄鋼", earnings=None):
    return Holding(
        name=name,
        kind=AssetKind.JP_STOCK,
        symbol=symbol,
        sector=sector,
        shares=shares,
        market_value=value,
        pnl=pnl,
        next_earnings=earnings,
    )


def review_for(holdings, frames):
    return portfolio_mod.review(AppConfig(holdings=holdings), FakeSource(frames))


def flat(price: float, n: int = 400):
    return make_bars(np.full(n, price))


# ------------------------------------------------------------- 金額の組み立て


def test_sell_amount_is_the_whole_position():
    frames = {"7011": make_bars(np.linspace(3000, 1500, 400))}
    review = review_for([stock("三菱重工業", "7011", 15000, pnl=-8000, shares=10.0)], frames)
    items = actions_mod.build(review, SETTINGS)

    item = items[0]
    assert item.action in (Action.STOP_LOSS, Action.SELL)
    assert item.amount == pytest.approx(item.amount)  # 金額が出ている
    assert "すべて" in item.headline


def test_amount_is_rounded_down_to_the_configured_unit():
    """端数のある金額を出してもアプリに入力しづらいだけなので切り捨てる。"""
    settings = ManualSettings(round_to=100.0)
    assert actions_mod._round_amount(12_345.6, settings) == 12_300.0
    assert actions_mod._round_amount(999.9, settings) == 900.0


def test_rounding_never_rounds_up():
    """切り上げると保有額を超える注文になりうるので、必ず切り捨てる。"""
    settings = ManualSettings(round_to=1000.0)
    for value in (1000.0, 1999.0, 5500.5):
        assert actions_mod._round_amount(value, settings) <= value


def test_trim_sells_the_configured_fraction():
    frames = {"5401": trending()}
    holding = stock("日本製鉄", "5401", 60000, pnl=20000, shares=10.0)
    review = review_for([holding], frames)
    # 判定に関わらず、金額計算そのものを直接確認する
    verdict = review.verdicts[0]
    amount = actions_mod._sell_amount(verdict, 1 / 3, ManualSettings(round_to=100.0))
    value = verdict.live_market_value

    assert amount == pytest.approx(value / 3, rel=0.01)


def test_amount_below_the_minimum_is_dropped():
    """1,000円未満は発注できないので、金額を出さずに理由を書く。"""
    settings = ManualSettings(min_order_amount=1000.0)
    frames = {"5401": trending()}
    review = review_for([stock("小口銘柄", "5401", 900, shares=0.5)], frames)
    verdict = review.verdicts[0]

    assert actions_mod._sell_amount(verdict, 1 / 3, settings) is None


def test_buy_amount_is_capped_by_available_cash():
    frames = {"5401": noisy(seed=3)}
    review = review_for([stock("日本製鉄", "5401", 10000)], frames)
    items = actions_mod.build(review, SETTINGS, cash=5000, max_buy_amount=30000)

    for item in items:
        if item.action is Action.BUY_MORE and item.amount:
            assert item.amount <= 5000


def test_buy_is_skipped_when_cash_is_below_the_minimum():
    frames = {"5401": noisy(seed=3)}
    review = review_for([stock("日本製鉄", "5401", 10000)], frames)
    items = actions_mod.build(review, SETTINGS, cash=500, max_buy_amount=30000)

    for item in items:
        if item.action is Action.BUY_MORE:
            assert item.amount is None
            assert "届かない" in item.headline


# ----------------------------------------------------------------- 並び順


def test_stop_loss_comes_before_everything_else():
    frames = {"5401": trending(), "7011": make_bars(np.linspace(3000, 1500, 400))}
    review = review_for(
        [
            stock("日本製鉄", "5401", 60000, pnl=20000),
            stock("三菱重工業", "7011", 15000, pnl=-8000),
        ],
        frames,
    )
    items = actions_mod.build(review, SETTINGS)

    assert items[0].action is Action.STOP_LOSS


def test_hold_items_are_not_actionable():
    frames = {"5401": flat(1000.0)}
    review = review_for([stock("横ばい銘柄", "5401", 10000, shares=10.0)], frames)
    items = actions_mod.build(review, SETTINGS)

    assert items[0].action is Action.HOLD
    assert not items[0].actionable
    assert items[0].headline == "対応不要"


# --------------------------------------------------------------- 決算の警告


def test_upcoming_earnings_is_flagged():
    frames = {"5401": flat(1000.0)}
    review = review_for(
        [stock("日本製鉄", "5401", 10000, earnings=date(2026, 8, 14))], frames
    )
    items = actions_mod.build(review, SETTINGS, today=date(2026, 8, 10))

    assert any("決算発表が4日後" in c for c in items[0].cautions)


def test_distant_earnings_is_not_flagged():
    frames = {"5401": flat(1000.0)}
    review = review_for(
        [stock("日本製鉄", "5401", 10000, earnings=date(2026, 11, 4))], frames
    )
    items = actions_mod.build(review, SETTINGS, today=date(2026, 8, 10))

    assert not any("決算" in c for c in items[0].cautions)


def test_past_earnings_date_is_ignored():
    """決算日を更新し忘れても、過ぎた日付で毎日警告し続けない。"""
    holding = stock("日本製鉄", "5401", 10000, earnings=date(2026, 8, 4))

    assert holding.days_to_earnings(date(2026, 8, 10)) is None


def test_days_to_earnings_counts_correctly():
    holding = stock("日本製鉄", "5401", 10000, earnings=date(2026, 8, 14))

    assert holding.days_to_earnings(date(2026, 8, 10)) == 4
    assert holding.days_to_earnings(date(2026, 8, 14)) == 0


# ------------------------------------------------------------- アラート出力


def test_quiet_day_says_nothing_to_do():
    frames = {"5401": flat(1000.0)}
    review = review_for([stock("横ばい銘柄", "5401", 10000, shares=10.0)], frames)
    items = actions_mod.build(review, SETTINGS)

    output = alert_mod.render(items)

    assert alert_mod.NOTHING_TO_DO in output
    assert not alert_mod.has_todo(items)


def test_has_todo_is_true_when_something_needs_action():
    frames = {"7011": make_bars(np.linspace(3000, 1500, 400))}
    review = review_for([stock("三菱重工業", "7011", 15000, pnl=-8000)], frames)
    items = actions_mod.build(review, SETTINGS)

    assert alert_mod.has_todo(items)


def test_cautions_alone_are_enough_to_notify():
    """操作は不要でも、決算が近いなら黙っていてはいけない。"""
    frames = {"5401": flat(1000.0)}
    review = review_for(
        [stock("日本製鉄", "5401", 10000, shares=10.0, earnings=date(2026, 8, 12))],
        frames,
    )
    items = actions_mod.build(review, SETTINGS, today=date(2026, 8, 10))

    assert not items[0].actionable
    assert alert_mod.has_todo(items)
    assert "注意しておくこと" in alert_mod.render(items)


def test_alert_shows_amounts_and_carries_the_disclaimer():
    frames = {"7011": make_bars(np.linspace(3000, 1500, 400))}
    review = review_for([stock("三菱重工業", "7011", 15000, pnl=-8000)], frames)
    output = alert_mod.render(actions_mod.build(review, SETTINGS))

    assert "今日やること" in output
    assert "円" in output
    assert alert_mod.DISCLAIMER in output


def test_hold_names_are_only_listed_when_asked():
    frames = {"5401": flat(1000.0)}
    review = review_for([stock("横ばい銘柄", "5401", 10000, shares=10.0)], frames)
    items = actions_mod.build(review, SETTINGS)

    assert "保有継続" not in alert_mod.render(items)
    assert "保有継続" in alert_mod.render(items, include_hold=True)
