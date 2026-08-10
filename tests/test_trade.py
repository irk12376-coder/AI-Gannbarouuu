"""ペーパートレードと安全装置のテスト。

自動売買で一番怖いのは「止まるべきときに止まらないこと」なので、
RiskGuard が止める側のケースを重点的に確認する。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from conftest import FakeSource, falling, trending
from paystock.config import TradeSettings
from paystock.models import Order, OrderSide
from paystock.trade.base import BrokerError
from paystock.trade.engine import JST, RiskGuard, TradingEngine, is_market_open
from paystock.trade.paper import PaperBroker


@pytest.fixture
def frames():
    return {"5401": trending(), "7011": falling()}


@pytest.fixture
def broker(tmp_path, frames):
    return PaperBroker(
        FakeSource(frames),
        state_path=tmp_path / "paper.json",
        initial_cash=100_000.0,
        slippage_pct=0.0,
    )


# ------------------------------------------------------------- PaperBroker


def test_buy_reduces_cash_and_creates_a_position(broker):
    broker.submit(Order(symbol="5401", side=OrderSide.BUY, amount=30_000))

    position = broker.position_for("5401")
    assert position is not None
    assert broker.cash() == pytest.approx(70_000.0)
    assert position.qty * position.avg_price == pytest.approx(30_000.0)


def test_sell_returns_cash_and_clears_the_position(broker):
    broker.submit(Order(symbol="5401", side=OrderSide.BUY, amount=30_000))
    position = broker.position_for("5401")
    broker.submit(Order(symbol="5401", side=OrderSide.SELL, qty=position.qty))

    assert broker.position_for("5401") is None
    assert broker.cash() == pytest.approx(100_000.0)


def test_buying_more_than_cash_is_rejected(broker):
    with pytest.raises(BrokerError, match="現金不足"):
        broker.submit(Order(symbol="5401", side=OrderSide.BUY, amount=200_000))


def test_selling_more_than_held_is_rejected(broker):
    broker.submit(Order(symbol="5401", side=OrderSide.BUY, amount=10_000))
    with pytest.raises(BrokerError, match="売却はできません"):
        broker.submit(Order(symbol="5401", side=OrderSide.SELL, qty=1_000_000))


def test_average_cost_is_recomputed_on_a_second_buy(broker):
    broker.submit(Order(symbol="5401", side=OrderSide.BUY, amount=10_000))
    first = broker.position_for("5401").avg_price
    broker.submit(Order(symbol="5401", side=OrderSide.BUY, amount=10_000))
    position = broker.position_for("5401")

    assert position.qty * position.avg_price == pytest.approx(20_000.0)
    assert position.avg_price == pytest.approx(first)  # 同じ価格で買えば単価は変わらない


def test_slippage_makes_buys_more_expensive(tmp_path, frames):
    slipped = PaperBroker(
        FakeSource(frames), state_path=tmp_path / "s.json", slippage_pct=1.0
    )
    fill = slipped.submit(Order(symbol="5401", side=OrderSide.BUY, amount=10_000))

    assert fill.filled_price > slipped.last_price("5401")


def test_state_survives_a_restart(tmp_path, frames):
    path = tmp_path / "paper.json"
    first = PaperBroker(FakeSource(frames), state_path=path, initial_cash=100_000)
    first.submit(Order(symbol="5401", side=OrderSide.BUY, amount=25_000))

    second = PaperBroker(FakeSource(frames), state_path=path)
    assert second.position_for("5401") is not None
    assert second.cash() == pytest.approx(first.cash())


def test_order_requires_qty_or_amount():
    with pytest.raises(ValueError):
        Order(symbol="5401", side=OrderSide.BUY)


# ------------------------------------------------------------- 立会時間


@pytest.mark.parametrize(
    "moment,expected",
    [
        ("2026-08-10 10:00", True),    # 月曜 前場
        ("2026-08-10 13:00", True),    # 月曜 後場
        ("2026-08-10 11:45", False),   # 昼休み
        ("2026-08-10 08:30", False),   # 寄り前
        ("2026-08-10 16:00", False),   # 大引け後
        ("2026-08-08 10:00", False),   # 土曜
        ("2026-08-09 10:00", False),   # 日曜
    ],
)
def test_market_hours(moment, expected):
    now = datetime.strptime(moment, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    assert is_market_open(now) is expected


# ------------------------------------------------------------- RiskGuard


def open_time() -> datetime:
    return datetime(2026, 8, 10, 10, 0, tzinfo=JST)


def test_guard_blocks_when_trading_is_disabled(tmp_path):
    guard = RiskGuard(TradeSettings(enabled=False), tmp_path / "state.json")
    result = guard.begin_session(100_000, open_time())

    assert not result.ok
    assert "enabled" in result.reason


def test_guard_blocks_on_kill_switch(tmp_path):
    kill = tmp_path / "KILL"
    kill.write_text("stop", encoding="utf-8")
    guard = RiskGuard(
        TradeSettings(enabled=True, kill_switch_file=str(kill)), tmp_path / "state.json"
    )

    assert not guard.begin_session(100_000, open_time()).ok


def test_guard_blocks_outside_trading_hours(tmp_path):
    guard = RiskGuard(TradeSettings(enabled=True), tmp_path / "state.json")
    midnight = datetime(2026, 8, 10, 3, 0, tzinfo=JST)

    assert not guard.begin_session(100_000, midnight).ok


def test_guard_halts_after_the_daily_loss_limit(tmp_path):
    state = tmp_path / "state.json"
    settings = TradeSettings(enabled=True, max_daily_loss_pct=5.0)

    RiskGuard(settings, state).begin_session(100_000, open_time())   # 当日の基準を記録
    later = RiskGuard(settings, state).begin_session(93_000, open_time())

    assert not later.ok
    assert "損失" in later.reason


def test_guard_allows_a_small_intraday_loss(tmp_path):
    state = tmp_path / "state.json"
    settings = TradeSettings(enabled=True, max_daily_loss_pct=5.0)

    RiskGuard(settings, state).begin_session(100_000, open_time())
    assert RiskGuard(settings, state).begin_session(98_000, open_time()).ok


def test_guard_caps_the_order_amount(tmp_path, broker):
    guard = RiskGuard(TradeSettings(enabled=True, max_order_amount=10_000), tmp_path / "s.json")
    order = Order(symbol="5401", side=OrderSide.BUY, amount=50_000)

    result = guard.check_order(order, 50_000, 100_000, broker)
    assert not result.ok
    assert "上限" in result.reason


def test_guard_caps_the_position_weight(tmp_path, broker):
    guard = RiskGuard(
        TradeSettings(enabled=True, max_order_amount=100_000, max_position_pct=20.0),
        tmp_path / "s.json",
    )
    order = Order(symbol="5401", side=OrderSide.BUY, amount=50_000)

    result = guard.check_order(order, 50_000, 100_000, broker)
    assert not result.ok
    assert "比率" in result.reason


def test_guard_preserves_the_cash_buffer(tmp_path, broker):
    guard = RiskGuard(
        TradeSettings(
            enabled=True,
            max_order_amount=100_000,
            max_position_pct=100.0,
            min_cash_buffer=5_000,
        ),
        tmp_path / "s.json",
    )
    order = Order(symbol="5401", side=OrderSide.BUY, amount=99_000)

    assert not guard.check_order(order, 99_000, 100_000, broker).ok


def test_guard_caps_the_daily_order_count(tmp_path, broker):
    guard = RiskGuard(
        TradeSettings(enabled=True, max_orders_per_day=2, max_order_amount=100_000),
        tmp_path / "s.json",
    )
    guard.begin_session(100_000, open_time())
    order = Order(symbol="5401", side=OrderSide.SELL, qty=1)

    guard.record_order()
    guard.record_order()
    result = guard.check_order(order, 1_000, 100_000, broker)

    assert not result.ok
    assert "発注回数" in result.reason


def test_daily_counters_reset_on_a_new_day(tmp_path, broker):
    state = tmp_path / "s.json"
    settings = TradeSettings(enabled=True, max_orders_per_day=1, max_order_amount=100_000)

    first = RiskGuard(settings, state)
    first.begin_session(100_000, datetime(2026, 8, 10, 10, 0, tzinfo=JST))
    first.record_order()

    second = RiskGuard(settings, state)
    second.begin_session(100_000, datetime(2026, 8, 11, 10, 0, tzinfo=JST))

    order = Order(symbol="5401", side=OrderSide.SELL, qty=1)
    assert second.check_order(order, 1_000, 100_000, broker).ok


# ------------------------------------------------------------ TradingEngine


def make_engine(tmp_path, broker, **overrides):
    defaults = {
        "enabled": True,
        "dry_run": False,
        "max_order_amount": 30_000,
        "max_position_pct": 50.0,
        "trading_hours_only": False,
    }
    settings = TradeSettings(**{**defaults, **overrides})
    return TradingEngine(
        broker,
        broker.source,
        settings,
        journal_path=tmp_path / "journal.jsonl",
        state_path=tmp_path / "state.json",
    )


def buy_into_a_loss(broker, symbol="7011", amount=20_000):
    """下降トレンド銘柄を高値で掴んだ状態を作る (売りシグナルが出る前提を用意する)。"""
    broker.submit(Order(symbol=symbol, side=OrderSide.BUY, amount=amount))
    broker._state["positions"][symbol]["avg_price"] *= 3


def test_engine_does_not_trade_while_halted(tmp_path, broker):
    engine = make_engine(tmp_path, broker, enabled=False)
    result = engine.run(["5401"], now=open_time())

    assert result.halted
    assert result.decisions == []
    assert broker.cash() == pytest.approx(100_000.0)


def test_dry_run_never_places_an_order(tmp_path, broker):
    buy_into_a_loss(broker)
    cash_before = broker.cash()
    engine = make_engine(tmp_path, broker, dry_run=True)

    result = engine.run(["7011"])

    assert result.executed == []
    assert broker.position_for("7011") is not None      # 売られていない
    assert broker.cash() == pytest.approx(cash_before)
    assert any("ドライラン" in d.detail for d in result.decisions)


def test_engine_sells_a_position_that_hits_the_stop_loss(tmp_path, broker):
    buy_into_a_loss(broker)
    engine = make_engine(tmp_path, broker)

    result = engine.run(["7011"])

    assert broker.position_for("7011") is None
    assert any(d.executed and d.symbol == "7011" for d in result.decisions)


def test_engine_does_not_sell_what_it_does_not_hold(tmp_path, broker):
    """売りシグナルが出ても、保有していなければ空売りには行かない。"""
    engine = make_engine(tmp_path, broker)

    result = engine.run(["7011"])

    assert result.executed == []
    assert any("保有していない" in d.detail for d in result.decisions)


def test_engine_skips_symbols_without_price_data(tmp_path, broker):
    engine = make_engine(tmp_path, broker)

    result = engine.run(["9999"])

    assert result.decisions == []
    assert not result.halted


def test_engine_writes_a_journal_line_for_orders(tmp_path, broker):
    buy_into_a_loss(broker)
    journal = tmp_path / "journal.jsonl"
    engine = make_engine(tmp_path, broker, dry_run=True)

    engine.run(["7011"])

    assert journal.exists()
    assert journal.read_text(encoding="utf-8").strip()
