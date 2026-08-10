"""売り時判定ルールのテスト。"""

from __future__ import annotations

import numpy as np

from conftest import make_bars
from paystock.analysis import indicators as ind_mod
from paystock.analysis.rules import RuleConfig, evaluate, trailing_stop
from paystock.models import Action, Indicators


def test_stop_loss_fires_regardless_of_other_signals():
    """含み損が閾値を超えたら、他がどれだけ強気でも損切り判定になる。"""
    ind = ind_mod.compute(make_bars(np.linspace(1000, 2000, 400)))  # 強い上昇トレンド
    action, _score, reasons, _stop, _target = evaluate(ind, RuleConfig(), pnl_pct=-20.0)

    assert action is Action.STOP_LOSS
    assert reasons[0].label == "損切りライン到達"


def test_healthy_uptrend_is_not_a_sell():
    ind = ind_mod.compute(make_bars(np.linspace(1000, 1400, 400)))
    action, score, _reasons, _stop, _target = evaluate(ind, RuleConfig(), pnl_pct=10.0)

    assert action in (Action.HOLD, Action.BUY_MORE)
    assert score < RuleConfig().trim_score


def test_broken_downtrend_scores_as_sell():
    ind = ind_mod.compute(make_bars(np.linspace(3000, 1500, 400)))
    action, score, _reasons, _stop, _target = evaluate(ind, RuleConfig(), pnl_pct=-5.0)

    assert score > 0
    assert action in (Action.TRIM, Action.SELL)


def test_every_reason_is_reported():
    """スコアの合計と根拠の合計が一致する (説明されない点数が無いこと)。"""
    ind = ind_mod.compute(make_bars(np.linspace(2000, 1000, 400)))
    _action, score, reasons, _stop, _target = evaluate(ind, RuleConfig(), pnl_pct=-30.0)

    assert score == sum(r.score for r in reasons)


def test_trailing_stop_sits_below_the_52w_high():
    ind = ind_mod.compute(make_bars(np.linspace(1000, 2000, 400)))
    stop = trailing_stop(ind, RuleConfig())

    assert stop is not None
    assert stop < ind.high_52w


def test_trailing_stop_is_none_without_atr():
    assert trailing_stop(Indicators(price=100.0, high_52w=120.0), RuleConfig()) is None


def test_wider_atr_multiple_gives_a_looser_stop():
    ind = ind_mod.compute(make_bars(np.linspace(1000, 2000, 400)))
    tight = trailing_stop(ind, RuleConfig(atr_stop_multiple=1.0))
    loose = trailing_stop(ind, RuleConfig(atr_stop_multiple=4.0))

    assert loose < tight


def test_thresholds_are_configurable():
    """同じ指標でも、閾値を厳しくすれば売り判定に変わる。"""
    ind = ind_mod.compute(make_bars(np.linspace(2000, 1800, 400)))

    lenient, _s1, _r1, _st1, _t1 = evaluate(ind, RuleConfig(sell_score=200.0), pnl_pct=-5.0)
    strict, _s2, _r2, _st2, _t2 = evaluate(ind, RuleConfig(sell_score=1.0), pnl_pct=-5.0)

    assert lenient is not Action.SELL
    assert strict is Action.SELL


def test_target_price_respects_reward_risk_ratio():
    ind = Indicators(price=1000.0, high_52w=1100.0, atr14=20.0, sma25=990.0)
    cfg = RuleConfig(atr_stop_multiple=2.5, reward_risk=2.0)
    _action, _score, _reasons, stop, target = evaluate(ind, cfg, pnl_pct=0.0)

    assert stop == 1100.0 - 2.5 * 20.0     # = 1050 … 現値より上なので
    assert target is None                   # 目標株価は出さない


def test_target_price_is_two_r_above_entry_when_stop_is_below():
    ind = Indicators(price=1000.0, high_52w=1010.0, atr14=20.0, sma25=990.0)
    cfg = RuleConfig(atr_stop_multiple=1.0, reward_risk=2.0)
    _action, _score, _reasons, stop, target = evaluate(ind, cfg, pnl_pct=0.0)

    assert stop == 990.0
    assert target == 1000.0 + 2.0 * (1000.0 - 990.0)


def test_screening_mode_skips_holding_only_rules():
    """pnl_pct を渡さない場合、損切り・利確のルールは発火しない。"""
    ind = ind_mod.compute(make_bars(np.linspace(1000, 2000, 400)))
    _action, _score, reasons, _stop, _target = evaluate(ind, RuleConfig(), pnl_pct=None)

    labels = {r.label for r in reasons}
    assert "損切りライン到達" not in labels
    assert "大きな含み益" not in labels
