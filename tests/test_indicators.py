"""指標計算のテスト。値の正しさは手計算できるケースで確認する。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from conftest import make_bars
from paystock.analysis import indicators as ind


def test_sma_matches_manual_average():
    close = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = ind.sma(close, 3)
    assert np.isnan(result.iloc[1])          # 期間に満たない区間は NaN
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[4] == pytest.approx(4.0)


def test_rsi_is_100_when_price_only_rises():
    """下落が一度も無ければ RSI は 100 に張り付く (0 除算にならないこと)。"""
    df = make_bars(np.linspace(100, 200, 60))
    result = ind.rsi(df["close"], 14)
    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_is_low_when_price_only_falls():
    df = make_bars(np.linspace(200, 100, 60))
    assert ind.rsi(df["close"], 14).iloc[-1] < 5.0


def test_rsi_stays_within_bounds_on_mixed_series():
    rng = np.random.default_rng(0)
    closes = 1000 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    result = ind.rsi(make_bars(closes)["close"], 14).dropna()
    assert result.between(0.0, 100.0).all()


def test_macd_histogram_is_line_minus_signal():
    df = make_bars(np.linspace(100, 300, 200))
    line, signal, hist = ind.macd(df["close"])
    assert hist.iloc[-1] == pytest.approx(line.iloc[-1] - signal.iloc[-1])


def test_macd_positive_in_uptrend_negative_in_downtrend():
    up, _s, _h = ind.macd(make_bars(np.linspace(100, 300, 200))["close"])
    down, _s2, _h2 = ind.macd(make_bars(np.linspace(300, 100, 200))["close"])
    assert up.iloc[-1] > 0
    assert down.iloc[-1] < 0


def test_bollinger_bands_bracket_the_middle():
    df = make_bars(np.array([100.0, 102.0, 98.0, 101.0, 99.0] * 10))
    lower, mid, upper = ind.bollinger(df["close"], 20)
    assert lower.iloc[-1] < mid.iloc[-1] < upper.iloc[-1]


def test_atr_is_positive_and_scales_with_range():
    narrow = ind.atr(make_bars([100.0] * 60, spread_pct=0.5), 14).iloc[-1]
    wide = ind.atr(make_bars([100.0] * 60, spread_pct=5.0), 14).iloc[-1]
    assert 0 < narrow < wide


def test_compute_fills_all_fields_with_enough_history():
    result = ind.compute(make_bars(np.linspace(1000, 2000, 400)))
    assert result.price == pytest.approx(2000.0)
    for field in ("sma25", "sma75", "sma200", "rsi14", "atr14", "high_52w", "return_12m"):
        assert getattr(result, field) is not None, field


def test_compute_leaves_long_windows_none_with_short_history():
    """データが短いときに例外を出さず、計算できない指標だけ None にする。"""
    result = ind.compute(make_bars(np.linspace(1000, 1100, 40)))
    assert result.sma25 is not None
    assert result.sma200 is None
    assert result.return_12m is None


def test_compute_rejects_empty_frame():
    with pytest.raises(ValueError):
        ind.compute(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))


def test_drawdown_from_52w_high_is_negative_after_a_fall():
    closes = np.concatenate([np.linspace(1000, 2000, 200), np.linspace(2000, 1500, 100)])
    result = ind.compute(make_bars(closes))
    assert result.drawdown_from_52w_high < -20.0


def test_volume_ratio_detects_a_spike():
    volumes_flat = make_bars([100.0] * 40)
    spiked = volumes_flat.copy()
    spiked.iloc[-1, spiked.columns.get_loc("volume")] *= 5
    assert ind.compute(spiked).volume_ratio > 2.0
