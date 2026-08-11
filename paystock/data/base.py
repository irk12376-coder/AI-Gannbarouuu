"""価格データソースの共通インタフェース。

実装は data/yahoo.py, data/stooq.py, data/synthetic.py。
どの実装も `history()` で同じ形の DataFrame を返す契約になっている:

    index : pd.DatetimeIndex (昇順, tz-naive)
    列    : open, high, low, close, volume (すべて float)
"""

from __future__ import annotations

import abc
import re

import pandas as pd

from ..models import Quote

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]

# 取得日数は全経路でこの値に揃える。揃えないとキャッシュのキーが分かれて
# 同じ銘柄を何度も取りに行く上、経路によって現在値が食い違いうる。
DEFAULT_HISTORY_DAYS = 400

_JP_CODE = re.compile(r"^\d{4}[0-9A-Z]?$")


class PriceSourceError(RuntimeError):
    """価格取得に失敗したときに投げる。"""


def is_jp_code(symbol: str) -> bool:
    """4桁の証券コード (末尾に英字が付く新形式も含む) かどうか。"""
    return bool(_JP_CODE.match(symbol.strip().upper()))


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    """各ソースの生 DataFrame を共通フォーマットに整える。"""
    df = df.rename(columns={c: str(c).strip().lower() for c in df.columns})
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise PriceSourceError(f"価格データに必要な列がありません: {missing}")

    df = df[REQUIRED_COLUMNS].astype(float)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    # 終値が欠けている行は指標計算を壊すので落とす
    return df.dropna(subset=["close"])


def quote_from_history(symbol: str, df: pd.DataFrame) -> Quote:
    """取得済みの日足から現在値を組み立てる。

    既に history() を呼んだ側は、これを使って二重取得を避ける。
    """
    if df.empty:
        raise PriceSourceError(f"{symbol}: 価格データが取得できませんでした")
    return Quote(
        symbol=symbol,
        price=float(df["close"].iloc[-1]),
        previous_close=float(df["close"].iloc[-2]) if len(df) >= 2 else None,
        currency="JPY" if is_jp_code(symbol) else "USD",
        as_of=df.index[-1].to_pydatetime(),
    )


class PriceSource(abc.ABC):
    """日足を返すデータソース。"""

    name: str = "base"

    @abc.abstractmethod
    def history(self, symbol: str, days: int = DEFAULT_HISTORY_DAYS) -> pd.DataFrame:
        """直近 days 営業日ぶんの日足を返す。"""

    def quote(self, symbol: str) -> Quote:
        """現在値。既定では日足の末尾から作る。

        あえて短い期間を取りに行かず DEFAULT_HISTORY_DAYS で引くのは、
        分析側と同じキャッシュを共有して結果を一致させるため。
        リアルタイム板を持つソース (kabu ステーション等) は override する。
        """
        return quote_from_history(symbol, self.history(symbol, days=DEFAULT_HISTORY_DAYS))
