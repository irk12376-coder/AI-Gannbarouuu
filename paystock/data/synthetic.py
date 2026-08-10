"""ダミー価格の生成 (オフラインのデモ・テスト専用)。

ネットワークが無い環境でも CLI の出力やバックテストの挙動を確認できるように、
銘柄コードから決定的に生成した幾何ブラウン運動を返す。

**このソースが返す価格は完全な作り物である。** 誤用を防ぐため、
`--offline` を付けたときだけ選択され、レポートには必ず警告が出る。
"""

from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd

from .base import DEFAULT_HISTORY_DAYS, PriceSource, normalize


def _seed_for(symbol: str) -> int:
    """銘柄コードから決定的なシードを作る (実行のたびに同じ系列になる)。"""
    digest = hashlib.sha256(symbol.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


class SyntheticSource(PriceSource):
    name = "synthetic"

    def __init__(self, base_price: float = 2000.0, days_back: int = 500) -> None:
        self.base_price = base_price
        self.days_back = days_back

    def history(self, symbol: str, days: int = DEFAULT_HISTORY_DAYS) -> pd.DataFrame:
        rng = np.random.default_rng(_seed_for(symbol))
        n = max(days, 260)

        # 銘柄ごとにドリフトとボラティリティを散らして、上昇/下降/横ばいを混在させる
        drift = rng.normal(0.0004, 0.0009)
        vol = rng.uniform(0.012, 0.030)
        start = self.base_price * rng.uniform(0.2, 12.0)

        shocks = rng.normal(drift, vol, n)
        close = start * np.exp(np.cumsum(shocks))

        intraday = np.abs(rng.normal(0.0, vol * 0.7, n))
        high = close * (1.0 + intraday)
        low = close * (1.0 - intraday)
        open_ = np.concatenate([[close[0]], close[:-1]]) * (
            1.0 + rng.normal(0.0, vol * 0.3, n)
        )
        # 始値が高安のレンジから外れないように内側へ寄せる
        high = np.maximum.reduce([high, open_, close])
        low = np.minimum.reduce([low, open_, close])
        volume = rng.lognormal(13.0, 0.5, n)

        index = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
        df = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            },
            index=index,
        )
        return normalize(df).tail(days)
