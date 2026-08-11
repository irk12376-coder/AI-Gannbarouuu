"""テスト共通のフィクスチャ。

価格データはすべて合成する。ネットワークに依存するテストは書かない
(取引所が休みでも CI でも同じ結果になるようにするため)。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from paystock.data.base import PriceSource, PriceSourceError, normalize


def make_bars(
    closes: list[float] | np.ndarray,
    start: str = "2024-01-01",
    volume: float = 1_000_000.0,
    spread_pct: float = 1.0,
) -> pd.DataFrame:
    """終値の列から OHLCV の DataFrame を作る。"""
    closes = np.asarray(closes, dtype=float)
    index = pd.bdate_range(start=start, periods=len(closes))
    spread = closes * spread_pct / 100.0
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return normalize(
        pd.DataFrame(
            {
                "open": opens,
                "high": np.maximum(closes, opens) + spread,
                "low": np.minimum(closes, opens) - spread,
                "close": closes,
                "volume": np.full(len(closes), volume),
            },
            index=index,
        )
    )


def _drift(n: int, start: float, daily_pct: float, wiggle_pct: float) -> np.ndarray:
    """一定方向のドリフトに小さな波を重ねた系列。

    完全に単調な系列は RSI が 0 / 100 に張り付いて現実の相場とかけ離れるので、
    上げ下げの両方が混ざるように少しだけ揺らす。
    """
    steps = np.arange(n)
    wave = 1.0 + np.sin(steps / 5.0) * wiggle_pct / 100.0
    return start * (1 + daily_pct / 100.0) ** steps * wave


def trending(
    n: int = 400, start: float = 1000.0, daily_pct: float = 0.2, wiggle_pct: float = 4.0
) -> pd.DataFrame:
    """上昇トレンドの系列。"""
    return make_bars(_drift(n, start, daily_pct, wiggle_pct))


def falling(
    n: int = 400, start: float = 3000.0, daily_pct: float = -0.2, wiggle_pct: float = 4.0
) -> pd.DataFrame:
    """下降トレンドの系列。"""
    return make_bars(_drift(n, start, daily_pct, wiggle_pct))


def noisy(
    n: int = 500,
    start: float = 1000.0,
    daily_drift: float = 0.0015,
    daily_vol: float = 0.018,
    seed: int = 7,
) -> pd.DataFrame:
    """実際の値動きに近いランダムウォーク (シード固定なので結果は再現する)。

    決まった形の波では MACD の転換や出来高の変化がほとんど起きず、
    エントリー条件が一度も成立しないまま「売買ゼロ」になってしまう。
    売買を伴う経路を検証したいテストはこちらを使う。
    """
    rng = np.random.default_rng(seed)
    closes = start * np.exp(np.cumsum(rng.normal(daily_drift, daily_vol, n)))
    volumes = rng.lognormal(13.0, 0.4, n)
    index = pd.bdate_range(start="2024-01-01", periods=n)
    spread = closes * rng.uniform(0.002, 0.02, n)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    return normalize(
        pd.DataFrame(
            {
                "open": opens,
                "high": np.maximum(closes, opens) + spread,
                "low": np.minimum(closes, opens) - spread,
                "close": closes,
                "volume": volumes,
            },
            index=index,
        )
    )


def choppy(n: int = 400, start: float = 2000.0, amplitude: float = 5.0) -> pd.DataFrame:
    """一定幅で上下する横ばい系列。"""
    wave = np.sin(np.arange(n) / 8.0) * amplitude
    return make_bars(start + wave)


class FakeSource(PriceSource):
    """辞書で与えた DataFrame を返すだけのソース。"""

    name = "fake"

    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[tuple[str, int]] = []

    def history(self, symbol: str, days: int = 400) -> pd.DataFrame:
        self.calls.append((symbol, days))
        if symbol not in self.frames:
            raise PriceSourceError(f"{symbol}: テストデータがありません")
        return self.frames[symbol].tail(days)


@pytest.fixture
def uptrend() -> pd.DataFrame:
    return trending()


@pytest.fixture
def downtrend() -> pd.DataFrame:
    return falling()


@pytest.fixture
def sideways() -> pd.DataFrame:
    return choppy()
