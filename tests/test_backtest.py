"""バックテストのテスト。"""

from __future__ import annotations

import numpy as np
import pytest

from conftest import choppy, falling, make_bars, noisy, trending
from paystock.analysis.rules import RuleConfig
from paystock.backtest import engine as backtest_mod


def test_requires_enough_history():
    with pytest.raises(ValueError, match="日数が足りません"):
        backtest_mod.run("X", make_bars(np.linspace(100, 110, 30)))


def test_equity_curve_covers_every_bar():
    df = trending()
    result = backtest_mod.run("上昇株", df)

    assert len(result.equity_curve) == len(df)
    assert result.equity_curve.index[0] == df.index[0]


def test_no_trades_means_equity_stays_flat():
    """一度も売買しなければ資産は初期資金のまま (現金が勝手に増減しない)。"""
    result = backtest_mod.run("横ばい株", choppy(), rules=RuleConfig(buy_score=-999.0))

    assert not result.trades
    assert result.final_equity == pytest.approx(result.initial_cash)
    assert result.total_return == pytest.approx(0.0)


def test_buy_hold_return_matches_the_price_move():
    df = trending()
    result = backtest_mod.run("上昇株", df)
    first, last = float(df["close"].iloc[0]), float(df["close"].iloc[-1])

    assert result.buy_hold_return == pytest.approx((last - first) / first * 100.0)


def test_max_drawdown_is_zero_or_negative():
    result = backtest_mod.run("上昇株", trending())

    assert result.max_drawdown <= 0.0


def test_cash_never_goes_negative():
    """全期間を通して資産がマイナスにならない (信用取引はしない前提)。"""
    for df in (trending(), falling(), choppy()):
        result = backtest_mod.run("X", df)
        assert (result.equity_curve >= 0).all()


def test_trades_are_recorded_with_entry_and_exit():
    """下落局面を含む系列なら、必ず建玉が閉じられる。"""
    result = backtest_mod.run("X", noisy())

    assert result.closed_trades
    for trade in result.closed_trades:
        assert trade.exit_day is not None
        assert trade.exit_day >= trade.entry_day
        assert trade.return_pct is not None


def test_win_rate_and_profit_factor_are_none_without_closed_trades():
    result = backtest_mod.run("横ばい株", choppy(), rules=RuleConfig(buy_score=-999.0))

    assert result.win_rate is None
    assert result.profit_factor is None


def test_slippage_reduces_the_result():
    df = noisy(seed=3)
    clean = backtest_mod.run("X", df, slippage_pct=0.0)
    costly = backtest_mod.run("X", df, slippage_pct=1.0)

    assert costly.final_equity <= clean.final_equity


def test_fees_reduce_the_result():
    """手数料を上げても売買が止まらず、結果だけが目減りすること。

    手数料を現金から二重に取ると買付が成立しなくなり、
    「手数料が高いほど成績が良い」という誤った結果になる。
    """
    df = noisy(seed=3)
    free = backtest_mod.run("X", df, fee_pct=0.0)
    charged = backtest_mod.run("X", df, fee_pct=1.0)

    assert len(charged.trades) == len(free.trades)
    assert charged.final_equity < free.final_equity


def test_orders_execute_on_the_next_open_not_the_signal_close():
    """終値で判定して同じ終値で約定する、という非現実的な処理になっていないこと。"""
    df = noisy()
    result = backtest_mod.run("X", df, slippage_pct=0.0)

    for trade in result.trades:
        # 約定価格は「その日の始値」であって「前日終値」ではない
        assert trade.entry_price == pytest.approx(float(df.loc[trade.entry_day, "open"]))


def test_summary_is_printable():
    text = backtest_mod.run("上昇株", trending()).summary()

    assert "トータルリターン" in text
    assert "最大ドローダウン" in text
